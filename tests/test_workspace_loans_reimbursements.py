"""0023 跨账本借款 / 报销读取端点测试。

资产页净资产 / 资产构成需要把借款(借出 = 应收资产、借入 = 应付负债)与报销
(待报销 = 应收资产)的头寸并入。资产页账户是 **工作区全局** 口径
(`/read/workspace/accounts` 不传 ledger_id 即跨账本),所以借款 / 报销读取也
必须跨账本,才能与账户聚合对齐。

本测试通过 web write API 创建多账本借款 / 报销,断言:
- GET /read/workspace/loans 与 /read/workspace/reimbursements 跨账本返回全部
  可见账本的条目;
- 单条记录的字段(direction / principal / currency / status / amount / id /
  ledger_id)与写入一致;
- `status` 过滤生效(active / repaid、pending / reimbursed);
- `ledger_id` 过滤生效(只回该账本)。
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


def _change_id(client: TestClient, token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def _create_loan(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/loans",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _create_reimbursement(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/reimbursements",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _setup(email: str):
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    web_token = web["access_token"]
    _seed_ledger(client, owner["access_token"], owner["device_id"], "LDG1")
    _seed_ledger(client, owner["access_token"], owner["device_id"], "LDG2")
    return client, TS, web_token


def _get_workspace_loans(client: TestClient, token: str, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/v1/read/workspace/loans?{query}" if query else "/api/v1/read/workspace/loans"
    res = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def _get_workspace_reimbursements(client: TestClient, token: str, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = (
        f"/api/v1/read/workspace/reimbursements?{query}"
        if query
        else "/api/v1/read/workspace/reimbursements"
    )
    res = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# 借款                                                                         #
# --------------------------------------------------------------------------- #


def test_workspace_loans_span_all_ledgers():
    client, _TS, token = _setup("ws_loan_span@example.com")
    _create_loan(
        client, token, "LDG1",
        direction="lent", counterparty="张三", principal=8000, currency="CNY",
        interest_rate=0.05, started_at="2026-01-01", status="active",
    )
    _create_loan(
        client, token, "LDG2",
        direction="borrowed", counterparty="李四", principal=3000, currency="CNY",
        interest_rate=0.0, started_at="2026-02-01", status="active",
    )

    rows = _get_workspace_loans(client, token)
    assert len(rows) == 2
    directions = {r["direction"] for r in rows}
    assert directions == {"lent", "borrowed"}
    principals = sorted(r["principal"] for r in rows)
    assert principals == [3000.0, 8000.0]
    # 跨账本:每条都带 ledger_id
    assert {r["ledger_id"] for r in rows} == {"LDG1", "LDG2"}


def test_workspace_loans_status_filter():
    client, _TS, token = _setup("ws_loan_status@example.com")
    created = _create_loan(
        client, token, "LDG1",
        direction="lent", counterparty="张三", principal=8000, currency="CNY",
        interest_rate=0.05, started_at="2026-01-01", status="active",
    )
    loan_id = created["entity_id"]
    base = _change_id(client, token, "LDG1")
    res = client.patch(
        f"/api/v1/write/ledgers/LDG1/loans/{loan_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, "status": "repaid"},
    )
    assert res.status_code == 200, res.text

    active = _get_workspace_loans(client, token, status="active")
    assert active == [], "repaid 不应出现在 active 过滤结果"

    repaid = _get_workspace_loans(client, token, status="repaid")
    assert len(repaid) == 1
    assert repaid[0]["status"] == "repaid"


def test_workspace_loans_ledger_filter():
    client, _TS, token = _setup("ws_loan_ledger@example.com")
    _create_loan(
        client, token, "LDG1",
        direction="lent", counterparty="张三", principal=8000, currency="CNY",
        interest_rate=0.05, started_at="2026-01-01", status="active",
    )
    _create_loan(
        client, token, "LDG2",
        direction="borrowed", counterparty="李四", principal=3000, currency="CNY",
        interest_rate=0.0, started_at="2026-02-01", status="active",
    )

    only_ldg1 = _get_workspace_loans(client, token, ledger_id="LDG1")
    assert len(only_ldg1) == 1
    assert only_ldg1[0]["direction"] == "lent"
    assert only_ldg1[0]["ledger_id"] == "LDG1"


# --------------------------------------------------------------------------- #
# 报销                                                                         #
# --------------------------------------------------------------------------- #


def test_workspace_reimbursements_span_all_ledgers():
    client, _TS, token = _setup("ws_rb_span@example.com")
    _create_reimbursement(
        client, token, "LDG1",
        title="出差机票", amount=1200, currency="CNY",
        happened_at="2026-03-01", status="pending",
    )
    _create_reimbursement(
        client, token, "LDG2",
        title="团建餐费", amount=600, currency="CNY",
        happened_at="2026-03-05", status="pending",
    )

    rows = _get_workspace_reimbursements(client, token)
    assert len(rows) == 2
    titles = {r["title"] for r in rows}
    assert titles == {"出差机票", "团建餐费"}
    amounts = sorted(r["amount"] for r in rows)
    assert amounts == [600.0, 1200.0]


def test_workspace_reimbursements_status_filter():
    client, _TS, token = _setup("ws_rb_status@example.com")
    created = _create_reimbursement(
        client, token, "LDG1",
        title="出差机票", amount=1200, currency="CNY",
        happened_at="2026-03-01", status="pending",
    )
    rb_id = created["entity_id"]
    base = _change_id(client, token, "LDG1")
    res = client.patch(
        f"/api/v1/write/ledgers/LDG1/reimbursements/{rb_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, "status": "reimbursed"},
    )
    assert res.status_code == 200, res.text

    pending = _get_workspace_reimbursements(client, token, status="pending")
    assert pending == [], "reimbursed 不应出现在 pending 过滤结果"

    reimbursed = _get_workspace_reimbursements(client, token, status="reimbursed")
    assert len(reimbursed) == 1
    assert reimbursed[0]["status"] == "reimbursed"
