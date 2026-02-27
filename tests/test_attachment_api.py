import os

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


def _auth(client: TestClient, email: str, password: str, client_type: str) -> dict:
    endpoint = "/api/v1/auth/login" if email.endswith("@existing.com") else "/api/v1/auth/register"
    payload = {
        "email": email,
        "password": password,
        "client_type": client_type,
        "device_name": f"pytest-{client_type}",
        "platform": client_type,
    }
    if endpoint.endswith("/login"):
        payload["email"] = email.replace("@existing.com", "@example.com")
    res = client.post(endpoint, json=payload)
    assert res.status_code == 200
    return res.json()


def test_attachment_upload_batch_exists_and_download_acl(tmp_path) -> None:
    old_dir = os.environ.get("ATTACHMENT_STORAGE_DIR")
    os.environ["ATTACHMENT_STORAGE_DIR"] = str(tmp_path)
    get_settings.cache_clear()

    client = _make_client()
    try:
        owner_app = _auth(client, "owner@example.com", "123456", "app")
        owner_app_token = owner_app["access_token"]
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

        create_ledger = client.post(
            "/api/v1/write/ledgers",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"ledger_name": "Attachment Ledger", "currency": "CNY"},
        )
        assert create_ledger.status_code == 200
        ledger_id = create_ledger.json()["ledger_id"]

        upload = client.post(
            "/api/v1/attachments/upload",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            data={"ledger_id": ledger_id},
            files={"file": ("note.txt", b"hello-beecount", "text/plain")},
        )
        assert upload.status_code == 200
        upload_payload = upload.json()
        assert upload_payload["ledger_id"] == ledger_id
        assert upload_payload["size"] == len(b"hello-beecount")
        assert len(upload_payload["sha256"]) == 64

        exists = client.post(
            "/api/v1/attachments/batch-exists",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={"ledger_id": ledger_id, "sha256_list": [upload_payload["sha256"], "deadbeef"]},
        )
        assert exists.status_code == 200
        items = {row["sha256"]: row for row in exists.json()["items"]}
        assert items[upload_payload["sha256"]]["exists"] is True
        assert items[upload_payload["sha256"]]["file_id"] == upload_payload["file_id"]
        assert items["deadbeef"]["exists"] is False

        download = client.get(
            f"/api/v1/attachments/{upload_payload['file_id']}",
            headers={"Authorization": f"Bearer {owner_app_token}"},
        )
        assert download.status_code == 200
        assert download.content == b"hello-beecount"

        invite = client.post(
            "/api/v1/share/invite",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={"ledger_id": ledger_id, "role": "viewer", "max_uses": 1},
        )
        assert invite.status_code == 200
        viewer_app = _auth(client, "viewer@example.com", "123456", "app")
        viewer_token = viewer_app["access_token"]
        join = client.post(
            "/api/v1/share/join",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"invite_code": invite.json()["invite_code"]},
        )
        assert join.status_code == 200

        viewer_download = client.get(
            f"/api/v1/attachments/{upload_payload['file_id']}",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert viewer_download.status_code == 200
        assert viewer_download.content == b"hello-beecount"

        viewer_upload = client.post(
            "/api/v1/attachments/upload",
            headers={"Authorization": f"Bearer {viewer_token}"},
            data={"ledger_id": ledger_id},
            files={"file": ("note2.txt", b"viewer-upload", "text/plain")},
        )
        assert viewer_upload.status_code == 403
    finally:
        app.dependency_overrides.clear()
        if old_dir is None:
            os.environ.pop("ATTACHMENT_STORAGE_DIR", None)
        else:
            os.environ["ATTACHMENT_STORAGE_DIR"] = old_dir
        get_settings.cache_clear()
