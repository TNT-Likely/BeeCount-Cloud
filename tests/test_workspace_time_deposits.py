"""0021 跨账本定期存款读取端点测试。

资产页净资产 / 资产构成需要把「持有中定期存款本金」当作资产头寸并入(否则存入
定期后账户余额被扣、定期又没算进资产,总资产凭空少了本金)。资产页账户是
**工作区全局**口径(`/read/workspace/accounts` 不传 ledger_id 即跨账本),所以
定期读取也必须跨账本,才能与账户聚合对齐。

本测试通过 web write API 创建多账本定期存款,断言:
- GET /read/workspace/time-deposits 跨账本返回全部可见账本的存款;
- 单条记录的 principal / currency / status / id 与写入一致;
- `status` 过滤生效(holding / withdrawn);
- `ledger_id` 过滤生效(只回该账本)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadTimeDepositProjection


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


def _create_deposit(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/time-deposits",
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


def _get_workspace(client: TestClient, token: str, **params) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/api/v1/read/workspace/time-deposits?{query}" if query else "/api/v1/read/workspace/time-deposits"
    res = client.get(url, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def test_workspace_time_deposits_span_all_ledgers():
    client, _TS, token = _setup("ws_td_span@example.com")
    _create_deposit(
        client, token, "LDG1",
        name="存单A", principal=10000, currency="CNY", annual_rate=0.03,
        term_months=12, start_date="2026-01-01T00:00:00Z",
        maturity_date="2027-01-01T00:00:00Z", status="holding",
        account_id="acc-1", account_name="现金",
    )
    _create_deposit(
        client, token, "LDG2",
        name="存单B", principal=5000, currency="CNY", annual_rate=0.025,
        term_months=6, start_date="2026-02-01T00:00:00Z",
        maturity_date="2026-08-01T00:00:00Z", status="holding",
        account_id="acc-2", account_name="银行卡",
    )

    rows = _get_workspace(client, token)
    assert len(rows) == 2
    principals = sorted(r["principal"] for r in rows)
    assert principals == [5000.0, 10000.0]
    statuses = {r["status"] for r in rows}
    assert statuses == {"holding"}


def test_workspace_time_deposits_status_filter():
    client, _TS, token = _setup("ws_td_status@example.com")
    created = _create_deposit(
        client, token, "LDG1",
        name="存单A", principal=10000, currency="CNY", annual_rate=0.03,
        term_months=12, start_date="2026-01-01T00:00:00Z",
        maturity_date="2027-01-01T00:00:00Z", status="holding",
        account_id="acc-1", account_name="现金",
    )
    dep_id = created["entity_id"]
    base = _change_id(client, token, "LDG1")
    res = client.patch(
        f"/api/v1/write/ledgers/LDG1/time-deposits/{dep_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base, "status": "withdrawn",
            "withdrawn_at": "2026-06-01T00:00:00Z", "actual_interest": 150,
        },
    )
    assert res.status_code == 200, res.text

    holding = _get_workspace(client, token, status="holding")
    assert holding == [], "withdrawn 不应出现在 holding 过滤结果"

    withdrawn = _get_workspace(client, token, status="withdrawn")
    assert len(withdrawn) == 1
    assert withdrawn[0]["status"] == "withdrawn"
    assert withdrawn[0]["actual_interest"] == 150.0


def test_workspace_time_deposits_ledger_filter():
    client, _TS, token = _setup("ws_td_ledger@example.com")
    _create_deposit(
        client, token, "LDG1",
        name="存单A", principal=10000, currency="CNY", annual_rate=0.03,
        term_months=12, start_date="2026-01-01T00:00:00Z",
        maturity_date="2027-01-01T00:00:00Z", status="holding",
        account_id="acc-1", account_name="现金",
    )
    _create_deposit(
        client, token, "LDG2",
        name="存单B", principal=5000, currency="CNY", annual_rate=0.025,
        term_months=6, start_date="2026-02-01T00:00:00Z",
        maturity_date="2026-08-01T00:00:00Z", status="holding",
        account_id="acc-2", account_name="银行卡",
    )

    only_ldg1 = _get_workspace(client, token, ledger_id="LDG1")
    assert len(only_ldg1) == 1
    assert only_ldg1[0]["principal"] == 10000.0
    assert only_ldg1[0]["ledger_id"] == "LDG1"


def test_workspace_time_deposits_derived_fields_present():
    client, _TS, token = _setup("ws_td_derived@example.com")
    _create_deposit(
        client, token, "LDG1",
        name="存单A", principal=10000, currency="CNY", annual_rate=0.03,
        term_months=12, start_date="2024-01-01T00:00:00Z",
        maturity_date="2025-01-01T00:00:00Z", status="holding",
        account_id="acc-1", account_name="现金",
    )
    rows = _get_workspace(client, token)
    assert len(rows) == 1
    r = rows[0]
    # 已到期的 holding:派生字段应反映 is_matured / 负 days_remaining。
    assert r["is_matured"] is True
    assert r["days_remaining"] <= 0
    assert r["expected_interest"] > 0
    assert r["ledger_id"] == "LDG1"
