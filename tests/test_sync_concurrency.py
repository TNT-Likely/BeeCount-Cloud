"""Tests for deterministic LWW, idempotent replay and sequential materialization.

Concurrent Postgres advisory-lock behavior is not exercised here (sqlite
in-memory has no advisory locks); these tests guard the determinism /
no-lost-updates properties that the advisory lock backs up in production.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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
    testing_session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
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


def _seed_snapshot(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    snapshot = (
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
                    "payload": {"content": snapshot},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _push_tx(
    client: TestClient,
    token: str,
    device_id: str,
    ledger_id: str,
    sync_id: str,
    amount: float,
    updated_at: datetime,
) -> dict:
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "transaction",
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "payload": {
                        "syncId": sync_id,
                        "type": "expense",
                        "amount": amount,
                        "happenedAt": updated_at.isoformat(),
                        "categoryName": "Food",
                        "categoryKind": "expense",
                        "accountName": "Cash",
                    },
                    "updated_at": updated_at.isoformat(),
                }
            ],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _read_transactions(client: TestClient, web_token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/transactions?limit=1000",
        headers={"Authorization": f"Bearer {web_token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_lww_device_id_tie_break_is_deterministic() -> None:
    """With the same updated_at, the higher device_id wins — not arrival order."""
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        token_a = owner["access_token"]
        device_a = owner["device_id"]

        _seed_snapshot(client, token_a, device_a, "L1")

        ts = datetime.now(timezone.utc) + timedelta(seconds=1)

        # Device A pushes first with tx amount=100.
        _push_tx(client, token_a, device_a, "L1", "tx1", 100.0, ts)

        # Register a second device for the same user (device B with a
        # lexicographically larger id, enforced below).
        login_b = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "123456",
                "client_type": "app",
                "device_id": "zzz-device-b",
                "device_name": "pytest-b",
                "platform": "app",
            },
        ).json()
        token_b = login_b["access_token"]
        device_b = login_b["device_id"]
        assert device_b > device_a, (device_a, device_b)

        # Device B sends same tx with same timestamp but amount=200.
        # Since device_b > device_a lex, B should win per tuple comparison.
        _push_tx(client, token_b, device_b, "L1", "tx1", 200.0, ts)

        # Now A tries to re-send amount=300 at the same timestamp.
        # A's tuple (ts, device_a) < (ts, device_b) — A must be rejected.
        res_a_again = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "device_id": device_a,
                "changes": [
                    {
                        "ledger_id": "L1",
                        "entity_type": "transaction",
                        "entity_sync_id": "tx1",
                        "action": "upsert",
                        "payload": {
                            "syncId": "tx1",
                            "type": "expense",
                            "amount": 300.0,
                            "happenedAt": ts.isoformat(),
                            "categoryName": "Food",
                            "categoryKind": "expense",
                            "accountName": "Cash",
                        },
                        "updated_at": ts.isoformat(),
                    }
                ],
            },
        )
        assert res_a_again.status_code == 200, res_a_again.text
        body = res_a_again.json()
        assert body["rejected"] == 1, body
        assert body["conflict_count"] == 1, body

        # Web view must show B's amount (200), not A's later attempt (300) or
        # earlier attempt (100).
        web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        ).json()
        txs = [t for t in _read_transactions(client, web["access_token"], "L1") if t["id"] == "tx1"]
        assert len(txs) == 1, txs
        assert float(txs[0]["amount"]) == 200.0, txs[0]
    finally:
        app.dependency_overrides.clear()


def test_lww_idempotent_replay_does_not_duplicate() -> None:
    """Same (device_id, updated_at) replay must be a no-op, not a duplicate insert."""
    from sqlalchemy import func, select

    from src.models import SyncChange

    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        token = owner["access_token"]
        device = owner["device_id"]
        _seed_snapshot(client, token, device, "L1")

        ts = datetime.now(timezone.utc) + timedelta(seconds=5)
        _push_tx(client, token, device, "L1", "tx-idem", 42.0, ts)
        # Replay identical payload.
        body2 = _push_tx(client, token, device, "L1", "tx-idem", 42.0, ts)
        # The endpoint accepts (idempotent), but the underlying SyncChange
        # table must hold exactly one row for this entity.
        assert body2["accepted"] == 1, body2

        # Peek into the DB via the same override session.
        override = app.dependency_overrides[get_db]
        db_gen = override()
        db = next(db_gen)
        try:
            tx_rows = db.scalar(
                select(func.count(SyncChange.change_id)).where(
                    SyncChange.entity_type == "transaction",
                    SyncChange.entity_sync_id == "tx-idem",
                )
            )
            assert tx_rows == 1, tx_rows
        finally:
            try:
                db.close()
            except Exception:
                pass
    finally:
        app.dependency_overrides.clear()


def test_sequential_individual_changes_all_land_in_snapshot() -> None:
    """Sanity: three individual pushes all land in the materialized snapshot.

    Under Postgres this is backed by pg_advisory_xact_lock; under sqlite the
    helper is a no-op and serialization is by the single-threaded test client.
    """
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        token = owner["access_token"]
        device = owner["device_id"]
        _seed_snapshot(client, token, device, "L1")

        base = datetime.now(timezone.utc)
        for i, sync_id in enumerate(["a", "b", "c"]):
            _push_tx(client, token, device, "L1", sync_id, 10.0 * (i + 1), base + timedelta(seconds=i + 1))

        web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        ).json()
        txs = _read_transactions(client, web["access_token"], "L1")
        sync_ids = sorted(t["id"] for t in txs)
        assert sync_ids == ["a", "b", "c"], txs
    finally:
        app.dependency_overrides.clear()
