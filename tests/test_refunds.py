"""0022 交易退款测试(App issue #465 退款功能)。

覆盖三条路径:
- web write/read 端点:CRUD、部分退/多次退、超退拒绝、cancelled 不计累计、
  原单必须是存在且为支出的交易
- mobile /sync/push → projection(mobile payload camelCase + partial-update
  merge 契约,命名对齐 CLAUDE.md:test_mobile_push_<entity>_partial_update_keeps_*)
- 删除路径(web + mobile push)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadRefundProjection, ReadTxProjection


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
    return res


def _read_refunds(client: TestClient, token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/refunds",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _setup(email: str, ledger_id: str):
    """注册 owner + web 登录 + 建账本,返回 (client, TS, token)。"""
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    token = web["access_token"]
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, token


# --------------------------------------------------------------------------- #
# web write/read:CRUD + 部分退/多次退 + 状态流转                                  #
# --------------------------------------------------------------------------- #


def test_refund_crud_and_partial_refunds() -> None:
    client, TS, token = _setup("rf1@example.com", "L_RF1")
    try:
        # 原单:支出 500
        tx = _web_tx(
            client, token, "L_RF1",
            tx_type="expense",
            amount=500.0,
            category_name="数码",
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        # 第一笔退款 200(已到账)
        res = _create_refund(
            client, token, "L_RF1",
            original_tx_id=tx_id,
            amount=200.0,
            account_name="微信",
            status="received",
            reason="价保退差",
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]
        assert refund_id.startswith("rf_")

        items = _read_refunds(client, token, "L_RF1")
        assert len(items) == 1
        r = items[0]
        assert r["original_tx_id"] == tx_id
        assert r["original_amount"] == 500.0  # 服务端从原单快照,不信客户端
        assert r["amount"] == 200.0
        assert r["currency"] == "CNY"
        assert r["status"] == "received"
        assert r["refunded_total"] == pytest.approx(200.0)
        assert r["remaining_refundable"] == pytest.approx(300.0)

        # 第二笔退款 300(pending)—— 累计到原单,remaining 归 0
        res = _create_refund(
            client, token, "L_RF1",
            original_tx_id=tx_id,
            amount=300.0,
            status="pending",
        )
        assert res.status_code == 200, res.text

        items = _read_refunds(client, token, "L_RF1")
        by_id = {i["id"]: i for i in items}
        assert by_id[refund_id]["refunded_total"] == pytest.approx(500.0)
        assert by_id[refund_id]["remaining_refundable"] == pytest.approx(0.0)

        # 状态推进:pending → received
        second_id = [i for i in items if i["id"] != refund_id][0]["id"]
        base = _latest_change_id(client, token, "L_RF1")
        res = client.patch(
            f"/api/v1/write/ledgers/L_RF1/refunds/{second_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "status": "received"},
        )
        assert res.status_code == 200, res.text
        items = _read_refunds(client, token, "L_RF1")
        assert all(i["status"] == "received" for i in items)

        # 删除第二笔 → 累计回落到 200
        base = _latest_change_id(client, token, "L_RF1")
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/L_RF1/refunds/{second_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text
        items = _read_refunds(client, token, "L_RF1")
        assert len(items) == 1
        assert items[0]["refunded_total"] == pytest.approx(200.0)
        assert items[0]["remaining_refundable"] == pytest.approx(300.0)
    finally:
        app.dependency_overrides.clear()


def test_refund_rejects_over_refund_400() -> None:
    client, TS, token = _setup("rf2@example.com", "L_RF2")
    try:
        tx = _web_tx(
            client, token, "L_RF2",
            tx_type="expense",
            amount=100.0,
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        res = _create_refund(client, token, "L_RF2", original_tx_id=tx_id, amount=60.0)
        assert res.status_code == 200, res.text

        # 60 + 50 = 110 > 100 → 拒绝
        res = _create_refund(client, token, "L_RF2", original_tx_id=tx_id, amount=50.0)
        assert res.status_code == 400, res.text
        assert "exceeds original amount" in res.json()["detail"]

        # 恰好退满 100 允许(容差内)
        res = _create_refund(client, token, "L_RF2", original_tx_id=tx_id, amount=40.0)
        assert res.status_code == 200, res.text
    finally:
        app.dependency_overrides.clear()


def test_refund_cancelled_not_counted_in_cumulative() -> None:
    """cancelled 退款不占额度:取消 60 后仍可退满 100。"""
    client, TS, token = _setup("rf3@example.com", "L_RF3")
    try:
        tx = _web_tx(
            client, token, "L_RF3",
            tx_type="expense",
            amount=100.0,
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        res = _create_refund(
            client, token, "L_RF3",
            original_tx_id=tx_id, amount=60.0, status="cancelled",
        )
        assert res.status_code == 200, res.text

        res = _create_refund(client, token, "L_RF3", original_tx_id=tx_id, amount=100.0)
        assert res.status_code == 200, res.text

        items = _read_refunds(client, token, "L_RF3")
        full = [i for i in items if i["amount"] == 100.0][0]
        # cancelled 那笔不计入 → 有效累计就是本笔 100
        assert full["refunded_total"] == pytest.approx(100.0)
        assert full["remaining_refundable"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_refund_requires_existing_expense_tx() -> None:
    client, TS, token = _setup("rf4@example.com", "L_RF4")
    try:
        # 原单不存在 → 400
        res = _create_refund(
            client, token, "L_RF4", original_tx_id="tx_nope", amount=10.0
        )
        assert res.status_code == 400, res.text
        assert "not found" in res.json()["detail"]

        # 原单是 income → 400(退款只对支出有意义)
        inc = _web_tx(
            client, token, "L_RF4",
            tx_type="income",
            amount=50.0,
            account_name="微信",
        )
        res = _create_refund(
            client, token, "L_RF4", original_tx_id=inc["entity_id"], amount=10.0
        )
        assert res.status_code == 400, res.text
        assert "expense" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_refund_update_retargets_original_tx() -> None:
    """update 可改挂靠的原单 + 金额;累计校验按新原单重算。"""
    client, TS, token = _setup("rf5@example.com", "L_RF5")
    try:
        tx_a = _web_tx(
            client, token, "L_RF5", tx_type="expense", amount=100.0, account_name="微信"
        )
        tx_b = _web_tx(
            client, token, "L_RF5", tx_type="expense", amount=200.0, account_name="微信"
        )

        res = _create_refund(
            client, token, "L_RF5",
            original_tx_id=tx_a["entity_id"], amount=100.0,
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        # 改挂到 B(200)并把金额改成 200 —— B 单无其他退款,允许
        base = _latest_change_id(client, token, "L_RF5")
        res = client.patch(
            f"/api/v1/write/ledgers/L_RF5/refunds/{refund_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "original_tx_id": tx_b["entity_id"],
                "amount": 200.0,
            },
        )
        assert res.status_code == 200, res.text

        items = _read_refunds(client, token, "L_RF5")
        assert len(items) == 1
        r = items[0]
        assert r["original_tx_id"] == tx_b["entity_id"]
        assert r["original_amount"] == 200.0
        assert r["amount"] == 200.0
        assert r["remaining_refundable"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_refund_rejects_zero_amount_422() -> None:
    client, TS, token = _setup("rf6@example.com", "L_RF6")
    try:
        tx = _web_tx(
            client, token, "L_RF6", tx_type="expense", amount=100.0, account_name="微信"
        )
        res = _create_refund(
            client, token, "L_RF6", original_tx_id=tx["entity_id"], amount=0
        )
        assert res.status_code == 422, res.text  # pydantic gt=0
    finally:
        app.dependency_overrides.clear()


def test_refund_delete_missing_returns_404() -> None:
    client, TS, token = _setup("rf7@example.com", "L_RF7")
    try:
        base = _latest_change_id(client, token, "L_RF7")
        res = client.request(
            "DELETE",
            "/api/v1/write/ledgers/L_RF7/refunds/rf_nope",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# mobile /sync/push:payload 落库 + partial-update merge 契约                    #
# --------------------------------------------------------------------------- #


def _push_change(
    client: TestClient,
    token: str,
    device_id: str,
    ledger_id: str,
    entity_type: str,
    entity_sync_id: str,
    payload: dict,
    action: str = "upsert",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": entity_type,
                    "entity_sync_id": entity_sync_id,
                    "action": action,
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _row(TS, model, external_ledger_id: str, sync_id: str):
    with TS() as db:
        internal_id = db.scalar(
            select(Ledger.id).where(Ledger.external_id == external_ledger_id)
        )
        assert internal_id is not None
        row = db.scalar(
            select(model).where(
                model.ledger_id == internal_id,
                model.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def test_mobile_push_refund_partial_update_keeps_existing_fields() -> None:
    """mobile push 退款 → 落库;partial update(只改 status)保留其余字段。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "rf8@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_RF8")

        # 原单也走 mobile push(expense),保证关联目标存在
        _push_change(
            client, token, device, "L_RF8", "transaction", "tx-1",
            {
                "syncId": "tx-1",
                "type": "expense",
                "amount": 300.0,
                "happenedAt": "2026-10-01T00:00:00+00:00",
                "categoryName": "服饰",
                "accountId": "acc-1",
                "accountName": "支付宝",
            },
        )
        _push_change(
            client, token, device, "L_RF8", "refund", "rf-1",
            {
                "syncId": "rf-1",
                "originalTxId": "tx-1",
                "originalAmount": 300.0,
                "amount": 120.0,
                "currency": "CNY",
                "accountId": "acc-1",
                "accountName": "支付宝",
                "happenedAt": "2026-10-10T00:00:00+00:00",
                "reason": "尺码不合适",
                "status": "pending",
                "note": "寄回中",
            },
        )
        row = _row(TS, ReadRefundProjection, "L_RF8", "rf-1")
        assert row.original_tx_id == "tx-1"
        assert row.amount == 120.0
        assert row.account_name == "支付宝"
        assert row.reason == "尺码不合适"

        # partial update:只改 status,不带 accountName/reason/amount 等
        _push_change(
            client, token, device, "L_RF8", "refund", "rf-1",
            {
                "syncId": "rf-1",
                "originalTxId": "tx-1",
                "amount": 120.0,
                "happenedAt": "2026-10-10T00:00:00+00:00",
                "status": "received",
            },
        )
        row = _row(TS, ReadRefundProjection, "L_RF8", "rf-1")
        assert row.status == "received"
        # merge 契约:缺键保留既有值,不被默认值覆盖
        assert row.account_name == "支付宝"
        assert row.account_sync_id == "acc-1"
        assert row.reason == "尺码不合适"
        assert row.note == "寄回中"
        assert row.amount == 120.0
        assert row.original_amount == 300.0
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_refund_delete_removes_projection() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "rf9@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_RF9")
        _push_change(
            client, token, device, "L_RF9", "refund", "rf-del-1",
            {
                "syncId": "rf-del-1",
                "originalTxId": "tx-1",
                "amount": 30.0,
                "happenedAt": "2026-10-05T00:00:00+00:00",
                "status": "pending",
            },
        )
        _row(TS, ReadRefundProjection, "L_RF9", "rf-del-1")

        _push_change(
            client, token, device, "L_RF9", "refund", "rf-del-1",
            {}, action="delete",
        )
        with TS() as db:
            internal_id = db.scalar(
                select(Ledger.id).where(Ledger.external_id == "L_RF9")
            )
            row = db.scalar(
                select(ReadRefundProjection).where(
                    ReadRefundProjection.ledger_id == internal_id,
                    ReadRefundProjection.sync_id == "rf-del-1",
                )
            )
            assert row is None
    finally:
        app.dependency_overrides.clear()


def test_refund_read_ignores_unknown_status_filter() -> None:
    """status 过滤传非法值时退回全量(跟 time-deposits 同款宽松语义)。"""
    client, TS, token = _setup("rf10@example.com", "L_RF10")
    try:
        tx = _web_tx(
            client, token, "L_RF10", tx_type="expense", amount=80.0, account_name="微信"
        )
        assert _create_refund(
            client, token, "L_RF10", original_tx_id=tx["entity_id"], amount=80.0
        ).status_code == 200
        res = client.get(
            "/api/v1/read/ledgers/L_RF10/refunds?status=bogus",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        assert len(res.json()) == 0  # 过滤不命中 → 空列表
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 0025 退款分摊(分别退 / 统一退)+ 实际入账改余额                                  #
# --------------------------------------------------------------------------- #


def _push_account(client: TestClient, token: str, device_id: str, payload: dict, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "account",
                    "entity_sync_id": payload.get("syncId"),
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _account_balance_by_name(client: TestClient, token: str, ledger_id: str, name: str) -> float:
    res = client.get(
        f"/api/v1/read/workspace/accounts?ledger_id={ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    hit = [a for a in rows if a["name"] == name]
    assert hit, f"账户 {name} 不在返回列表: {rows}"
    return float(hit[0]["balance"])


def _refund_linked_txs(TS, refund_sync_id: str) -> list[dict]:
    db = TS()
    try:
        rows = db.execute(
            select(ReadTxProjection).where(
                ReadTxProjection.sync_id.like(f"{refund_sync_id}::rf-%")
            )
        ).scalars().all()
        return [
            {
                "sync_id": r.sync_id,
                "tx_type": r.tx_type,
                "amount": float(r.amount),
                "account_sync_id": r.account_sync_id,
                "exclude_from_stats": r.exclude_from_stats,
            }
            for r in rows
        ]
    finally:
        db.close()


def _setup_two_accounts(email: str, ledger_id: str):
    """注册 owner + web 登录 + 建账本 + 两张卡(acc-1 微信 / acc-2 支付宝)。"""
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    token = web["access_token"]
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    _push_account(
        client, owner["access_token"], owner["device_id"],
        {"syncId": "acc-1", "name": "微信", "type": "card", "currency": "CNY", "initialBalance": 0},
        ledger_id,
    )
    _push_account(
        client, owner["access_token"], owner["device_id"],
        {"syncId": "acc-2", "name": "支付宝", "type": "card", "currency": "CNY", "initialBalance": 0},
        ledger_id,
    )
    return client, TS, token


def test_refund_split_links_income_to_each_leg_account() -> None:
    """分别退款(0025):原始两账户分摊支出,分别退款 → 各张卡各回一笔 income,
    且账户余额真实变动;关联交易打 excludeFromStats。"""
    client, TS, token = _setup_two_accounts("rf_split1@example.com", "L_RFS1")
    try:
        # 原单:支出 500,分两张卡付(acc-1 300 / acc-2 200)
        tx = _web_tx(
            client, token, "L_RFS1",
            tx_type="expense",
            amount=500.0,
            category_name="数码",
            payment_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
            ],
        )
        tx_id = tx["entity_id"]
        # 支出已分别扣款:微信 -300、支付宝 -200
        assert _account_balance_by_name(client, token, "L_RFS1", "微信") == -300.0
        assert _account_balance_by_name(client, token, "L_RFS1", "支付宝") == -200.0

        # 分别退款 500(已到账):acc-1 回 300、acc-2 回 200
        res = _create_refund(
            client, token, "L_RFS1",
            original_tx_id=tx_id,
            amount=500.0,
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
            ],
            status="received",
            reason="整单退",
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        # 读回:refund_mode / refund_legs 正确透传
        items = _read_refunds(client, token, "L_RFS1")
        r = [i for i in items if i["id"] == refund_id][0]
        assert r["refund_mode"] == "split"
        assert r["refund_legs"] == [
            {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
            {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
        ]

        # 各张卡余额回正:微信 0、支付宝 0
        assert _account_balance_by_name(client, token, "L_RFS1", "微信") == pytest.approx(0.0)
        assert _account_balance_by_name(client, token, "L_RFS1", "支付宝") == pytest.approx(0.0)

        # 生成两条分别退款关联交易(income),excludeFromStats
        linked = _refund_linked_txs(TS, refund_id)
        assert len(linked) == 2
        by_acc = {l["account_sync_id"]: l for l in linked}
        assert by_acc["acc-1"]["tx_type"] == "income"
        assert by_acc["acc-1"]["amount"] == 300.0
        assert by_acc["acc-2"]["tx_type"] == "income"
        assert by_acc["acc-2"]["amount"] == 200.0
        assert all(l["exclude_from_stats"] is True for l in linked)
    finally:
        app.dependency_overrides.clear()


def test_refund_unified_links_income_to_single_account() -> None:
    """统一退款(0025):全额退到单一卡 → 该卡 +全额,另一张卡不动。"""
    client, TS, token = _setup_two_accounts("rf_uni1@example.com", "L_RFU1")
    try:
        tx = _web_tx(
            client, token, "L_RFU1",
            tx_type="expense",
            amount=500.0,
            category_name="数码",
            account_name="微信",
        )
        tx_id = tx["entity_id"]
        assert _account_balance_by_name(client, token, "L_RFU1", "微信") == -500.0

        # 统一退款 500 到 acc-2(支付宝),已到账
        res = _create_refund(
            client, token, "L_RFU1",
            original_tx_id=tx_id,
            amount=500.0,
            refund_mode="unified",
            account_id="acc-2",
            account_name="支付宝",
            status="received",
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        items = _read_refunds(client, token, "L_RFU1")
        r = [i for i in items if i["id"] == refund_id][0]
        assert r["refund_mode"] == "unified"
        assert r["refund_legs"] is None

        # 支付宝 +500,微信仍 -500
        assert _account_balance_by_name(client, token, "L_RFU1", "支付宝") == pytest.approx(500.0)
        assert _account_balance_by_name(client, token, "L_RFU1", "微信") == pytest.approx(-500.0)

        linked = _refund_linked_txs(TS, refund_id)
        assert len(linked) == 1
        assert linked[0]["tx_type"] == "income"
        assert linked[0]["amount"] == 500.0
        assert linked[0]["account_sync_id"] == "acc-2"
    finally:
        app.dependency_overrides.clear()


def test_refund_split_validation_rejects_mismatched_legs() -> None:
    """分别退款但 legs 合计 != 退款金额 → 400。"""
    client, TS, token = _setup_two_accounts("rf_val1@example.com", "L_RFV1")
    try:
        tx = _web_tx(
            client, token, "L_RFV1", tx_type="expense", amount=500.0, account_name="微信"
        )
        tx_id = tx["entity_id"]
        res = _create_refund(
            client, token, "L_RFV1",
            original_tx_id=tx_id,
            amount=500.0,
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 100.0},  # 合计 400 != 500
            ],
            status="received",
        )
        assert res.status_code == 400, res.text
        assert "sum(refundLegs)" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_refund_split_validation_requires_legs() -> None:
    """分别退款但没给 legs → 400。"""
    client, TS, token = _setup_two_accounts("rf_val2@example.com", "L_RFV2")
    try:
        tx = _web_tx(
            client, token, "L_RFV2", tx_type="expense", amount=500.0, account_name="微信"
        )
        tx_id = tx["entity_id"]
        res = _create_refund(
            client, token, "L_RFV2",
            original_tx_id=tx_id,
            amount=500.0,
            refund_mode="split",
            refund_legs=[],
            status="received",
        )
        assert res.status_code == 400, res.text
        assert "refundLegs" in res.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_refund_pending_or_cancelled_no_linked_tx() -> None:
    """pending / cancelled 不改动余额、不产生关联交易。"""
    client, TS, token = _setup_two_accounts("rf_pc1@example.com", "L_RFPC1")
    try:
        tx = _web_tx(
            client, token, "L_RFPC1", tx_type="expense", amount=500.0, account_name="微信"
        )
        tx_id = tx["entity_id"]
        # pending
        res = _create_refund(
            client, token, "L_RFPC1",
            original_tx_id=tx_id, amount=500.0, status="pending",
            account_id="acc-1", account_name="微信",
        )
        assert res.status_code == 200, res.text
        rid = res.json()["entity_id"]
        assert _refund_linked_txs(TS, rid) == []
        assert _account_balance_by_name(client, token, "L_RFPC1", "微信") == pytest.approx(-500.0)

        # 推进到 received → 产生关联交易,余额回正
        base = _latest_change_id(client, token, "L_RFPC1")
        res = client.patch(
            f"/api/v1/write/ledgers/L_RFPC1/refunds/{rid}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "status": "received"},
        )
        assert res.status_code == 200, res.text
        assert len(_refund_linked_txs(TS, rid)) == 1
        assert _account_balance_by_name(client, token, "L_RFPC1", "微信") == pytest.approx(0.0)

        # 取消 → 清理关联交易,余额回到 -500
        base = _latest_change_id(client, token, "L_RFPC1")
        res = client.patch(
            f"/api/v1/write/ledgers/L_RFPC1/refunds/{rid}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "status": "cancelled"},
        )
        assert res.status_code == 200, res.text
        assert _refund_linked_txs(TS, rid) == []
        assert _account_balance_by_name(client, token, "L_RFPC1", "微信") == pytest.approx(-500.0)
    finally:
        app.dependency_overrides.clear()


def test_refund_delete_cleans_linked_tx() -> None:
    """删除已到账退款 → 关联交易被清理,余额恢复。"""
    client, TS, token = _setup_two_accounts("rf_del1@example.com", "L_RFD1")
    try:
        tx = _web_tx(
            client, token, "L_RFD1", tx_type="expense", amount=500.0, account_name="微信"
        )
        tx_id = tx["entity_id"]
        res = _create_refund(
            client, token, "L_RFD1",
            original_tx_id=tx_id, amount=500.0, status="received",
            account_id="acc-1", account_name="微信",
        )
        assert res.status_code == 200, res.text
        rid = res.json()["entity_id"]
        assert len(_refund_linked_txs(TS, rid)) == 1
        assert _account_balance_by_name(client, token, "L_RFD1", "微信") == pytest.approx(0.0)

        base = _latest_change_id(client, token, "L_RFD1")
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/L_RFD1/refunds/{rid}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text
        assert _refund_linked_txs(TS, rid) == []
        assert _account_balance_by_name(client, token, "L_RFD1", "微信") == pytest.approx(-500.0)
    finally:
        app.dependency_overrides.clear()


def test_refund_split_to_unified_switch_cleans_orphan_legs() -> None:
    """split(两笔腿)→ 改 unified:孤儿腿关联交易被清理,只留统一退那笔。"""
    client, TS, token = _setup_two_accounts("rf_sw1@example.com", "L_RFSW1")
    try:
        tx = _web_tx(
            client, token, "L_RFSW1", tx_type="expense", amount=500.0, account_name="微信"
        )
        tx_id = tx["entity_id"]
        res = _create_refund(
            client, token, "L_RFSW1",
            original_tx_id=tx_id, amount=500.0, status="received",
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
            ],
        )
        assert res.status_code == 200, res.text
        rid = res.json()["entity_id"]
        assert len(_refund_linked_txs(TS, rid)) == 2

        base = _latest_change_id(client, token, "L_RFSW1")
        res = client.patch(
            f"/api/v1/write/ledgers/L_RFSW1/refunds/{rid}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "refund_mode": "unified",
                "refund_legs": None,
                "account_id": "acc-1",
                "account_name": "微信",
            },
        )
        assert res.status_code == 200, res.text
        linked = _refund_linked_txs(TS, rid)
        assert len(linked) == 1
        assert linked[0]["account_sync_id"] == "acc-1"
        assert linked[0]["amount"] == 500.0
    finally:
        app.dependency_overrides.clear()



def test_read_txs_include_refunded_total() -> None:
    """0022 流水列表 refunded_total:读交易端点按原单聚合有效退款(不含
    cancelled),前端据此给全额退款的交易金额画删除线。"""
    client, TS, token = _setup("rf_strike@example.com", "L_RF_STRIKE")
    try:
        tx = _web_tx(
            client, token, "L_RF_STRIKE",
            tx_type="expense",
            amount=100.0,
            category_name="数码",
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        def _tx_row() -> dict:
            res = client.get(
                "/api/v1/read/ledgers/L_RF_STRIKE/transactions",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 200, res.text
            return next(t for t in res.json() if t["id"] == tx_id)

        # 无退款 → 0
        assert _tx_row()["refunded_total"] == pytest.approx(0.0)

        # 部分退 40 → 40
        res = _create_refund(
            client, token, "L_RF_STRIKE",
            original_tx_id=tx_id,
            amount=40.0,
            account_name="微信",
            status="received",
        )
        assert res.status_code == 200, res.text
        assert _tx_row()["refunded_total"] == pytest.approx(40.0)

        # 全额退(第二笔 60)→ 100
        res = _create_refund(
            client, token, "L_RF_STRIKE",
            original_tx_id=tx_id,
            amount=60.0,
            account_name="微信",
            status="received",
        )
        assert res.status_code == 200, res.text
        row = _tx_row()
        assert row["refunded_total"] == pytest.approx(100.0)
        assert row["refunded_total"] >= row["amount"] - 0.011  # 前端删除线口径

        # 取消第二笔 → 回落到 40
        base = _latest_change_id(client, token, "L_RF_STRIKE")
        items = _read_refunds(client, token, "L_RF_STRIKE")
        second_id = [i for i in items if i["amount"] == 60.0][0]["id"]
        res = client.patch(
            f"/api/v1/write/ledgers/L_RF_STRIKE/refunds/{second_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "status": "cancelled"},
        )
        assert res.status_code == 200, res.text
        assert _tx_row()["refunded_total"] == pytest.approx(40.0)
    finally:
        app.dependency_overrides.clear()


def test_workspace_txs_include_refunded_total() -> None:
    """workspace/transactions(流水列表实际数据源)同样带 refunded_total。"""
    client, TS, token = _setup("rf_strike2@example.com", "L_RF_STRIKE2")
    try:
        tx = _web_tx(
            client, token, "L_RF_STRIKE2",
            tx_type="expense",
            amount=80.0,
            category_name="数码",
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        res = _create_refund(
            client, token, "L_RF_STRIKE2",
            original_tx_id=tx_id,
            amount=80.0,
            account_name="微信",
            status="received",
        )
        assert res.status_code == 200, res.text

        res = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        row = next(t for t in res.json()["items"] if t["id"] == tx_id)
        assert row["refunded_total"] == pytest.approx(80.0)
    finally:
        app.dependency_overrides.clear()


def test_read_txs_refunded_to_account_ignores_other_cards() -> None:
    """0022 卡维度 refunded_to_account:跨卡退款不计入「已退款」。

    钱退到别的卡时,原支出卡的支出仍然成立(余额也没回来),流水不能画
    删除线 —— 与卡的角度(账户余额)口径一致;退回到本卡的部分才计入。
    """
    client, TS, token = _setup_two_accounts("rf_card1@example.com", "L_RFC1")
    try:
        # 原单:支出 100 在 acc-1(微信)
        tx = _web_tx(
            client, token, "L_RFC1",
            tx_type="expense",
            amount=100.0,
            category_name="数码",
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        def _tx_row() -> dict:
            res = client.get(
                "/api/v1/read/ledgers/L_RFC1/transactions",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status_code == 200, res.text
            return next(t for t in res.json() if t["id"] == tx_id)

        # ① 统一退款 100 到另一张卡(acc-2):总口径 100,卡维度 0 → 不画删除线
        res = _create_refund(
            client, token, "L_RFC1",
            original_tx_id=tx_id,
            amount=100.0,
            refund_mode="unified",
            account_id="acc-2",
            account_name="支付宝",
            status="received",
        )
        assert res.status_code == 200, res.text
        row = _tx_row()
        assert row["refunded_total"] == pytest.approx(100.0)
        assert row["refunded_to_account"] == pytest.approx(0.0)
        assert row["refunded_to_account"] < row["amount"] - 0.011  # 前端不画线
        # 余额佐证:微信仍 -100(支出成立),支付宝 +100(钱到了别卡)
        assert _account_balance_by_name(client, token, "L_RFC1", "微信") == pytest.approx(-100.0)
        assert _account_balance_by_name(client, token, "L_RFC1", "支付宝") == pytest.approx(100.0)

        # ② 取消上一笔,改分别退款 100:30 回本卡 + 70 去别卡
        base = _latest_change_id(client, token, "L_RFC1")
        items = _read_refunds(client, token, "L_RFC1")
        first_id = [i for i in items if i["amount"] == 100.0][0]["id"]
        res = client.patch(
            f"/api/v1/write/ledgers/L_RFC1/refunds/{first_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "status": "cancelled"},
        )
        assert res.status_code == 200, res.text
        row = _tx_row()
        assert row["refunded_total"] == pytest.approx(0.0)
        assert row["refunded_to_account"] == pytest.approx(0.0)

        res = _create_refund(
            client, token, "L_RFC1",
            original_tx_id=tx_id,
            amount=100.0,
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 30.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 70.0},
            ],
            status="received",
        )
        assert res.status_code == 200, res.text
        row = _tx_row()
        # 只有本卡那 30 计入卡维度;总口径仍是 100
        assert row["refunded_total"] == pytest.approx(100.0)
        assert row["refunded_to_account"] == pytest.approx(30.0)
        assert row["refunded_to_account"] < row["amount"] - 0.011  # 仍不画线
        # 余额佐证:微信 -100+30=-70、支付宝 +70
        assert _account_balance_by_name(client, token, "L_RFC1", "微信") == pytest.approx(-70.0)
        assert _account_balance_by_name(client, token, "L_RFC1", "支付宝") == pytest.approx(70.0)
    finally:
        app.dependency_overrides.clear()


def test_workspace_txs_refunded_to_account_ignores_other_cards() -> None:
    """workspace/transactions(流水列表实际数据源)同样带卡维度 refunded_to_account。"""
    client, TS, token = _setup_two_accounts("rf_card2@example.com", "L_RFC2")
    try:
        tx = _web_tx(
            client, token, "L_RFC2",
            tx_type="expense",
            amount=60.0,
            category_name="数码",
            account_name="微信",
        )
        tx_id = tx["entity_id"]

        # 分别退款 60:40 回本卡(acc-1)+ 20 去别卡(acc-2)
        res = _create_refund(
            client, token, "L_RFC2",
            original_tx_id=tx_id,
            amount=60.0,
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 40.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 20.0},
            ],
            status="received",
        )
        assert res.status_code == 200, res.text

        res = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        row = next(t for t in res.json()["items"] if t["id"] == tx_id)
        assert row["refunded_total"] == pytest.approx(60.0)
        assert row["refunded_to_account"] == pytest.approx(40.0)
    finally:
        app.dependency_overrides.clear()
