"""Ledger soft-delete: mobile push or web DELETE should both hide the ledger
from subsequent reads, while preserving the underlying SyncChange history for
audit. Other users' ledgers with the same external_id must be unaffected."""

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


def _seed_snapshot(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
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


def test_mobile_push_delete_hides_ledger_from_reads() -> None:
    client = _make_client()
    try:
        owner = _register(client, "o@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_snapshot(client, app_token, device, "L1")

        # Confirm ledger visible pre-delete.
        r = client.get("/api/v1/sync/ledgers", headers={"Authorization": f"Bearer {app_token}"})
        assert [lg["ledger_id"] for lg in r.json()] == ["L1"]

        # Mobile pushes a ledger_snapshot delete tombstone.
        now = datetime.now(timezone.utc).isoformat()
        res = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {app_token}"},
            json={
                "device_id": device,
                "changes": [
                    {
                        "ledger_id": "L1",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "L1",
                        "action": "delete",
                        "payload": {},
                        "updated_at": now,
                    }
                ],
            },
        )
        assert res.status_code == 200, res.text

        # /sync/ledgers now skips it.
        r = client.get("/api/v1/sync/ledgers", headers={"Authorization": f"Bearer {app_token}"})
        assert r.json() == []

        # web /read/ledgers also skips it; /read/ledgers/L1 → 404.
        web = _login_web(client, "o@example.com")
        web_token = web["access_token"]
        r = client.get("/api/v1/read/ledgers", headers={"Authorization": f"Bearer {web_token}"})
        assert r.json() == []
        r = client.get("/api/v1/read/ledgers/L1", headers={"Authorization": f"Bearer {web_token}"})
        assert r.status_code == 404

        # History is preserved (tombstone + original upsert rows both present).
        from sqlalchemy import func, select

        from src.models import SyncChange

        override = app.dependency_overrides[get_db]
        db = next(override())
        try:
            count = db.scalar(
                select(func.count(SyncChange.change_id)).where(
                    SyncChange.entity_type == "ledger_snapshot"
                )
            )
            assert count >= 2, count
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_web_delete_ledger_endpoint() -> None:
    client = _make_client()
    try:
        owner = _register(client, "o@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        _seed_snapshot(client, app_token, device, "L1")

        web = _login_web(client, "o@example.com")
        web_token = web["access_token"]

        # Web deletes via DELETE /write/ledgers/L1
        r = client.delete(
            "/api/v1/write/ledgers/L1",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert r.status_code == 200, r.text
        meta = r.json()
        assert meta["ledger_id"] == "L1"
        assert meta["new_change_id"] > 0

        # Subsequently not visible to either mobile or web.
        r = client.get("/api/v1/sync/ledgers", headers={"Authorization": f"Bearer {app_token}"})
        assert r.json() == []
        r = client.get("/api/v1/read/ledgers", headers={"Authorization": f"Bearer {web_token}"})
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()


def test_soft_delete_does_not_affect_other_users() -> None:
    client = _make_client()
    try:
        a = _register(client, "a@example.com")
        b = _register(client, "b@example.com")
        _seed_snapshot(client, a["access_token"], a["device_id"], "shared-name")
        _seed_snapshot(client, b["access_token"], b["device_id"], "shared-name")

        # A deletes its own ledger.
        now = datetime.now(timezone.utc).isoformat()
        client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {a['access_token']}"},
            json={
                "device_id": a["device_id"],
                "changes": [
                    {
                        "ledger_id": "shared-name",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "shared-name",
                        "action": "delete",
                        "payload": {},
                        "updated_at": now,
                    }
                ],
            },
        )

        # A's list: empty. B's list: still visible.
        ra = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {a['access_token']}"},
        )
        rb = client.get(
            "/api/v1/sync/ledgers",
            headers={"Authorization": f"Bearer {b['access_token']}"},
        )
        assert ra.json() == []
        assert [lg["ledger_id"] for lg in rb.json()] == ["shared-name"]
    finally:
        app.dependency_overrides.clear()
