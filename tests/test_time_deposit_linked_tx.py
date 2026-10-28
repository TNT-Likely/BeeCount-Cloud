"""0021 定期存款 关联交易(存入 / 支取)测试。

核心诉求:存入定期应从原账户扣除存款金额;支取时应把本息转回原账户。
且两类流水在交易流水里都应是 **转账** —— 本金只是在「账户 ↔ 定期存款」之间
搬家,不是真的花掉 / 赚到(所以也不是收入支出)。

本测试通过 web write API 创建 / 更新 / 删除定期存款,断言:
- 创建(holding)→ 自动生成一条 ``transfer``(本金)关联交易,``from`` 挂原账户
  (钱从账户出去进定期),且账户聚合余额 = -本金;
- 推进到 withdrawn(带实得利息)→ 额外生成一条 ``transfer``(本金 + 利息)
  关联交易,``to`` 挂原账户(本息回到账户),账户余额 = -本金 + (本金 + 利息)
  = +利息;原存入流水保留;
- 删除 → 两条关联交易都被清理,账户余额恢复到 0。

关联交易打 ``excludeFromStats=True``(不污染全局收支统计,但与借款 /
报销一致:账户余额与流水包含它们,真实反映资金变动)。

注意:定期存款不是账户,所以转账的另一侧留空(读出来显示「现金 → -」),
语义靠 note(存入定期 / 定期支取)表达。**绝不能**给空侧填一个账户名 ——
projection 会按名反查成真实账户 sync_id,凭空改余额。
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
from src.models import ReadTxProjection


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


def _push_account(client: TestClient, token: str, device_id: str, payload: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": "LDG",
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


def _setup(email: str):
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    web_token = web["access_token"]
    _seed_ledger(client, owner["access_token"], owner["device_id"], "LDG")
    _push_account(
        client,
        owner["access_token"],
        owner["device_id"],
        {"syncId": "acc-1", "name": "现金", "type": "cash", "currency": "CNY", "initialBalance": 0},
    )
    return client, TS, web_token


def _change_id(client: TestClient, token: str) -> int:
    res = client.get(
        "/api/v1/read/ledgers/LDG",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def _create_deposit(client: TestClient, token: str, **body) -> dict:
    base = _change_id(client, token)
    res = client.post(
        f"/api/v1/write/ledgers/LDG/time-deposits",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _patch_deposit(client: TestClient, token: str, deposit_id: str, **body) -> dict:
    base = _change_id(client, token)
    res = client.patch(
        f"/api/v1/write/ledgers/LDG/time-deposits/{deposit_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _delete_deposit(client: TestClient, token: str, deposit_id: str) -> dict:
    base = _change_id(client, token)
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/LDG/time-deposits/{deposit_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _account_balance(client: TestClient, token: str) -> float:
    res = client.get(
        "/api/v1/read/workspace/accounts?ledger_id=LDG",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    rows = res.json()
    hit = [a for a in rows if a["name"] == "现金"]
    assert hit, f"账户 现金 不在返回列表: {rows}"
    return float(hit[0]["balance"])


def _linked_txs(TS, suffix: str):
    db = TS()
    try:
        rows = db.execute(
            select(ReadTxProjection).where(ReadTxProjection.sync_id.like(f"%{suffix}"))
        ).scalars().all()
        return [
            {
                "sync_id": r.sync_id,
                "tx_type": r.tx_type,
                "amount": float(r.amount),
                "account_sync_id": r.account_sync_id,
                "from_account_sync_id": r.from_account_sync_id,
                "to_account_sync_id": r.to_account_sync_id,
                "exclude_from_stats": r.exclude_from_stats,
            }
            for r in rows
        ]
    finally:
        db.close()


def test_time_deposit_deducts_from_source_on_deposit_and_returns_on_withdraw():
    client, TS, tk = _setup("td_linked@test.com")

    # 1) 存入 500(holding)→ 账户 -500,并产生存入(transfer)关联交易
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

    assert _account_balance(client, tk) == -500.0

    deposits = _linked_txs(TS, "::td-deposit")
    assert len(deposits) == 1
    d = deposits[0]
    assert d["tx_type"] == "transfer"
    assert d["amount"] == 500.0
    # 转账方向:钱从原账户出去、进定期(定期不是账户 → to 侧留空)
    assert d["from_account_sync_id"] == "acc-1"
    assert d["to_account_sync_id"] is None
    assert d["exclude_from_stats"] is True

    # 存入态不应有支取流水
    assert _linked_txs(TS, "::td-withdraw") == []

    # 2) 支取(withdrawn,实得利息 10)→ 额外 +510,账户净 +10;存入流水保留
    _patch_deposit(
        client, tk, dep_id,
        status="withdrawn",
        actual_interest=10,
        withdrawn_at="2026-07-01",
    )
    assert _account_balance(client, tk) == 10.0

    withdraws = _linked_txs(TS, "::td-withdraw")
    assert len(withdraws) == 1
    w = withdraws[0]
    assert w["tx_type"] == "transfer"
    assert w["amount"] == 510.0
    # 反向转账:本息回到原账户(to 侧挂原账户,from 侧留空)
    assert w["to_account_sync_id"] == "acc-1"
    assert w["from_account_sync_id"] is None

    # 存入流水仍在(完整资金流:出去 → 回来带息)
    assert len(_linked_txs(TS, "::td-deposit")) == 1

    # 3) 删除 → 两条关联交易都被清理,账户余额恢复 0
    _delete_deposit(client, tk, dep_id)
    assert _linked_txs(TS, "::td-deposit") == []
    assert _linked_txs(TS, "::td-withdraw") == []
    assert _account_balance(client, tk) == 0.0


def test_time_deposit_without_account_generates_no_linked_tx():
    client, TS, tk = _setup("td_noacct@test.com")

    # 不选原账户 → 不应生成任何关联交易,账户余额不变
    _create_deposit(
        client, tk,
        principal=300,
        term_months=6,
        start_date="2026-02-01",
        status="holding",
    )
    assert _linked_txs(TS, "::td-deposit") == []
    assert _linked_txs(TS, "::td-withdraw") == []
    assert _account_balance(client, tk) == 0.0


def test_time_deposit_matured_no_withdraw_tx():
    client, TS, tk = _setup("td_matured@test.com")

    _create_deposit(
        client, tk,
        principal=1000,
        term_months=12,
        start_date="2026-01-01",
        status="matured",
        account_id="acc-1",
        account_name="现金",
    )
    # 到期未支取:只扣本金,不应有支取流水
    assert _account_balance(client, tk) == -1000.0
    assert len(_linked_txs(TS, "::td-deposit")) == 1
    assert _linked_txs(TS, "::td-withdraw") == []
