"""0032 退款到账关联交易回显「原账单」测试。

退款 status=received 时会生成 income 关联交易
(services/linked_transactions.reconcile_refund_linked_tx),tx sync_id 形如
``<refund_entity_id>::rf-tx``(统一退款)或 ``<refund_entity_id>::rf-leg-N``
(分别退款)。流水里这笔「退款到账」此前只显示金额和「退款到账」备注,看不出
退的是哪笔消费 —— 读端点应按 sync_id 反查退款记录 → 原始交易,回填
``refund_source_label``。

覆盖:
- 统一退款:关联交易带上原单备注
- 原单无备注 / 无分类:回退到分类名、再回退到「金额 · 日期」
- 普通交易(非退款关联)不带该字段
- 分别退款(split)的每条腿都带原单信息
- 跨账本端点(/read/workspace/transactions)同样回填
"""

from __future__ import annotations

from datetime import datetime, timezone

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


def _web_tx(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "happened_at": datetime.now(timezone.utc).isoformat(),
            **body,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _create_refund(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/refunds",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "happened_at": datetime.now(timezone.utc).date().isoformat(),
            **body,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _read_txs(client: TestClient, token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _setup(email: str, ledger_id: str):
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    token = web["access_token"]
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, token


def _refund_linked_txs(txs: list[dict]) -> list[dict]:
    """挑出退款到账生成的关联交易(note 以「退款到账」开头)。"""
    return [t for t in txs if (t.get("note") or "").startswith("退款到账")]


def test_unified_refund_linked_tx_carries_original_note() -> None:
    """统一退款:关联交易的 refund_source_label = 原账单备注。"""
    client, TS, token = _setup("rfsl1@example.com", "L_RFSL1")
    try:
        tx = _web_tx(
            client, token, "L_RFSL1",
            tx_type="expense",
            amount=500.0,
            note="买跑鞋",
            category_name="运动",
            account_id="acc-1",
            account_name="微信",
        )
        _create_refund(
            client, token, "L_RFSL1",
            original_tx_id=tx["entity_id"],
            amount=200.0,
            account_id="acc-1",
            account_name="微信",
            status="received",
            reason="价保退差",
        )

        linked = _refund_linked_txs(_read_txs(client, token, "L_RFSL1"))
        assert len(linked) == 1, linked
        assert linked[0]["refund_source_label"] == "买跑鞋"
    finally:
        app.dependency_overrides.clear()


def test_refund_source_label_falls_back_to_category_name() -> None:
    """原单没有备注时,回退到分类名。"""
    client, TS, token = _setup("rfsl2@example.com", "L_RFSL2")
    try:
        tx = _web_tx(
            client, token, "L_RFSL2",
            tx_type="expense",
            amount=300.0,
            category_name="数码",
            account_id="acc-1",
            account_name="微信",
        )
        _create_refund(
            client, token, "L_RFSL2",
            original_tx_id=tx["entity_id"],
            amount=100.0,
            account_id="acc-1",
            account_name="微信",
            status="received",
        )

        linked = _refund_linked_txs(_read_txs(client, token, "L_RFSL2"))
        assert len(linked) == 1, linked
        assert linked[0]["refund_source_label"] == "数码"
    finally:
        app.dependency_overrides.clear()


def test_refund_source_label_falls_back_to_amount_and_date() -> None:
    """原单既无备注也无分类:回退成「金额 · 日期」,至少认得出是哪笔。"""
    client, TS, token = _setup("rfsl3@example.com", "L_RFSL3")
    try:
        tx = _web_tx(
            client, token, "L_RFSL3",
            tx_type="expense",
            amount=88.0,
            account_id="acc-1",
            account_name="微信",
        )
        _create_refund(
            client, token, "L_RFSL3",
            original_tx_id=tx["entity_id"],
            amount=88.0,
            account_id="acc-1",
            account_name="微信",
            status="received",
        )

        linked = _refund_linked_txs(_read_txs(client, token, "L_RFSL3"))
        assert len(linked) == 1, linked
        label = linked[0]["refund_source_label"]
        assert label and "88" in label
    finally:
        app.dependency_overrides.clear()


def test_plain_tx_has_no_refund_source_label() -> None:
    """普通交易不是退款关联,该字段为 None(不能误报)。"""
    client, TS, token = _setup("rfsl4@example.com", "L_RFSL4")
    try:
        _web_tx(
            client, token, "L_RFSL4",
            tx_type="expense",
            amount=50.0,
            note="午饭",
            account_id="acc-1",
            account_name="微信",
        )
        txs = _read_txs(client, token, "L_RFSL4")
        assert len(txs) == 1, txs
        assert txs[0].get("refund_source_label") is None
    finally:
        app.dependency_overrides.clear()


def test_split_refund_legs_each_carry_original_note() -> None:
    """分别退款:每条腿的关联交易都回显同一张原单。"""
    client, TS, token = _setup("rfsl5@example.com", "L_RFSL5")
    try:
        tx = _web_tx(
            client, token, "L_RFSL5",
            tx_type="expense",
            amount=500.0,
            note="机票",
            account_id="acc-1",
            account_name="微信",
        )
        _create_refund(
            client, token, "L_RFSL5",
            original_tx_id=tx["entity_id"],
            amount=500.0,
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
            ],
            status="received",
        )

        linked = _refund_linked_txs(_read_txs(client, token, "L_RFSL5"))
        assert len(linked) == 2, linked
        assert {t["refund_source_label"] for t in linked} == {"机票"}
    finally:
        app.dependency_overrides.clear()


def test_workspace_transactions_carry_refund_source_label() -> None:
    """跨账本端点同样回填(与账本内端点口径一致)。"""
    client, TS, token = _setup("rfsl6@example.com", "L_RFSL6")
    try:
        tx = _web_tx(
            client, token, "L_RFSL6",
            tx_type="expense",
            amount=420.0,
            note="酒店",
            account_id="acc-1",
            account_name="微信",
        )
        _create_refund(
            client, token, "L_RFSL6",
            original_tx_id=tx["entity_id"],
            amount=420.0,
            account_id="acc-1",
            account_name="微信",
            status="received",
        )

        res = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        # 跨账本端点返回分页对象(账本内端点是裸数组)。
        body = res.json()
        linked = _refund_linked_txs(body["items"] if isinstance(body, dict) else body)
        assert len(linked) == 1, linked
        assert linked[0]["refund_source_label"] == "酒店"
    finally:
        app.dependency_overrides.clear()
