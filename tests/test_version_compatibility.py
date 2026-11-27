from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import version as version_mod
from src.database import Base, get_db
from src.main import app
from src.models import Device


def _make_client() -> tuple[TestClient, sessionmaker]:
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
    return TestClient(app), testing_session


def _register_device(
    client: TestClient,
    email: str,
    *,
    platform: str = "ios",
    client_type: str = "app",
    app_version: str | None = "4.0.1+10",
) -> dict:
    payload: dict[str, object] = {
        "email": email,
        "password": "password123",
        "client_type": client_type,
        "device_name": f"{email}-device",
        "platform": platform,
    }
    if app_version is not None:
        payload["app_version"] = app_version
    res = client.post("/api/v1/auth/register", json=payload)
    assert res.status_code == 200, res.text
    return res.json()


def test_parse_semver_and_comparison() -> None:
    assert version_mod.parse_semver("4.1.0") == (4, 1, 0)
    assert version_mod.parse_semver("v4.1.0+123") == (4, 1, 0)
    assert version_mod.parse_semver("4.1.0 (123)") == (4, 1, 0)
    assert version_mod.parse_semver("4.1.0-beta.2") == (4, 1, 0)
    assert version_mod.parse_semver("web-1.11.0") is None
    assert version_mod.parse_semver("") is None
    assert version_mod.parse_semver(None) is None

    assert version_mod.is_version_below("3.9.9", "4.0.0") is True
    assert version_mod.is_version_below("0.0.1", "4.0.0") is False
    assert version_mod.is_version_below("4.0.0+1", "4.0.0") is False
    assert version_mod.is_version_below("4.1.0", "4.0.0") is False
    assert version_mod.is_version_below("web-1.0.0", "4.0.0") is False


def test_public_version_exposes_app_compatibility_policy(monkeypatch) -> None:
    client, _ = _make_client()
    try:
        monkeypatch.setattr(version_mod, "__version__", "1.11.0")
        monkeypatch.setattr(version_mod, "MIN_APP_VERSION", "4.0.0")
        monkeypatch.setattr(version_mod, "RECOMMENDED_APP_VERSION", "4.1.0")
        res = client.get("/api/v1/version")
        assert res.status_code == 200
        body = res.json()
        assert body["version"] == "1.11.0"
        assert body["min_app_version"] == "4.0.0"
        assert body["recommended_app_version"] == "4.1.0"
        assert "recurring_rules_v1" in body["capabilities"]
    finally:
        app.dependency_overrides.clear()


def test_sync_refreshes_device_app_version_from_header() -> None:
    client, testing_session = _make_client()
    try:
        auth = _register_device(client, "upgrade@example.com", app_version="4.0.1")
        token = auth["access_token"]
        device_id = auth["device_id"]

        res = client.get(
            "/api/v1/sync/pull",
            params={"since": 0, "device_id": device_id},
            headers={
                "Authorization": f"Bearer {token}",
                "X-BeeCount-App-Version": "4.1.0+410",
            },
        )
        assert res.status_code == 200, res.text

        db = testing_session()
        try:
            device = db.scalar(select(Device).where(Device.id == device_id))
            assert device is not None
            assert device.app_version == "4.1.0+410"
        finally:
            db.close()

        push_res = client.post(
            "/api/v1/sync/push",
            json={"device_id": device_id, "changes": []},
            headers={
                "Authorization": f"Bearer {token}",
                "X-BeeCount-App-Version": "4.1.1",
            },
        )
        assert push_res.status_code == 200, push_res.text

        db = testing_session()
        try:
            device = db.scalar(select(Device).where(Device.id == device_id))
            assert device is not None
            assert device.app_version == "4.1.1"
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_sync_rejects_app_below_min_version_with_426(monkeypatch) -> None:
    client, testing_session = _make_client()
    try:
        monkeypatch.setattr(version_mod, "__version__", "1.12.0")
        monkeypatch.setattr(version_mod, "MIN_APP_VERSION", "4.1.0")
        monkeypatch.setattr(version_mod, "RECOMMENDED_APP_VERSION", "4.2.0")

        old_mobile = _register_device(client, "old-app@example.com", app_version="4.0.1")
        token = old_mobile["access_token"]
        device_id = old_mobile["device_id"]

        # 1. Header below min -> 426 on push, pull, and full.
        push_res = client.post(
            "/api/v1/sync/push",
            json={"device_id": device_id, "changes": []},
            headers={
                "Authorization": f"Bearer {token}",
                "X-BeeCount-App-Version": "4.0.1",
            },
        )
        assert push_res.status_code == 426
        body = push_res.json()
        assert body["error"]["code"] == "APP_UPGRADE_REQUIRED"
        assert body["code"] == "app_upgrade_required"
        assert body["min_app_version"] == "4.1.0"
        assert body["recommended_app_version"] == "4.2.0"
        assert body["server_version"] == "1.12.0"
        assert body["client_app_version"] == "4.0.1"

        # 2. Even without header, stored mobile device.app_version below min -> 426.
        pull_res = client.get(
            "/api/v1/sync/pull",
            params={"since": 0, "device_id": device_id},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert pull_res.status_code == 426

        full_res = client.get(
            "/api/v1/sync/full",
            params={"ledger_id": "ledger-1"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-BeeCount-App-Version": "4.0.1",
            },
        )
        assert full_res.status_code == 426

        # 3. Once the same device upgrades and sends >= min header, sync succeeds
        #    and Device.app_version is updated.
        upgraded = client.post(
            "/api/v1/sync/push",
            json={"device_id": device_id, "changes": []},
            headers={
                "Authorization": f"Bearer {token}",
                "X-BeeCount-App-Version": "4.1.0",
            },
        )
        assert upgraded.status_code == 200

        db = testing_session()
        try:
            device = db.scalar(select(Device).where(Device.id == device_id))
            assert device is not None
            assert device.app_version == "4.1.0"
        finally:
            db.close()

        # 4. Web sessions (platform=web) are never blocked by mobile min_app_version.
        web = _register_device(
            client,
            "web-user@example.com",
            platform="web",
            client_type="web",
            app_version="1.12.0",
        )
        web_pull = client.get(
            "/api/v1/sync/pull",
            params={"since": 0, "device_id": web["device_id"]},
            headers={"Authorization": f"Bearer {web['access_token']}"},
        )
        assert web_pull.status_code == 200
    finally:
        app.dependency_overrides.clear()
