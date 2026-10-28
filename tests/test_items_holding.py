"""归物 · 物品持有(0029):购买 / 成本摊薄 / 保修 / 处置 / 关联交易。

「归物」重新定义为物品持有管理:记录购买日期与金额,按存活时间算日均成本,
保修到期只过保(物品仍存活),只有二手卖出或报废才停止记录。

覆盖范围:
- 创建往返 + 派生态(持有天数 / 净成本 / 日均成本 / 保修状态)
- 保修到期自动过保,但 status 仍是 in_use、天数继续涨(「到期自动过保但仍存活」)
- 交易挂物品:金额计入累计成本,可列出所有关联交易
- 二手卖出:收入抵减成本,持有天数定格在处置日
- 报废:无回收款;改回 in_use 清掉处置字段
- 校验:缺购买日期 / 处置日早于购买日 / 非法状态 → 400
- PATCH 不带字段保留现值
- 物品本身不生成交易、不进余额与收支统计
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), TS


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _latest_change_id(client: TestClient, token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def _setup(email: str, ledger_id: str):
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, web["access_token"]


def _create_item(client: TestClient, token: str, ledger_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "name": "MacBook",
        "purchase_date": "2026-01-01",
        "purchase_amount": 10000.0,
        **body,
    }
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/items",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


def _patch_item(
    client: TestClient, token: str, ledger_id: str, item_id: str, **body
):
    base = _latest_change_id(client, token, ledger_id)
    return client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/items/{item_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )


def _items(client: TestClient, token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/items",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _create_tx(client: TestClient, token: str, ledger_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "tx_type": "expense",
        "amount": 100.0,
        "category_name": "数码",
        "happened_at": datetime.now(timezone.utc).isoformat(),
        **body,
    }
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )


def _item_txs(client: TestClient, token: str, ledger_id: str, item_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/items/{item_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _days(n: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=n)).isoformat()


def _expected_held_days(purchase: str, end: str) -> int:
    d1 = datetime.fromisoformat(purchase).date()
    d2 = datetime.fromisoformat(end).date()
    return (d2 - d1).days + 1


# --------------------------------------------------------------------------- #


def test_create_item_roundtrip_with_derived() -> None:
    """创建往返:购买日/金额/保修落库,派生态按今天现算。"""
    client, TS, token = _setup("it1@example.com", "L_IT1")
    try:
        res = _create_item(
            client, token, "L_IT1", warranty_until=_days(30), note="工作机"
        )
        assert res.status_code == 200, res.text
        item_id = res.json()["entity_id"]

        rows = _items(client, token, "L_IT1")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == item_id
        assert row["name"] == "MacBook"
        assert row["purchase_amount"] == 10000.0
        assert row["purchase_date"].startswith("2026-01-01")
        assert row["status"] == "in_use"
        assert row["is_disposed"] is False
        assert row["note"] == "工作机"

        today = datetime.now(timezone.utc).date().isoformat()
        assert row["held_days"] == _expected_held_days("2026-01-01", today)
        assert row["total_cost"] == 10000.0
        assert row["daily_cost"] == round(
            10000.0 / _expected_held_days("2026-01-01", today), 2
        )
        assert row["warranty_status"] == "active"
        assert row["warranty_days_remaining"] == 30
    finally:
        app.dependency_overrides.clear()


def test_warranty_expired_but_item_still_alive() -> None:
    """保修到期只「过保」:status 仍是 in_use,天数继续涨、成本继续摊。"""
    client, TS, token = _setup("it2@example.com", "L_IT2")
    try:
        res = _create_item(client, token, "L_IT2", warranty_until=_days(-10))
        assert res.status_code == 200, res.text
        row = _items(client, token, "L_IT2")[0]

        assert row["warranty_status"] == "expired"
        assert row["warranty_days_remaining"] == -10
        # 关键:过保 ≠ 处置。物品仍存活,继续累计天数。
        assert row["status"] == "in_use"
        assert row["is_disposed"] is False
        assert row["held_days"] > 0
    finally:
        app.dependency_overrides.clear()


def test_no_warranty_recorded() -> None:
    """没记保修 → warranty_status='none',天数剩余为 None。"""
    client, TS, token = _setup("it3@example.com", "L_IT3")
    try:
        res = _create_item(client, token, "L_IT3")
        assert res.status_code == 200, res.text
        row = _items(client, token, "L_IT3")[0]
        assert row["warranty_status"] == "none"
        assert row["warranty_days_remaining"] is None
        assert row["warranty_until"] is None
    finally:
        app.dependency_overrides.clear()


def test_linked_transactions_add_to_cost_and_are_listable() -> None:
    """交易挂物品:金额计入累计成本,且能列出所有关联交易。"""
    client, TS, token = _setup("it4@example.com", "L_IT4")
    try:
        res = _create_item(client, token, "L_IT4", purchase_amount=1000.0)
        assert res.status_code == 200, res.text
        item_id = res.json()["entity_id"]

        for amount in (200.0, 50.0):
            tx_res = _create_tx(
                client, token, "L_IT4", amount=amount, item_id=item_id
            )
            assert tx_res.status_code == 200, tx_res.text

        row = _items(client, token, "L_IT4")[0]
        assert row["linked_expense"] == 250.0
        assert row["linked_tx_count"] == 2
        assert row["total_cost"] == 1250.0

        txs = _item_txs(client, token, "L_IT4", item_id)
        assert len(txs) == 2
        assert {t["amount"] for t in txs} == {200.0, 50.0}
        # 关联交易端点返回时能带上物品名
        assert all(t["item_id"] == item_id for t in txs)
        assert all(t["item_name"] == "MacBook" for t in txs)
    finally:
        app.dependency_overrides.clear()


def test_sold_disposal_offsets_cost_and_freezes_days() -> None:
    """二手卖出:收入抵减成本(净成本),持有天数定格在处置日。"""
    client, TS, token = _setup("it5@example.com", "L_IT5")
    try:
        res = _create_item(client, token, "L_IT5", purchase_amount=6000.0)
        assert res.status_code == 200, res.text
        item_id = res.json()["entity_id"]
        _create_tx(client, token, "L_IT5", amount=400.0, item_id=item_id)

        disposal = "2026-06-01"
        patch = _patch_item(
            client,
            token,
            "L_IT5",
            item_id,
            status="disposed",
            disposal_type="sold",
            disposal_date=disposal,
            disposal_amount=3000.0,
        )
        assert patch.status_code == 200, patch.text

        row = _items(client, token, "L_IT5")[0]
        assert row["status"] == "disposed"
        assert row["is_disposed"] is True
        assert row["disposal_type"] == "sold"
        assert row["disposal_amount"] == 3000.0
        # 净成本 = 6000 + 400(关联支出) - 3000(卖出收入)
        assert row["total_cost"] == 3400.0
        # 天数定格:处置日 - 购买日 + 1,不再随今天增长
        assert row["held_days"] == _expected_held_days("2026-01-01", disposal)
    finally:
        app.dependency_overrides.clear()


def test_scrapped_has_no_income_and_can_return_to_in_use() -> None:
    """报废无回收款;改回 in_use 会清掉处置字段。"""
    client, TS, token = _setup("it6@example.com", "L_IT6")
    try:
        res = _create_item(client, token, "L_IT6", purchase_amount=500.0)
        assert res.status_code == 200, res.text
        item_id = res.json()["entity_id"]

        patch = _patch_item(
            client, token, "L_IT6", item_id,
            status="disposed", disposal_type="scrapped",
            disposal_date="2026-03-01", disposal_amount=999.0,
        )
        assert patch.status_code == 200, patch.text
        row = _items(client, token, "L_IT6")[0]
        assert row["disposal_type"] == "scrapped"
        # 报废不认回收款(即使传了也归零)
        assert (row["disposal_amount"] or 0.0) == 0.0
        assert row["total_cost"] == 500.0

        back = _patch_item(client, token, "L_IT6", item_id, status="in_use")
        assert back.status_code == 200, back.text
        row = _items(client, token, "L_IT6")[0]
        assert row["status"] == "in_use"
        assert row["disposal_type"] is None
        assert row["disposal_date"] is None
        assert row["disposal_amount"] is None
    finally:
        app.dependency_overrides.clear()


def test_validation_rejects_bad_payloads() -> None:
    """缺购买日期 / 处置日早于购买日 / 非法状态 → 400。"""
    client, TS, token = _setup("it7@example.com", "L_IT7")
    try:
        base = _latest_change_id(client, token, "L_IT7")
        # purchase_date 是 schema 层 required → 422(不是业务 400)
        bad = client.post(
            "/api/v1/write/ledgers/L_IT7/items",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "name": "无日期"},
        )
        assert bad.status_code == 422, bad.text

        # status 是 Literal → schema 层 422
        bad_status = _create_item(client, token, "L_IT7", status="broken")
        assert bad_status.status_code == 422, bad_status.text

        ok = _create_item(client, token, "L_IT7")
        assert ok.status_code == 200, ok.text
        item_id = ok.json()["entity_id"]
        early = _patch_item(
            client, token, "L_IT7", item_id,
            status="disposed", disposal_type="sold",
            disposal_date="2025-01-01", disposal_amount=10.0,
        )
        assert early.status_code == 400, early.text
    finally:
        app.dependency_overrides.clear()


def test_patch_keeps_existing_values_when_field_absent() -> None:
    """PATCH 不带某字段 → 保留现值(不能被清掉)。"""
    client, TS, token = _setup("it8@example.com", "L_IT8")
    try:
        res = _create_item(
            client, token, "L_IT8", purchase_amount=888.0, warranty_until=_days(60)
        )
        assert res.status_code == 200, res.text
        item_id = res.json()["entity_id"]

        patch = _patch_item(client, token, "L_IT8", item_id, note="换了壳")
        assert patch.status_code == 200, patch.text
        row = _items(client, token, "L_IT8")[0]
        assert row["note"] == "换了壳"
        assert row["purchase_amount"] == 888.0
        assert row["warranty_until"] is not None
    finally:
        app.dependency_overrides.clear()


def test_item_does_not_affect_balance_or_stats() -> None:
    """归物只是持有台账:不生成交易、不进余额与收支统计。"""
    client, TS, token = _setup("it9@example.com", "L_IT9")
    try:
        res = _create_item(client, token, "L_IT9", purchase_amount=9999.0)
        assert res.status_code == 200, res.text

        summary = client.get(
            "/api/v1/read/ledgers/L_IT9/stats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert summary.status_code == 200, summary.text
        data = summary.json()
        assert float(data.get("expense_total") or 0.0) == 0.0
        assert float(data.get("balance_all") or data.get("balance") or 0.0) == 0.0

        txs = client.get(
            "/api/v1/read/ledgers/L_IT9/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert txs.status_code == 200, txs.text
        assert txs.json() == []
    finally:
        app.dependency_overrides.clear()
