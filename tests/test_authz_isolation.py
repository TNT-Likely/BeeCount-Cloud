"""Multi-user isolation regression.

Self-hosted deployments can have multiple users registered against one backend.
This test guarantees user B cannot see or touch user A's ledger or data —
independent of every router's own ACL code.
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


def _register(client: TestClient, email: str, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    client.post(
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
                    "payload": {
                        "content": (
                            f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
                            '"items":[],"accounts":[],"categories":[],"tags":[]}'
                        )
                    },
                    "updated_at": now,
                }
            ],
        },
    )


def test_user_b_cannot_see_or_touch_user_a_ledger() -> None:
    client = _make_client()
    try:
        a = _register(client, "a@example.com")
        b = _register(client, "b@example.com")
        a_token, a_device = a["access_token"], a["device_id"]
        b_token, b_device = b["access_token"], b["device_id"]

        # A owns ledger "LA".
        _seed_ledger(client, a_token, a_device, "LA")

        # B must get empty ledger list on /sync/ledgers.
        b_ledgers = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {b_token}"},
        )
        assert b_ledgers.status_code == 200
        assert b_ledgers.json() == []

        # B must not be able to /sync/full A's ledger.
        b_full = client.get(
            "/api/v1/sync/full?ledger_id=LA",
            headers={"Authorization": f"Bearer {b_token}"},
        )
        assert b_full.status_code == 200
        # Snapshot must be None (B has no access); a present snapshot would be a leak.
        assert b_full.json().get("snapshot") is None

        # B must not be able to read A's ledger detail via /read/ledgers/LA.
        b_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "b@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        ).json()
        res = client.get(
            "/api/v1/read/ledgers/LA",
            headers={"Authorization": f"Bearer {b_web['access_token']}"},
        )
        assert res.status_code == 404, res.text

        # B pushing to ledger_id="LA" creates B's OWN ledger called "LA" —
        # external_id is namespaced per user via the (user_id, external_id)
        # unique constraint, so this is not a leak. A's "LA" is not touched.
        push = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {b_token}"},
            json={
                "device_id": b_device,
                "changes": [
                    {
                        "ledger_id": "LA",
                        "entity_type": "transaction",
                        "entity_sync_id": "hack",
                        "action": "upsert",
                        "payload": {"amount": 9999, "type": "expense"},
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        assert push.status_code == 200, push.text

        # A's ledger still has zero transactions — B's push landed in B's own
        # ledger, not A's.
        a_detail = client.get(
            "/api/v1/sync/full?ledger_id=LA",
            headers={"Authorization": f"Bearer {a_token}"},
        )
        assert a_detail.status_code == 200
        snapshot_payload = a_detail.json().get("snapshot")
        assert snapshot_payload is not None
        # A's snapshot items must stay empty (no tx from B).
        import json as _json
        content = _json.loads(snapshot_payload["payload"]["content"])
        assert content.get("items") == [], content

        # A still sees their own ledger.
        a_ledgers = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {a_token}"},
        )
        assert a_ledgers.status_code == 200
        external_ids = [lg["ledger_id"] for lg in a_ledgers.json()]
        assert external_ids == ["LA"], external_ids
    finally:
        app.dependency_overrides.clear()


def test_users_can_reuse_same_external_ledger_id_independently() -> None:
    """Two users each creating a ledger called 'default' must not collide."""
    client = _make_client()
    try:
        a = _register(client, "a@example.com")
        b = _register(client, "b@example.com")
        _seed_ledger(client, a["access_token"], a["device_id"], "default")
        _seed_ledger(client, b["access_token"], b["device_id"], "default")

        a_ledgers = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        ).json()
        b_ledgers = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {b['access_token']}"},
        ).json()
        assert [lg["ledger_id"] for lg in a_ledgers] == ["default"]
        assert [lg["ledger_id"] for lg in b_ledgers] == ["default"]
    finally:
        app.dependency_overrides.clear()


def test_register_disabled_returns_403() -> None:
    """With REGISTRATION_ENABLED=false, /auth/register must refuse."""
    import os

    from src.config import get_settings

    # Force-reload settings with reg disabled.
    prior = os.environ.get("REGISTRATION_ENABLED")
    os.environ["REGISTRATION_ENABLED"] = "false"
    get_settings.cache_clear()
    # Rebind the module-level settings in auth router so its closure sees the new value.
    import importlib

    from src.routers import auth as auth_router

    importlib.reload(auth_router)
    # Re-register router on a fresh app to pick up the reloaded function.
    from src.main import app as main_app

    client = _make_client()
    try:
        res = client.post(
            "/api/v1/auth/register",
            json={
                "email": "x@example.com",
                "password": "123456",
                "client_type": "app",
                "device_name": "p",
                "platform": "app",
            },
        )
        assert res.status_code == 403, res.text
    finally:
        app.dependency_overrides.clear()
        # Restore for other tests.
        if prior is None:
            os.environ.pop("REGISTRATION_ENABLED", None)
        else:
            os.environ["REGISTRATION_ENABLED"] = prior
        get_settings.cache_clear()
        importlib.reload(auth_router)
        _ = main_app  # silence
