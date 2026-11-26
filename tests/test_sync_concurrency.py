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


def test_operation_receipts_distinguish_same_second_writes_replay_and_batch_rejection() -> None:
    client = _make_client()
    try:
        owner = _register(client, "receipts@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_snapshot(client, token, device, "L")
        now = datetime.now(timezone.utc)
        headers = {"Authorization": f"Bearer {token}"}

        def event(op, amount, instant=now, **fields):
            return {
                "operation_id": op, "ledger_id": "L", "entity_type": "transaction",
                "entity_sync_id": "T", "action": "upsert", "updated_at": instant.isoformat(),
                "payload": {"type": "expense", "amount": amount, "happenedAt": now.isoformat(), **fields},
            }

        def send(*events):
            response = client.post("/api/v1/sync/push", headers=headers, json={
                "device_id": device, "changes": events, "return_results": True,
            })
            assert response.status_code == 200, response.text
            return response.json()

        first = event("first", 12, note="keep remote note")
        second = {**event("second", 18, note="stale local note"), "changed_fields": ["amount"]}
        batch = send(first, second)
        assert (batch["accepted"], batch["rejected"]) == (2, 0)
        a, b = batch["results"]
        assert b["change_id"] > a["change_id"]
        assert b["winner"]["payload"]["amount"] == 18
        assert b["winner"]["payload"]["note"] == "keep remote note"

        mixed = send(first, event("stale", 7, now - timedelta(seconds=1)), {
            **event("another", 42), "entity_sync_id": "U",
        }, {**event("mismatched-reuse", 5), "ledger_id": None})
        replay, stale, independent, missing = mixed["results"]
        assert (mixed["accepted"], mixed["rejected"], mixed["conflict_count"]) == (2, 2, 1)
        assert (replay["status"], replay["change_id"]) == ("accepted", a["change_id"])
        assert replay["winner"]["change_id"] == stale["winner"]["change_id"] == b["change_id"]
        assert stale["status"] == "conflict" and stale["winner"]["payload"]["amount"] == 18
        assert independent["status"] == "accepted"
        assert missing["status"] == "rejected" and missing["winner"] is None
        assert send(first)["results"][0]["winner"]["payload"]["note"] == "keep remote note"

        tombstone = {**event("delete", 0), "action": "delete", "payload": {}}
        deleted = send(tombstone)["results"][0]["change_id"]
        late_replay = send(first)["results"][0]
        assert late_replay["status"] == "accepted" and late_replay["change_id"] == a["change_id"]
        assert late_replay["winner"]["change_id"] == deleted
        assert late_replay["winner"]["action"] == "delete"
        stale_after_delete = send(event("offline-edit", 27))["results"][0]
        assert stale_after_delete["status"] == "conflict"
        assert stale_after_delete["winner"]["change_id"] == deleted
        wrong_version = {**event("stale-restore", 27), "restore_after_change_id": a["change_id"]}
        assert send(wrong_version)["results"][0]["reason"] == "restore_version_mismatch"
        restored = {**event("newer-edit", 27), "changed_fields": ["amount"], "restore_after_change_id": deleted}
        result = send(restored)["results"][0]["winner"]
        assert result["payload"]["type"] == "expense"
        assert result["payload"]["amount"] == 27 and result["payload"]["happenedAt"]
    finally:
        app.dependency_overrides.clear()
