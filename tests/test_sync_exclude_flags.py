"""交易标记(exclude_from_stats / exclude_from_budget)同步契约 — push 侧:

- mobile push 的 transaction upsert payload 带 camelCase
  `excludeFromStats` / `excludeFromBudget` → 落 read_tx_projection 两列
- partial-update(后续 push 不带该 key)时保持原值(merge 契约,D6 核心),
  不得被抹成 False

read 端过滤(收支分析 / 预算用量)与 schema/写端契约由后续任务追加。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadTxProjection


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


def _tx_row(TS, external_ledger_id: str, sync_id: str) -> ReadTxProjection:
    with TS() as db:
        internal_id = db.scalar(
            select(Ledger.id).where(Ledger.external_id == external_ledger_id)
        )
        assert internal_id is not None
        row = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == internal_id,
                ReadTxProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


def test_push_transaction_persists_exclude_flags() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "excl1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_EXCL1")
        _push_tx(
            client, token, device, "L_EXCL1",
            {
                "syncId": "tx-flag-1",
                "type": "expense",
                "amount": 500.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "excludeFromStats": True,
                "excludeFromBudget": True,
            },
        )
        row = _tx_row(TS, "L_EXCL1", "tx-flag-1")
        assert row.exclude_from_stats is True
        assert row.exclude_from_budget is True
    finally:
        app.dependency_overrides.clear()


def test_partial_update_preserves_exclude_flags() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "excl2@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_EXCL2")
        # 先推一条带 excludeFromStats 的交易
        _push_tx(
            client, token, device, "L_EXCL2",
            {
                "syncId": "tx-flag-2",
                "type": "expense",
                "amount": 500.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "excludeFromStats": True,
            },
        )
        # 再推一条只改金额、不带 excludeFromStats 键的 partial update
        _push_tx(
            client, token, device, "L_EXCL2",
            {
                "syncId": "tx-flag-2",
                "type": "expense",
                "amount": 600.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
            },
        )
        row = _tx_row(TS, "L_EXCL2", "tx-flag-2")
        assert row.amount == 600.0
        # 标记必须保留,不被抹成 False(D6 核心)
        assert row.exclude_from_stats is True
    finally:
        app.dependency_overrides.clear()
