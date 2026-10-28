"""0021 五大功能测试:优惠(折扣)/ 转账手续费 / 多账户合并付款 / 定期存款 / 归物。

覆盖三条路径:
- web write/read 端点(折扣/手续费/legs 的 round-trip + 校验 + 类型归属清理)
- mobile /sync/push → projection(mobile payload camelCase + partial-update merge 契约)
- 定期存款/归物的 CRUD + 派生字段 + sync 契约

合并契约测试命名对齐 CLAUDE.md 要求:
test_mobile_push_<entity>_partial_update_keeps_existing_fields
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
from src.models import (
    Ledger,
    ReadItemReturnProjection,
    ReadTimeDepositProjection,
    ReadTxProjection,
)


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
        json={"base_change_id": base, **body},
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


# --------------------------------------------------------------------------- #
# 优惠(折扣)                                                                    #
# --------------------------------------------------------------------------- #


def test_web_create_expense_with_discount_roundtrips() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "disc1@example.com")
        web = _login_web(client, "disc1@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_DISC1")

        _web_tx(
            client, token, "L_DISC1",
            tx_type="expense",
            amount=100.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            category_name="餐饮",
            account_name="微信",
            discount={
                "mode": "amount",
                "value": 20.0,
                "couponName": "满100减20",
                "originalAmount": 120.0,
                "discountAmount": 20.0,
                "finalAmount": 100.0,
            },
        )
        txs = _read_txs(client, token, "L_DISC1")
        assert len(txs) == 1
        d = txs[0]["discount"]
        assert d is not None
        assert d["mode"] == "amount"
        assert d["couponName"] == "满100减20"
        assert d["originalAmount"] == 120.0
        assert d["finalAmount"] == 100.0
    finally:
        app.dependency_overrides.clear()


def test_web_update_discount_and_clear() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "disc2@example.com")
        web = _login_web(client, "disc2@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_DISC2")

        created = _web_tx(
            client, token, "L_DISC2",
            tx_type="expense",
            amount=100.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            account_name="微信",
            discount={"mode": "percent", "value": 0.85, "finalAmount": 100.0},
        )
        tx_id = created["entity_id"]

        # 改成满减券
        base = _latest_change_id(client, token, "L_DISC2")
        res = client.patch(
            f"/api/v1/write/ledgers/L_DISC2/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "discount": {"mode": "amount", "value": 20.0, "couponName": "券"},
            },
        )
        assert res.status_code == 200, res.text
        d = _read_txs(client, token, "L_DISC2")[0]["discount"]
        assert d["mode"] == "amount"
        assert d["couponName"] == "券"

        # 显式清除
        base = _latest_change_id(client, token, "L_DISC2")
        res = client.patch(
            f"/api/v1/write/ledgers/L_DISC2/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "discount": None},
        )
        assert res.status_code == 200, res.text
        assert _read_txs(client, token, "L_DISC2")[0]["discount"] is None
    finally:
        app.dependency_overrides.clear()


def test_discount_cleared_when_type_changes_to_income() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "disc3@example.com")
        web = _login_web(client, "disc3@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_DISC3")

        created = _web_tx(
            client, token, "L_DISC3",
            tx_type="expense",
            amount=100.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            account_name="微信",
            discount={"mode": "percent", "value": 0.9},
        )
        tx_id = created["entity_id"]

        base = _latest_change_id(client, token, "L_DISC3")
        res = client.patch(
            f"/api/v1/write/ledgers/L_DISC3/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "tx_type": "income"},
        )
        assert res.status_code == 200, res.text
        # 类型变 income → discount 归一化清理
        assert _read_txs(client, token, "L_DISC3")[0]["discount"] is None
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_discount_persists_and_partial_update_keeps_fields() -> None:
    """mobile push 折扣 → 落库;partial update(只改金额)保留折扣(merge 契约)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "disc4@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_DISC4")
        _push_change(
            client, token, device, "L_DISC4", "transaction", "tx-disc-1",
            {
                "syncId": "tx-disc-1",
                "type": "expense",
                "amount": 85.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "discount": {
                    "mode": "percent",
                    "value": 0.85,
                    "originalAmount": 100.0,
                    "discountAmount": 15.0,
                    "finalAmount": 85.0,
                },
            },
        )
        row = _row(TS, ReadTxProjection, "L_DISC4", "tx-disc-1")
        import json
        assert json.loads(row.discount_json)["value"] == 0.85

        # partial update:只改备注,不带 discount 键
        _push_change(
            client, token, device, "L_DISC4", "transaction", "tx-disc-1",
            {
                "syncId": "tx-disc-1",
                "type": "expense",
                "amount": 85.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "note": "改备注",
            },
        )
        row = _row(TS, ReadTxProjection, "L_DISC4", "tx-disc-1")
        assert row.note == "改备注"
        assert json.loads(row.discount_json)["value"] == 0.85
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 转账手续费                                                                    #
# --------------------------------------------------------------------------- #


def test_web_create_transfer_with_fee_roundtrips() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "fee1@example.com")
        web = _login_web(client, "fee1@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_FEE1")

        _web_tx(
            client, token, "L_FEE1",
            tx_type="transfer",
            amount=1000.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            from_account_name="招行",
            to_account_name="支付宝",
            transfer_fee={
                "amount": 5.0,
                "mode": "deduct_from_source",
            },
        )
        txs = _read_txs(client, token, "L_FEE1")
        assert len(txs) == 1
        fee = txs[0]["transfer_fee"]
        assert fee is not None
        assert fee["amount"] == 5.0
        assert fee["mode"] == "deduct_from_source"
    finally:
        app.dependency_overrides.clear()


def test_web_create_transfer_with_discount_roundtrips() -> None:
    """0026 优惠模式:transfer_fee.mode='discount'(转出方少扣、到账方全收)
    round-trip。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fee_disc@example.com")
        web = _login_web(client, "fee_disc@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_FEE_DISC")

        _web_tx(
            client, token, "L_FEE_DISC",
            tx_type="transfer",
            amount=50.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            from_account_name="A卡",
            to_account_name="B卡",
            transfer_fee={"amount": 20.0, "mode": "discount"},
        )
        txs = _read_txs(client, token, "L_FEE_DISC")
        assert len(txs) == 1
        fee = txs[0]["transfer_fee"]
        assert fee is not None
        assert fee["amount"] == 20.0
        assert fee["mode"] == "discount"
    finally:
        app.dependency_overrides.clear()


def test_transfer_discount_rejects_oversize() -> None:
    """0026:优惠额 >= 转账金额 → 400(实扣会 <= 0,数据不合法)。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fee_disc2@example.com")
        web = _login_web(client, "fee_disc2@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_FEE_DISC2")

        base = _latest_change_id(client, token, "L_FEE_DISC2")
        res = client.post(
            "/api/v1/write/ledgers/L_FEE_DISC2/transactions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 20.0,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "from_account_name": "A卡",
                "to_account_name": "B卡",
                "transfer_fee": {"amount": 20.0, "mode": "discount"},
            },
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_transfer_fee_rejects_unknown_mode() -> None:
    """未知 mode → 400。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "fee_disc3@example.com")
        web = _login_web(client, "fee_disc3@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_FEE_DISC3")

        base = _latest_change_id(client, token, "L_FEE_DISC3")
        res = client.post(
            "/api/v1/write/ledgers/L_FEE_DISC3/transactions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "tx_type": "transfer",
                "amount": 20.0,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "from_account_name": "A卡",
                "to_account_name": "B卡",
                "transfer_fee": {"amount": 2.0, "mode": "mystery"},
            },
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_transfer_fee_cleared_when_type_changes_to_expense() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "fee2@example.com")
        web = _login_web(client, "fee2@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_FEE2")

        created = _web_tx(
            client, token, "L_FEE2",
            tx_type="transfer",
            amount=1000.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            from_account_name="招行",
            to_account_name="支付宝",
            transfer_fee={"amount": 5.0, "mode": "deduct_from_source"},
        )
        tx_id = created["entity_id"]

        base = _latest_change_id(client, token, "L_FEE2")
        res = client.patch(
            f"/api/v1/write/ledgers/L_FEE2/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "tx_type": "expense"},
        )
        assert res.status_code == 200, res.text
        assert _read_txs(client, token, "L_FEE2")[0]["transfer_fee"] is None
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_transfer_fee_partial_update_keeps_fields() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "fee3@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_FEE3")
        _push_change(
            client, token, device, "L_FEE3", "transaction", "tx-fee-1",
            {
                "syncId": "tx-fee-1",
                "type": "transfer",
                "amount": 1000.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "fromAccountName": "招行",
                "toAccountName": "支付宝",
                "transferFee": {"amount": 5.0, "mode": "deduct_from_target"},
            },
        )
        # partial update:只改金额
        _push_change(
            client, token, device, "L_FEE3", "transaction", "tx-fee-1",
            {
                "syncId": "tx-fee-1",
                "type": "transfer",
                "amount": 2000.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "fromAccountName": "招行",
                "toAccountName": "支付宝",
            },
        )
        row = _row(TS, ReadTxProjection, "L_FEE3", "tx-fee-1")
        import json
        assert row.amount == 2000.0
        fee = json.loads(row.transfer_fee_json)
        assert fee["amount"] == 5.0
        assert fee["mode"] == "deduct_from_target"
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 多账户合并付款                                                                 #
# --------------------------------------------------------------------------- #


def test_web_create_expense_with_payment_legs_roundtrips() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "legs1@example.com")
        web = _login_web(client, "legs1@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_LEGS1")

        _web_tx(
            client, token, "L_LEGS1",
            tx_type="expense",
            amount=100.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            account_name="支付宝",
            payment_legs=[
                {"accountId": "acc-1", "accountName": "支付宝", "amount": 60.0},
                {"accountId": "acc-2", "accountName": "微信", "amount": 40.0},
            ],
        )
        txs = _read_txs(client, token, "L_LEGS1")
        legs = txs[0]["payment_legs"]
        assert legs is not None
        assert len(legs) == 2
        assert sum(leg["amount"] for leg in legs) == 100.0
    finally:
        app.dependency_overrides.clear()


def test_payment_legs_total_mismatch_rejected_400() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "legs2@example.com")
        web = _login_web(client, "legs2@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_LEGS2")

        base = _latest_change_id(client, token, "L_LEGS2")
        res = client.post(
            "/api/v1/write/ledgers/L_LEGS2/transactions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 100.0,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "payment_legs": [
                    {"accountId": "acc-1", "accountName": "支付宝", "amount": 60.0},
                ],
            },
        )
        assert res.status_code == 400, res.text
        assert "payment legs total" in res.text
    finally:
        app.dependency_overrides.clear()


def test_payment_legs_rescale_on_amount_change() -> None:
    """只改金额(不重发 legs)→ 各腿按比例缩放,sum(legs) == amount 不变式保持。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "legs3@example.com")
        web = _login_web(client, "legs3@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_LEGS3")

        created = _web_tx(
            client, token, "L_LEGS3",
            tx_type="expense",
            amount=100.0,
            happened_at=datetime.now(timezone.utc).isoformat(),
            payment_legs=[
                {"accountId": "acc-1", "accountName": "支付宝", "amount": 60.0},
                {"accountId": "acc-2", "accountName": "微信", "amount": 40.0},
            ],
        )
        tx_id = created["entity_id"]

        base = _latest_change_id(client, token, "L_LEGS3")
        res = client.patch(
            f"/api/v1/write/ledgers/L_LEGS3/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "amount": 200.0},
        )
        assert res.status_code == 200, res.text
        legs = _read_txs(client, token, "L_LEGS3")[0]["payment_legs"]
        assert sum(leg["amount"] for leg in legs) == pytest.approx(200.0)
        assert legs[0]["amount"] == pytest.approx(120.0)
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_payment_legs_partial_update_keeps_fields() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "legs4@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_LEGS4")
        _push_change(
            client, token, device, "L_LEGS4", "transaction", "tx-legs-1",
            {
                "syncId": "tx-legs-1",
                "type": "expense",
                "amount": 100.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "paymentLegs": [
                    {"accountId": "acc-1", "accountName": "支付宝", "amount": 60.0},
                    {"accountId": "acc-2", "accountName": "微信", "amount": 40.0},
                ],
            },
        )
        _push_change(
            client, token, device, "L_LEGS4", "transaction", "tx-legs-1",
            {
                "syncId": "tx-legs-1",
                "type": "expense",
                "amount": 100.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "note": "聚餐",
            },
        )
        row = _row(TS, ReadTxProjection, "L_LEGS4", "tx-legs-1")
        import json
        legs = json.loads(row.payment_legs_json)
        assert len(legs) == 2
        assert sum(leg["amount"] for leg in legs) == 100.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 定期存款                                                                      #
# --------------------------------------------------------------------------- #


def test_time_deposit_crud_and_derived_fields() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "td1@example.com")
        web = _login_web(client, "td1@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_TD1")

        start = datetime.now(timezone.utc).date().isoformat()
        base = _latest_change_id(client, token, "L_TD1")
        res = client.post(
            "/api/v1/write/ledgers/L_TD1/time-deposits",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "principal": 10000.0,
                "currency": "CNY",
                "annual_rate": 0.0325,
                "term_months": 12,
                "start_date": start,
                "account_name": "招商银行",
            },
        )
        assert res.status_code == 200, res.text
        deposit_id = res.json()["entity_id"]
        assert deposit_id.startswith("td_")

        # 读列表:到期日 = 起息日 + 12 个月;预期利息按单利现算
        res = client.get(
            "/api/v1/read/ledgers/L_TD1/time-deposits",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) == 1
        dep = items[0]
        assert dep["principal"] == 10000.0
        assert dep["term_months"] == 12
        assert dep["status"] == "holding"
        assert dep["is_matured"] is False
        assert dep["days_remaining"] >= 360
        # 10000 * 0.0325 * 365/365 ≈ 325
        assert dep["expected_interest"] == pytest.approx(325.0, abs=1.0)

        # 支取:status → withdrawn,withdrawn_at 缺省补今天
        base = _latest_change_id(client, token, "L_TD1")
        res = client.patch(
            f"/api/v1/write/ledgers/L_TD1/time-deposits/{deposit_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "status": "withdrawn", "actual_interest": 330.0},
        )
        assert res.status_code == 200, res.text
        res = client.get(
            "/api/v1/read/ledgers/L_TD1/time-deposits",
            headers={"Authorization": f"Bearer {token}"},
        )
        dep = res.json()[0]
        assert dep["status"] == "withdrawn"
        assert dep["actual_interest"] == 330.0
        assert dep["expected_interest"] == 330.0
        assert dep["days_remaining"] == 0
        assert dep["is_matured"] is True

        # 删除
        base = _latest_change_id(client, token, "L_TD1")
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/L_TD1/time-deposits/{deposit_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text
        res = client.get(
            "/api/v1/read/ledgers/L_TD1/time-deposits",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.json() == []
    finally:
        app.dependency_overrides.clear()


def test_time_deposit_validation_rejects_bad_principal() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "td2@example.com")
        web = _login_web(client, "td2@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_TD2")

        base = _latest_change_id(client, token, "L_TD2")
        res = client.post(
            "/api/v1/write/ledgers/L_TD2/time-deposits",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "principal": 0,
                "annual_rate": 0.03,
                "term_months": 12,
                "start_date": "2026-01-01",
            },
        )
        assert res.status_code == 422, res.text  # pydantic gt=0
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_time_deposit_partial_update_keeps_existing_fields() -> None:
    """mobile push 定期存款 → 落库;partial update(只改 status)保留其余字段。"""
    client, TS = _make_client()
    try:
        owner = _register(client, "td3@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_TD3")
        _push_change(
            client, token, device, "L_TD3", "time_deposit", "td-1",
            {
                "syncId": "td-1",
                "principal": 5000.0,
                "currency": "CNY",
                "annualRate": 0.03,
                "termMonths": 6,
                "startDate": "2026-01-01T00:00:00+00:00",
                "maturityDate": "2026-07-01T00:00:00+00:00",
                "status": "holding",
                "accountId": "acc-1",
                "accountName": "招行",
                "interestMode": "simple",
            },
        )
        row = _row(TS, ReadTimeDepositProjection, "L_TD3", "td-1")
        assert row.principal == 5000.0
        assert row.annual_rate == 0.03
        assert row.account_name == "招行"

        # partial update:只改 status,不带 accountName/rate 等
        _push_change(
            client, token, device, "L_TD3", "time_deposit", "td-1",
            {
                "syncId": "td-1",
                "principal": 5000.0,
                "annualRate": 0.03,
                "termMonths": 6,
                "startDate": "2026-01-01T00:00:00+00:00",
                "maturityDate": "2026-07-01T00:00:00+00:00",
                "status": "matured",
            },
        )
        row = _row(TS, ReadTimeDepositProjection, "L_TD3", "td-1")
        assert row.status == "matured"
        assert row.account_name == "招行"
        assert row.annual_rate == 0.03
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_time_deposit_delete_removes_projection() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "td4@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_TD4")
        _push_change(
            client, token, device, "L_TD4", "time_deposit", "td-del-1",
            {
                "syncId": "td-del-1",
                "principal": 1000.0,
                "annualRate": 0.02,
                "termMonths": 3,
                "startDate": "2026-01-01T00:00:00+00:00",
                "maturityDate": "2026-04-01T00:00:00+00:00",
            },
        )
        _row(TS, ReadTimeDepositProjection, "L_TD4", "td-del-1")
        _push_change(
            client, token, device, "L_TD4", "time_deposit", "td-del-1",
            {}, action="delete",
        )
        with TS() as db:
            internal_id = db.scalar(
                select(Ledger.id).where(Ledger.external_id == "L_TD4")
            )
            row = db.scalar(
                select(ReadTimeDepositProjection).where(
                    ReadTimeDepositProjection.ledger_id == internal_id,
                    ReadTimeDepositProjection.sync_id == "td-del-1",
                )
            )
            assert row is None
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 归物                                                                          #
# --------------------------------------------------------------------------- #


def test_item_return_crud() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "ir1@example.com")
        web = _login_web(client, "ir1@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_IR1")

        base = _latest_change_id(client, token, "L_IR1")
        res = client.post(
            "/api/v1/write/ledgers/L_IR1/item-returns",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "name": "蓝牙耳机",
                "original_amount": 299.0,
                "refund_amount": 299.0,
                "happened_at": "2026-10-01",
                "reason": "七天无理由",
                "status": "pending",
                "account_name": "支付宝",
            },
        )
        assert res.status_code == 200, res.text
        return_id = res.json()["entity_id"]
        assert return_id.startswith("ir_")

        res = client.get(
            "/api/v1/read/ledgers/L_IR1/item-returns",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) == 1
        assert items[0]["name"] == "蓝牙耳机"
        assert items[0]["refund_amount"] == 299.0
        assert items[0]["status"] == "pending"

        # 状态流转:pending → returned → refunded
        for next_status in ("returned", "refunded"):
            base = _latest_change_id(client, token, "L_IR1")
            res = client.patch(
                f"/api/v1/write/ledgers/L_IR1/item-returns/{return_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"base_change_id": base, "status": next_status},
            )
            assert res.status_code == 200, res.text
        res = client.get(
            "/api/v1/read/ledgers/L_IR1/item-returns",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.json()[0]["status"] == "refunded"

        # 删除
        base = _latest_change_id(client, token, "L_IR1")
        res = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/L_IR1/item-returns/{return_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base},
        )
        assert res.status_code == 200, res.text
        res = client.get(
            "/api/v1/read/ledgers/L_IR1/item-returns",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.json() == []
    finally:
        app.dependency_overrides.clear()


def test_item_return_validation_rejects_empty_name() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "ir2@example.com")
        web = _login_web(client, "ir2@example.com")
        token = web["access_token"]
        _seed_ledger(client, owner["access_token"], owner["device_id"], "L_IR2")

        base = _latest_change_id(client, token, "L_IR2")
        res = client.post(
            "/api/v1/write/ledgers/L_IR2/item-returns",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "name": "",
                "refund_amount": 10.0,
                "happened_at": "2026-10-01",
            },
        )
        assert res.status_code == 422, res.text
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_item_return_partial_update_keeps_existing_fields() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "ir3@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_IR3")
        _push_change(
            client, token, device, "L_IR3", "item_return", "ir-1",
            {
                "syncId": "ir-1",
                "name": "键盘",
                "originalTxId": "tx-9",
                "originalAmount": 400.0,
                "refundAmount": 380.0,
                "happenedAt": "2026-09-20T00:00:00+00:00",
                "reason": "质量问题",
                "status": "returned",
                "accountId": "acc-3",
                "accountName": "微信",
            },
        )
        row = _row(TS, ReadItemReturnProjection, "L_IR3", "ir-1")
        assert row.name == "键盘"
        assert row.original_tx_id == "tx-9"

        # partial update:只改 status + refundAmount
        _push_change(
            client, token, device, "L_IR3", "item_return", "ir-1",
            {
                "syncId": "ir-1",
                "name": "键盘",
                "refundAmount": 380.0,
                "happenedAt": "2026-09-20T00:00:00+00:00",
                "status": "refunded",
            },
        )
        row = _row(TS, ReadItemReturnProjection, "L_IR3", "ir-1")
        assert row.status == "refunded"
        # merge 契约:不带 accountName 也要保留
        assert row.account_name == "微信"
        assert row.original_tx_id == "tx-9"
        assert row.reason == "质量问题"
    finally:
        app.dependency_overrides.clear()
