"""共享账本交易只能引用 Owner 标签。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    LedgerMember,
    ReadTxProjection,
    SyncChange,
    UserTagProjection,
)


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), session_factory


def _register(client: TestClient, email: str, device_id: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": "app",
            "device_name": f"pytest-{device_id}",
            "platform": "test",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _login_web(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "web-device",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _seed_shared_ledger(
    session_factory,
    *,
    owner_id: str,
    editor_id: str,
    external_id: str,
) -> str:
    with session_factory() as db:
        ledger = Ledger(
            user_id=owner_id,
            external_id=external_id,
            name="Shared",
        )
        db.add(ledger)
        db.flush()
        db.add_all(
            [
                LedgerMember(
                    ledger_id=ledger.id,
                    user_id=owner_id,
                    role="owner",
                ),
                LedgerMember(
                    ledger_id=ledger.id,
                    user_id=editor_id,
                    role="editor",
                    invited_by=owner_id,
                ),
                UserTagProjection(
                    user_id=owner_id,
                    sync_id="owner-tag",
                    name="Owner Tag",
                    source_change_id=1,
                ),
                UserTagProjection(
                    user_id=editor_id,
                    sync_id="editor-tag",
                    name="Editor Tag",
                    source_change_id=1,
                ),
            ]
        )
        db.commit()
        return ledger.id


def _push_transaction(
    client: TestClient,
    *,
    token: str,
    device_id: str,
    ledger_id: str,
    tx_id: str,
    payload: dict,
) -> dict:
    response = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "transaction",
                    "entity_sync_id": tx_id,
                    "action": "upsert",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "syncId": tx_id,
                        "type": "expense",
                        "amount": 10,
                        "happenedAt": datetime.now(timezone.utc).isoformat(),
                        **payload,
                    },
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_mobile_shared_tx_rejects_editor_tags_and_canonicalizes_owner_tag() -> None:
    client, session_factory = _make_client()
    try:
        owner = _register(client, "owner-mobile-tags@example.com", "owner-app")
        editor = _register(client, "editor-mobile-tags@example.com", "editor-app")
        ledger_internal_id = _seed_shared_ledger(
            session_factory,
            owner_id=owner["user"]["id"],
            editor_id=editor["user"]["id"],
            external_id="shared-mobile-tags",
        )

        unknown = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-unknown-name",
            payload={"tags": "Editor Only"},
        )
        assert unknown["accepted"] == 0
        assert unknown["rejected"] == 1

        foreign_id = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-editor-id",
            payload={"tags": "Editor Tag", "tagIds": ["editor-tag"]},
        )
        assert foreign_id["accepted"] == 0
        assert foreign_id["rejected"] == 1

        valid = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-owner-tag",
            payload={"tags": "伪造名称", "tagIds": ["owner-tag"]},
        )
        assert valid["accepted"] == 1
        assert valid["rejected"] == 0

        with session_factory() as db:
            invalid_changes = db.scalars(
                select(SyncChange).where(
                    SyncChange.entity_sync_id.in_(
                        ["tx-unknown-name", "tx-editor-id"]
                    )
                )
            ).all()
            assert invalid_changes == []
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_internal_id,
                    ReadTxProjection.sync_id == "tx-owner-tag",
                )
            )
            assert tx is not None
            assert tx.tags_csv == "Owner Tag"
            assert json.loads(tx.tag_sync_ids_json or "[]") == ["owner-tag"]

        # 个人账本保持老协议兼容：只有 name、没有 tagIds 的交易仍可接受。
        personal = _push_transaction(
            client,
            token=owner["access_token"],
            device_id=owner["device_id"],
            ledger_id="personal-mobile-tags",
            tx_id="tx-personal-legacy",
            payload={"tags": "Legacy Personal Tag"},
        )
        assert personal["accepted"] == 1
        assert personal["rejected"] == 0
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_web_shared_tx_resolves_known_owner_name_and_rejects_unknown() -> None:
    client, session_factory = _make_client()
    try:
        owner = _register(client, "owner-web-tags@example.com", "owner-app")
        editor = _register(client, "editor-web-tags@example.com", "editor-app")
        ledger_internal_id = _seed_shared_ledger(
            session_factory,
            owner_id=owner["user"]["id"],
            editor_id=editor["user"]["id"],
            external_id="shared-web-tags",
        )
        web_token = _login_web(client, "editor-web-tags@example.com")
        headers = {
            "Authorization": f"Bearer {web_token}",
            "X-Device-ID": "web-device",
        }
        base_payload = {
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": 12,
            "happened_at": datetime.now(timezone.utc).isoformat(),
        }

        rejected = client.post(
            "/api/v1/write/ledgers/shared-web-tags/transactions",
            headers=headers,
            json={**base_payload, "tags": ["Editor Only"]},
        )
        assert rejected.status_code == 400, rejected.text
        assert rejected.json()["error_code"] == "SHARED_TX_TAG_NOT_OWNER"

        accepted = client.post(
            "/api/v1/write/ledgers/shared-web-tags/transactions",
            headers=headers,
            json={**base_payload, "tags": ["Owner Tag"]},
        )
        assert accepted.status_code == 200, accepted.text
        tx_id = accepted.json()["entity_id"]

        with session_factory() as db:
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_internal_id,
                    ReadTxProjection.sync_id == tx_id,
                )
            )
            assert tx is not None
            assert tx.tags_csv == "Owner Tag"
            assert json.loads(tx.tag_sync_ids_json or "[]") == ["owner-tag"]
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_editor_batch_cannot_create_unknown_shared_tag() -> None:
    client, session_factory = _make_client()
    try:
        owner = _register(client, "owner-batch-tags@example.com", "owner-app")
        editor = _register(client, "editor-batch-tags@example.com", "editor-app")
        _seed_shared_ledger(
            session_factory,
            owner_id=owner["user"]["id"],
            editor_id=editor["user"]["id"],
            external_id="shared-batch-tags",
        )
        web_token = _login_web(client, "editor-batch-tags@example.com")
        headers = {
            "Authorization": f"Bearer {web_token}",
            "X-Device-ID": "web-device",
        }

        response = client.post(
            "/api/v1/write/ledgers/shared-batch-tags/transactions/batch",
            headers=headers,
            json={
                "base_change_id": 0,
                "auto_ai_tag": False,
                "transactions": [
                    {
                        "tx_type": "expense",
                        "amount": 8,
                        "happened_at": datetime.now(timezone.utc).isoformat(),
                        "tags": ["Editor Only"],
                    }
                ],
            },
        )
        assert response.status_code == 400, response.text
        assert response.json()["error_code"] == "SHARED_TX_TAG_NOT_OWNER"

        with session_factory() as db:
            editor_tag = db.scalar(
                select(UserTagProjection).where(
                    UserTagProjection.user_id == editor["user"]["id"],
                    UserTagProjection.name == "Editor Only",
                )
            )
            assert editor_tag is None
    finally:
        client.close()
        app.dependency_overrides.clear()
