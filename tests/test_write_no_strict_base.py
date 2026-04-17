"""Guard: /write/* must not 409 on base_change_id mismatch by default.

Before we dropped the strict equality check, a mobile fullPush streaming
changes would bump the server-side latest ledger_snapshot faster than web
retries could catch up, producing endless 409s. These tests encode the new
contract: strict check OFF by default, opt-in via STRICT_BASE_CHANGE_ID.
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


def _register_app(client: TestClient, email: str) -> dict:
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


def _seed_snapshot(client: TestClient, token: str, device_id: str, ledger_id: str) -> int:
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
    return int(res.json()["server_cursor"])


def test_write_ignores_stale_base_change_id_by_default() -> None:
    """Web sends an out-of-date base_change_id → should still succeed."""
    client = _make_client()
    try:
        owner = _register_app(client, "owner@example.com")
        owner_app_token = owner["access_token"]
        owner_device = owner["device_id"]

        _seed_snapshot(client, owner_app_token, owner_device, "L1")

        owner_web = _login_web(client, "owner@example.com")
        web_token = owner_web["access_token"]

        # Mobile pushes some entity changes → server-side materializer bumps
        # latest_change_id repeatedly.
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            res = client.post(
                "/api/v1/sync/push",
                headers={"Authorization": f"Bearer {owner_app_token}"},
                json={
                    "device_id": owner_device,
                    "changes": [
                        {
                            "ledger_id": "L1",
                            "entity_type": "transaction",
                            "entity_sync_id": f"mtx{i}",
                            "action": "upsert",
                            "payload": {
                                "syncId": f"mtx{i}",
                                "type": "expense",
                                "amount": 5.0 + i,
                                "happenedAt": now,
                                "categoryName": "Food",
                                "categoryKind": "expense",
                                "accountName": "Cash",
                            },
                            "updated_at": now,
                        }
                    ],
                },
            )
            assert res.status_code == 200, res.text

        # Web's cached base_change_id is 1 (the seed) but server latest is way
        # higher after materialization. Legacy strict check would 409; the new
        # default behavior must succeed.
        acc_res = client.post(
            "/api/v1/write/ledgers/L1/accounts",
            headers={"Authorization": f"Bearer {web_token}"},
            json={
                "base_change_id": 1,
                "name": "WebAcct",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 100,
            },
        )
        assert acc_res.status_code == 200, acc_res.text
        meta = acc_res.json()
        assert meta["ledger_id"] == "L1"
        assert meta["new_change_id"] > 0
        assert meta["entity_id"]
    finally:
        app.dependency_overrides.clear()


def test_write_409_when_strict_flag_is_on() -> None:
    """With STRICT_BASE_CHANGE_ID=true, the legacy 409 behavior must return."""
    import importlib
    import os

    from src.config import get_settings

    prior = os.environ.get("STRICT_BASE_CHANGE_ID")
    os.environ["STRICT_BASE_CHANGE_ID"] = "true"
    get_settings.cache_clear()

    # Rebind module-level `settings` inside write.py (captured at import time).
    from src.routers import write as write_router

    importlib.reload(write_router)

    client = _make_client()
    try:
        owner = _register_app(client, "owner@example.com")
        owner_app_token = owner["access_token"]
        owner_device = owner["device_id"]

        _seed_snapshot(client, owner_app_token, owner_device, "L1")
        owner_web = _login_web(client, "owner@example.com")
        web_token = owner_web["access_token"]

        # Web sends a stale base_change_id → must 409 now.
        res = client.post(
            "/api/v1/write/ledgers/L1/accounts",
            headers={"Authorization": f"Bearer {web_token}"},
            json={
                "base_change_id": 999,
                "name": "WebAcct",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 0,
            },
        )
        assert res.status_code == 409, res.text
    finally:
        app.dependency_overrides.clear()
        if prior is None:
            os.environ.pop("STRICT_BASE_CHANGE_ID", None)
        else:
            os.environ["STRICT_BASE_CHANGE_ID"] = prior
        get_settings.cache_clear()
        importlib.reload(write_router)
