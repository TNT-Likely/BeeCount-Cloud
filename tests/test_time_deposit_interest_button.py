"""0030 定期存款「利息按键」:手动生成关联利息收入流水。

与 test_time_deposit_linked_tx.py 的系统自动关联交易(存入/支取)不同,本测试覆盖
用户手动点「利息」按钮创建的、挂在定期存款上的收入交易:

- 关联交易写入 time_deposit_sync_id,读端点透出 time_deposit_id;
- 定存列表现算 interest_received(关联交易中 income 金额合计)与 interest_tx_count;
- 新的「某定存关联交易」端点能列出这些流水;
- 系统自动的存入/支取流水(不带 timeDepositId)**不**计入 interest_received。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTxProjection

from test_time_deposit_linked_tx import _setup, _create_deposit, _change_id


def _create_income_tx(client: TestClient, token: str, deposit_id: str, amount: float) -> dict:
    base = _change_id(client, token)
    res = client.post(
        "/api/v1/write/ledgers/LDG/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "tx_type": "income",
            "amount": amount,
            "category_name": "利息",
            "account_name": "现金",
            "time_deposit_id": deposit_id,
            "happened_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _list_deposits(client: TestClient, token: str) -> list[dict]:
    res = client.get(
        "/api/v1/read/ledgers/LDG/time-deposits",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _deposit_txs(client: TestClient, token: str, deposit_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/LDG/time-deposits/{deposit_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _linked_by_deposit(TS, deposit_id: str) -> list[dict]:
    db = TS()
    try:
        rows = (
            db.execute(
                select(ReadTxProjection).where(
                    ReadTxProjection.time_deposit_sync_id == deposit_id
                )
            )
            .scalars()
            .all()
        )
        return [
            {"sync_id": r.sync_id, "tx_type": r.tx_type, "amount": float(r.amount)}
            for r in rows
        ]
    finally:
        db.close()


def test_interest_button_creates_linked_income_and_accumulates():
    client, TS, tk = _setup("td_intbtn@test.com")

    created = _create_deposit(
        client, tk,
        principal=10000,
        annual_rate=0.03,
        term_months=12,
        start_date="2026-01-01",
        status="holding",
        account_id="acc-1",
        account_name="现金",
    )
    dep_id = created["entity_id"]

    # 点「利息」:生成一笔 120 的关联利息收入
    _create_income_tx(client, tk, dep_id, 120.0)

    deposits = _list_deposits(client, tk)
    dep = next(d for d in deposits if d["id"] == dep_id)
    assert dep["interest_received"] == 120.0, dep
    assert dep["interest_tx_count"] == 1, dep

    # 关联交易端点应列出这笔流水
    txs = _deposit_txs(client, tk, dep_id)
    assert len(txs) == 1
    assert txs[0]["time_deposit_id"] == dep_id
    assert txs[0]["tx_type"] == "income"
    assert float(txs[0]["amount"]) == 120.0

    # 直接查投影:time_deposit_sync_id 已落库
    linked = _linked_by_deposit(TS, dep_id)
    assert len(linked) == 1 and linked[0]["amount"] == 120.0

    # 再点一次:累计到 320
    _create_income_tx(client, tk, dep_id, 200.0)
    dep = next(d for d in _list_deposits(client, tk) if d["id"] == dep_id)
    assert dep["interest_received"] == 320.0
    assert dep["interest_tx_count"] == 2


def test_auto_deposit_withdraw_tx_not_counted_as_interest():
    """系统自动的存入(expense)/支取(income 本息)流水不带 timeDepositId,
    不应被 interest_received 计入。"""
    client, TS, tk = _setup("td_intauto@test.com")

    created = _create_deposit(
        client, tk,
        principal=500,
        annual_rate=0.03,
        term_months=12,
        start_date="2026-01-01",
        status="holding",
        account_id="acc-1",
        account_name="现金",
    )
    dep_id = created["entity_id"]

    # holding 态:只有自动存入(expense)流水,无手动利息
    dep = next(d for d in _list_deposits(client, tk) if d["id"] == dep_id)
    assert dep["interest_received"] == 0.0
    assert dep["interest_tx_count"] == 0
    # 自动流水确实存在,但没挂 timeDepositId
    auto = _linked_by_deposit(TS, dep_id)
    assert auto == []

    # 支取(带实得利息 10)→ 自动生成 income(510),仍不应计入 interest_received
    base = _change_id(client, tk)
    client.patch(
        f"/api/v1/write/ledgers/LDG/time-deposits/{dep_id}",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base, "status": "withdrawn",
              "actual_interest": 10, "withdrawn_at": "2026-07-01"},
    )
    dep = next(d for d in _list_deposits(client, tk) if d["id"] == dep_id)
    assert dep["interest_received"] == 0.0
    assert dep["interest_tx_count"] == 0
