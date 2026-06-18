"""服务端预算用量按 exclude_from_budget 过滤(设计 D2):

预算 used 只看 exclude_from_budget,不看 exclude_from_stats —— 两标记独立。
- exclude_from_budget=True 的交易 → 不计入预算用量
- exclude_from_stats=True 但 exclude_from_budget=False 的交易 → 仍计入预算用量

交易标记只能通过 /sync/push 的 camelCase payload 设置(写端点不收该字段),
预算通过 web write 端点创建。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
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
    return TestClient(app)


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


def _push_tx(
    client: TestClient, token: str, device_id: str, ledger_id: str, payload: dict
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
                    "entity_type": "transaction",
                    "entity_sync_id": payload["syncId"],
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def test_total_budget_usage_excludes_exclude_from_budget_only() -> None:
    client = _make_client()
    try:
        owner = _register(client, "bef@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_BUDGET_EXCL"
        _seed_ledger(client, app_token, device, ledger_id)

        # 当前周期内的 happened_at(start_day 默认 1 → 本月 1 日到下月 1 日)
        happened = (
            datetime.now(timezone.utc)
            .replace(hour=12, minute=0, second=0, microsecond=0)
            .isoformat()
        )

        # (a) 普通支出 100 —— 计入
        _push_tx(
            client, app_token, device, ledger_id,
            {
                "syncId": "tx-normal",
                "type": "expense",
                "amount": 100.0,
                "happenedAt": happened,
            },
        )
        # (b) exclude_from_budget=True 支出 500 —— 不计入
        _push_tx(
            client, app_token, device, ledger_id,
            {
                "syncId": "tx-excl-budget",
                "type": "expense",
                "amount": 500.0,
                "happenedAt": happened,
                "excludeFromBudget": True,
            },
        )
        # (c) exclude_from_stats=True 但 exclude_from_budget=False 支出 30 —— 仍计入
        _push_tx(
            client, app_token, device, ledger_id,
            {
                "syncId": "tx-excl-stats",
                "type": "expense",
                "amount": 30.0,
                "happenedAt": happened,
                "excludeFromStats": True,
                "excludeFromBudget": False,
            },
        )

        # 用 web token 创建总预算(写端点要求 web scope)
        web = _login_web(client, "bef@example.com")
        token = web["access_token"]
        base = _latest_change_id(client, token, ledger_id)
        res = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/budgets",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "type": "total",
                "amount": 5000,
                "start_day": 1,
            },
        )
        assert res.status_code == 200, res.text

        res = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/budgets/usage",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert len(items) == 1
        # D2: (b) 被排除,(c) 仍计入 → 100 + 30 = 130
        assert items[0]["used"] == 130.0, (
            f"expected 130.0 (100+30, exclude_from_budget excluded, "
            f"exclude_from_stats still counted), got {items[0]['used']}"
        )
    finally:
        app.dependency_overrides.clear()
