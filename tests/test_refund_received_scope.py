"""退款「到账才算已退款」口径(pending 不再与余额给出相反结论)。

背景:回款 income 只在 status=received 时生成
(services/linked_transactions.py)—— pending 的钱还没到用户手里。此前统计与
流水删除线却把 pending 也算进「已退」,于是同一笔钱出现两个相反结论:
expense_total 说支出已被冲减(钱回来了)、账户余额却仍扣着这笔钱(钱没回来),
流水里还画着删除线。

本文件固化修复后的「已到账」口径:
- pending:refunded_total / refunded_to_account 均为 0 —— 不画删除线、
  支出统计不冲减、不生成回款;
- PATCH 成 received:三者同时生效 —— 删除线到位、统计冲减、回款 income
  生成(余额回来);
- 退款管理页(list_refunds)的进度 / 剩余可退仍是「已申请」口径(含 pending),
  pending 照样占额度,不能重复退。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
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


def _push_entity(
    client: TestClient,
    token: str,
    device_id: str,
    ledger_id: str,
    entity_type: str,
    payload: dict,
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
                    "entity_sync_id": payload.get("syncId"),
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _create_refund(client: TestClient, token: str, ledger_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/refunds",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "happened_at": datetime.now(timezone.utc).date().isoformat(),
            **body,
        },
    )


def _patch_refund(client: TestClient, token: str, ledger_id: str, refund_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    return client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/refunds/{refund_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )


def _get(client: TestClient, token: str, path: str):
    res = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def _setup(email: str, ledger_id: str):
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, web["access_token"], owner["access_token"], owner["device_id"]


def _setup_two_accounts(email: str, ledger_id: str):
    client, TS, token, app_token, device = _setup(email, ledger_id)
    for sync_id, name in (("acc-1", "微信"), ("acc-2", "支付宝")):
        _push_entity(
            client, app_token, device, ledger_id, "account",
            {"syncId": sync_id, "name": name, "type": "card",
             "currency": "CNY", "initialBalance": 0},
        )
    return client, TS, token


def _tx_row(client: TestClient, token: str, ledger_id: str, tx_id: str) -> dict:
    page = _get(client, token, f"/api/v1/read/workspace/transactions?ledger_id={ledger_id}")
    return next(r for r in page["items"] if r["id"] == tx_id)


def _summary(client: TestClient, token: str, ledger_id: str) -> dict:
    return _get(client, token, f"/api/v1/read/summary?ledger_id={ledger_id}")


def _expense_by_card(client: TestClient, token: str, ledger_id: str) -> dict[str, float]:
    rows = _get(client, token, f"/api/v1/read/workspace/accounts?ledger_id={ledger_id}")
    return {r["name"]: float(r["expense_total"]) for r in rows}


# --------------------------------------------------------------------------- #


def test_pending_refund_is_not_counted_as_refunded() -> None:
    """pending(未到账)不算已退款:不画删除线、不冲减统计、不生成回款。"""
    client, TS, token = _setup_two_accounts("rs1@example.com", "L_RS1")
    try:
        tx = _web_tx(
            client, token, "L_RS1", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        tx_id = tx["entity_id"]
        res = _create_refund(
            client, token, "L_RS1", original_tx_id=tx_id, amount=500.0,
            refund_mode="unified", account_id="acc-1", account_name="微信",
            status="pending",
        )
        assert res.status_code == 200, res.text

        # 流水行:pending 不算已退 → 前端不画删除线
        row = _tx_row(client, token, "L_RS1", tx_id)
        assert row["refunded_total"] == pytest.approx(0.0)
        assert row["refunded_to_account"] == pytest.approx(0.0)

        summary = _summary(client, token, "L_RS1")
        # 统计:钱没回来 → 支出仍真实发生,不冲减
        assert summary["expense_total"] == pytest.approx(500.0)
        # 余额:未到账不生成回款 income → 仍只记支出 -500
        assert summary["balance"] == pytest.approx(-500.0)
    finally:
        app.dependency_overrides.clear()


def test_pending_becomes_received_activates_all_three() -> None:
    """pending → received:删除线、统计冲减、回款余额三者同时生效。"""
    client, TS, token = _setup_two_accounts("rs2@example.com", "L_RS2")
    try:
        tx = _web_tx(
            client, token, "L_RS2", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        tx_id = tx["entity_id"]
        res = _create_refund(
            client, token, "L_RS2", original_tx_id=tx_id, amount=500.0,
            refund_mode="unified", account_id="acc-1", account_name="微信",
            status="pending",
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        # 确认起点是「未退」
        assert _summary(client, token, "L_RS2")["expense_total"] == pytest.approx(500.0)

        # 标记已到账
        res = _patch_refund(
            client, token, "L_RS2", refund_id, status="received",
        )
        assert res.status_code == 200, res.text

        row = _tx_row(client, token, "L_RS2", tx_id)
        assert row["refunded_total"] == pytest.approx(500.0)
        assert row["refunded_to_account"] == pytest.approx(500.0)

        summary = _summary(client, token, "L_RS2")
        assert summary["expense_total"] == pytest.approx(0.0)
        # 回款 income 已生成:余额从 -500 回到 0(回款带 excludeFromStats,
        # 故收入统计仍为 0)
        assert summary["balance"] == pytest.approx(0.0)
        assert summary["income_total"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_refunds_page_progress_counts_pending() -> None:
    """退款管理页进度仍是「已申请」口径:pending 占额度,不能重复退。"""
    client, TS, token = _setup_two_accounts("rs3@example.com", "L_RS3")
    try:
        tx = _web_tx(
            client, token, "L_RS3", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        res = _create_refund(
            client, token, "L_RS3", original_tx_id=tx["entity_id"],
            amount=200.0, refund_mode="unified",
            account_id="acc-1", account_name="微信", status="pending",
        )
        assert res.status_code == 200, res.text

        rows = _get(client, token, "/api/v1/read/ledgers/L_RS3/refunds")
        assert len(rows) == 1
        # pending 照样计入进度与剩余可退(额度占用语义)
        assert rows[0]["refunded_total"] == pytest.approx(200.0)
        assert rows[0]["remaining_refundable"] == pytest.approx(300.0)
        # 但统计口径仍未冲减
        assert _summary(client, token, "L_RS3")["expense_total"] == pytest.approx(500.0)
    finally:
        app.dependency_overrides.clear()


def test_accounts_expense_not_netted_while_pending() -> None:
    """账户页:本卡 pending 退款不冲减本卡支出,到账后才冲减。"""
    client, TS, token = _setup_two_accounts("rs4@example.com", "L_RS4")
    try:
        tx = _web_tx(
            client, token, "L_RS4", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        res = _create_refund(
            client, token, "L_RS4", original_tx_id=tx["entity_id"],
            amount=200.0, refund_mode="unified",
            account_id="acc-1", account_name="微信", status="pending",
        )
        assert res.status_code == 200, res.text
        refund_id = res.json()["entity_id"]

        # pending:微信支出仍是 500
        assert _expense_by_card(client, token, "L_RS4") == {"微信": 500.0, "支付宝": 0.0}

        res = _patch_refund(client, token, "L_RS4", refund_id, status="received")
        assert res.status_code == 200, res.text
        # 到账后:退回本卡的 200 才从本卡支出里扣掉
        assert _expense_by_card(client, token, "L_RS4") == {"微信": 300.0, "支付宝": 0.0}
    finally:
        app.dependency_overrides.clear()
