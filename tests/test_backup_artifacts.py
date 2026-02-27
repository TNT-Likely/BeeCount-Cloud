import json
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import get_settings
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


def test_backup_upload_db_snapshot_and_list_flow() -> None:
    client = _make_client()
    settings = get_settings()
    original_backup_dir = settings.backup_storage_dir
    original_upload_limit = settings.backup_max_upload_bytes
    try:
        with TemporaryDirectory() as backup_dir:
            settings.backup_storage_dir = backup_dir
            settings.backup_max_upload_bytes = 1024 * 1024

            owner = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "owner@example.com",
                    "password": "123456",
                    "client_type": "app",
                    "device_name": "pytest-app",
                    "platform": "ios",
                },
            )
            assert owner.status_code == 200
            owner_payload = owner.json()
            owner_app_token = owner_payload["access_token"]
            owner_device = owner_payload["device_id"]

            init_snapshot = client.post(
                "/api/v1/sync/push",
                headers={"Authorization": f"Bearer {owner_app_token}"},
                json={
                    "device_id": owner_device,
                    "changes": [
                        {
                            "ledger_id": "ledger-backup",
                            "entity_type": "ledger_snapshot",
                            "entity_sync_id": "ledger-backup",
                            "action": "upsert",
                            "payload": {
                                "content": (
                                    '{"ledgerName":"Ledger Backup","currency":"CNY","count":0,'
                                    '"items":[],"accounts":[],"categories":[],"tags":[]}'
                                )
                            },
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ],
                },
            )
            assert init_snapshot.status_code == 200

            upload_db = client.post(
                "/api/v1/admin/backups/upload-db",
                headers={"Authorization": f"Bearer {owner_app_token}"},
                data={
                    "ledger_id": "ledger-backup",
                    "note": "nightly-db",
                    "metadata": json.dumps({"channel": "mobile"}),
                },
                files={"file": ("ledger-backup.sqlite3", b"sqlite-payload", "application/octet-stream")},
            )
            assert upload_db.status_code == 200
            db_upload_payload = upload_db.json()
            assert db_upload_payload["kind"] == "db"
            assert db_upload_payload["snapshot_id"] is None
            assert db_upload_payload["note"] == "nightly-db"
            assert db_upload_payload["size"] == len(b"sqlite-payload")

            upload_snapshot = client.post(
                "/api/v1/admin/backups/upload-snapshot",
                headers={"Authorization": f"Bearer {owner_app_token}"},
                json={
                    "ledger_id": "ledger-backup",
                    "payload": {
                        "ledgerName": "Ledger Backup",
                        "currency": "CNY",
                        "count": 1,
                        "items": [],
                        "accounts": [],
                        "categories": [],
                        "tags": [],
                    },
                    "note": "nightly-snapshot",
                    "metadata": {"channel": "mobile"},
                },
            )
            assert upload_snapshot.status_code == 200
            snapshot_upload_payload = upload_snapshot.json()
            assert snapshot_upload_payload["kind"] == "snapshot"
            assert snapshot_upload_payload["snapshot_id"]
            assert snapshot_upload_payload["note"] == "nightly-snapshot"

            list_all = client.get(
                "/api/v1/admin/backups/artifacts",
                headers={"Authorization": f"Bearer {owner_app_token}"},
                params={"ledger_id": "ledger-backup"},
            )
            assert list_all.status_code == 200
            items = list_all.json()
            assert len(items) == 2
            assert {item["kind"] for item in items} == {"db", "snapshot"}

            owner_web = client.post(
                "/api/v1/auth/login",
                json={
                    "email": "owner@example.com",
                    "password": "123456",
                    "client_type": "web",
                    "device_name": "pytest-web",
                    "platform": "web",
                },
            )
            assert owner_web.status_code == 200
            owner_web_token = owner_web.json()["access_token"]

            restore = client.post(
                "/api/v1/admin/backups/restore",
                headers={"Authorization": f"Bearer {owner_web_token}"},
                json={"snapshot_id": snapshot_upload_payload["snapshot_id"]},
            )
            assert restore.status_code == 200
            assert restore.json()["restored"] is True
    finally:
        settings.backup_storage_dir = original_backup_dir
        settings.backup_max_upload_bytes = original_upload_limit
        app.dependency_overrides.clear()
