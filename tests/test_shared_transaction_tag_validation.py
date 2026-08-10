"""共享账本交易只能引用 Owner 标签。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    AttachmentFile,
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
    updated_at: datetime | None = None,
    expected_status: int = 200,
) -> dict:
    change_time = updated_at or datetime.now(timezone.utc)
    response = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [_transaction_change(ledger_id, tx_id, payload, change_time)],
        },
    )
    assert response.status_code == expected_status, response.text
    return response.json()


def _transaction_change(
    ledger_id: str,
    tx_id: str,
    payload: dict,
    updated_at: datetime | None = None,
) -> dict:
    return {
        "ledger_id": ledger_id,
        "entity_type": "transaction",
        "entity_sync_id": tx_id,
        "action": "upsert",
        "updated_at": (updated_at or datetime.now(timezone.utc)).isoformat(),
        "payload": {
            "syncId": tx_id,
            "type": "expense",
            "amount": 10,
            "happenedAt": datetime.now(timezone.utc).isoformat(),
            **payload,
        },
    }


def test_mobile_shared_tx_rejects_editor_tags_and_canonicalizes_owner_tag(
    tmp_path: Path,
) -> None:
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

        # 构造一个已有附件的交易。后面的混合批先移除附件、再遇到非法标签；
        # 整批 400 时除了 DB 行必须 rollback，物理文件也不能提前被 unlink。
        attachment_path = tmp_path / "mixed-rollback.bin"
        attachment_path.write_bytes(b"keep after rollback")
        with session_factory() as db:
            db.add_all(
                [
                    AttachmentFile(
                        id="mixed-rollback-file",
                        ledger_id=ledger_internal_id,
                        user_id=owner["user"]["id"],
                        sha256="mixed-rollback-sha",
                        size_bytes=19,
                        mime_type="application/octet-stream",
                        file_name="mixed-rollback.bin",
                        storage_path=str(attachment_path),
                    ),
                    ReadTxProjection(
                        ledger_id=ledger_internal_id,
                        sync_id="tx-mixed-attachment",
                        user_id=owner["user"]["id"],
                        tx_type="expense",
                        amount=10,
                        happened_at=datetime.now(timezone.utc),
                        attachments_json=json.dumps(
                            [
                                {
                                    "fileName": "mixed-rollback.bin",
                                    "cloudFileId": "mixed-rollback-file",
                                }
                            ]
                        ),
                        tx_index=0,
                        source_change_id=1,
                    ),
                ]
            )
            db.commit()

        unknown = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-unknown-name",
            payload={"tags": "Editor Only"},
            expected_status=400,
        )
        assert unknown["error_code"] == "SHARED_TX_TAG_NOT_OWNER"

        foreign_id = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-editor-id",
            payload={"tags": "Editor Tag", "tagIds": ["editor-tag"]},
            expected_status=400,
        )
        assert foreign_id["error_code"] == "SHARED_TX_TAG_NOT_OWNER"

        # 一批中前一条已 flush、后一条标签非法时必须整体 400 + rollback，
        # 防止客户端保留整批 local changes、服务端却只落一半。
        mixed = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
            json={
                "device_id": editor["device_id"],
                "changes": [
                    _transaction_change(
                        "shared-mobile-tags",
                        "tx-mixed-attachment",
                        {
                            "tags": "Owner Tag",
                            "tagIds": ["owner-tag"],
                            "attachments": [],
                        },
                    ),
                    _transaction_change(
                        "shared-mobile-tags",
                        "tx-mixed-invalid",
                        {"tags": "Editor Only"},
                    ),
                ],
            },
        )
        assert mixed.status_code == 400, mixed.text
        assert mixed.json()["error_code"] == "SHARED_TX_TAG_NOT_OWNER"

        replay_time = datetime.now(timezone.utc)
        valid = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-owner-tag",
            payload={"tags": "伪造名称", "tagIds": ["owner-tag"]},
            updated_at=replay_time,
        )
        assert valid["accepted"] == 1
        assert valid["rejected"] == 0

        with session_factory() as db:
            invalid_changes = db.scalars(
                select(SyncChange).where(
                    SyncChange.entity_sync_id.in_(
                        [
                            "tx-unknown-name",
                            "tx-editor-id",
                            "tx-mixed-attachment",
                            "tx-mixed-invalid",
                        ]
                    )
                )
            ).all()
            assert invalid_changes == []
            rolled_back_tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_internal_id,
                    ReadTxProjection.sync_id == "tx-mixed-attachment",
                )
            )
            assert rolled_back_tx is not None
            assert json.loads(rolled_back_tx.attachments_json or "[]") == [
                {
                    "fileName": "mixed-rollback.bin",
                    "cloudFileId": "mixed-rollback-file",
                }
            ]
            assert db.get(AttachmentFile, "mixed-rollback-file") is not None
            assert attachment_path.exists()
            tx = db.scalar(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == ledger_internal_id,
                    ReadTxProjection.sync_id == "tx-owner-tag",
                )
            )
            assert tx is not None
            assert tx.tags_csv == "Owner Tag"
            assert json.loads(tx.tag_sync_ids_json or "[]") == ["owner-tag"]

            # 标签后来被删除时，相同 device/timestamp 的重放仍应先命中 LWW
            # equality，不能重新校验已经接受过的旧 payload。
            owner_tag = db.scalar(
                select(UserTagProjection).where(
                    UserTagProjection.user_id == owner["user"]["id"],
                    UserTagProjection.sync_id == "owner-tag",
                )
            )
            assert owner_tag is not None
            db.delete(owner_tag)
            db.commit()

        replay = _push_transaction(
            client,
            token=editor["access_token"],
            device_id=editor["device_id"],
            ledger_id="shared-mobile-tags",
            tx_id="tx-owner-tag",
            payload={"tags": "Owner Tag", "tagIds": ["owner-tag"]},
            updated_at=replay_time,
        )
        assert replay["accepted"] == 1
        assert replay["rejected"] == 0

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

            db.add(
                UserTagProjection(
                    user_id=owner["user"]["id"],
                    sync_id="owner-tag-duplicate",
                    name="Owner Tag",
                    source_change_id=2,
                )
            )
            db.commit()

        ambiguous = client.post(
            "/api/v1/write/ledgers/shared-web-tags/transactions",
            headers=headers,
            json={**base_payload, "tags": ["Owner Tag"]},
        )
        assert ambiguous.status_code == 400, ambiguous.text
        assert ambiguous.json()["error_code"] == "SHARED_TX_TAG_AMBIGUOUS"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_editor_batch_cannot_create_unknown_shared_tag() -> None:
    from src.services.ai.image_cache import clear_cache, peek_image, store_image

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

        image_id = store_image(
            image_bytes=b"not-consumed-on-tag-error",
            mime_type="image/png",
            user_id=editor["user"]["id"],
        )
        response = client.post(
            "/api/v1/write/ledgers/shared-batch-tags/transactions/batch",
            headers=headers,
            json={
                "base_change_id": 0,
                "auto_ai_tag": False,
                "attach_image_id": image_id,
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
        assert peek_image(image_id=image_id, user_id=editor["user"]["id"]) is not None

        with session_factory() as db:
            editor_tag = db.scalar(
                select(UserTagProjection).where(
                    UserTagProjection.user_id == editor["user"]["id"],
                    UserTagProjection.name == "Editor Only",
                )
            )
            assert editor_tag is None

            db.add(
                UserTagProjection(
                    user_id=owner["user"]["id"],
                    sync_id="owner-tag-duplicate",
                    name="Owner Tag",
                    source_change_id=2,
                )
            )
            db.commit()

        ambiguous = client.post(
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
                        "tags": ["Owner Tag"],
                    }
                ],
            },
        )
        assert ambiguous.status_code == 400, ambiguous.text
        assert ambiguous.json()["error_code"] == "SHARED_TX_TAG_AMBIGUOUS"
    finally:
        clear_cache()
        client.close()
        app.dependency_overrides.clear()


def test_editor_import_rejects_unknown_shared_tag() -> None:
    client, session_factory = _make_client()
    try:
        owner = _register(client, "owner-import-tags@example.com", "owner-app")
        editor = _register(client, "editor-import-tags@example.com", "editor-app")
        _seed_shared_ledger(
            session_factory,
            owner_id=owner["user"]["id"],
            editor_id=editor["user"]["id"],
            external_id="shared-import-tags",
        )
        web_token = _login_web(client, "editor-import-tags@example.com")
        headers = {"Authorization": f"Bearer {web_token}"}
        csv_text = (
            "Type,Category,Subcategory,Amount,Account,From Account,To Account,"
            "Note,Time,Tags,Attachments\n"
            "Expense,,,8,,,,imported,2026-08-09 12:00:00,Editor Only,\n"
        )
        upload = client.post(
            "/api/v1/import/upload",
            headers=headers,
            files={"file": ("shared.csv", csv_text.encode(), "text/csv")},
            data={"target_ledger_id": "shared-import-tags"},
        )
        assert upload.status_code == 200, upload.text

        execute = client.post(
            f"/api/v1/import/{upload.json()['import_token']}/execute",
            headers=headers,
        )
        assert execute.status_code == 200, execute.text
        assert '"code": "SHARED_TX_TAG_NOT_OWNER"' in execute.text

        with session_factory() as db:
            txs = db.scalars(
                select(ReadTxProjection).where(
                    ReadTxProjection.ledger_id == db.scalar(
                        select(Ledger.id).where(
                            Ledger.external_id == "shared-import-tags"
                        )
                    )
                )
            ).all()
            assert txs == []
            leaked_tag = db.scalar(
                select(UserTagProjection).where(
                    UserTagProjection.user_id == editor["user"]["id"],
                    UserTagProjection.name == "Editor Only",
                )
            )
            assert leaked_tag is None
    finally:
        client.close()
        app.dependency_overrides.clear()
