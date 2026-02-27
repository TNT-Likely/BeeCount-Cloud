from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User


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


def _register_web(client: TestClient, email: str, *, app_version: str = "1.0.0") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": f"{email}-device",
            "platform": "web",
            "app_version": app_version,
            "os_version": "macOS",
            "device_model": "MacBook",
        },
    )
    assert res.status_code == 200
    return res.json()


def test_admin_can_create_user_and_non_admin_cannot() -> None:
    client, testing_session = _make_client()
    try:
        admin_auth = _register_web(client, "admin@example.com")
        admin_token = admin_auth["access_token"]

        db = testing_session()
        try:
            admin_user = db.scalar(select(User).where(User.id == admin_auth["user"]["id"]))
            assert admin_user is not None
            admin_user.is_admin = True
            db.add(admin_user)
            db.commit()
        finally:
            db.close()

        created = client.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "email": "created@example.com",
                "password": "123456",
                "is_admin": False,
                "is_enabled": True,
            },
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["email"] == "created@example.com"
        assert payload["is_admin"] is False
        assert payload["is_enabled"] is True

        created_login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "created@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "created-device",
                "platform": "web",
            },
        )
        assert created_login.status_code == 200

        member_auth = _register_web(client, "member@example.com")
        member_token = member_auth["access_token"]
        denied = client.post(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"email": "x@example.com", "password": "123456"},
        )
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_devices_returns_all_and_non_admin_only_self() -> None:
    client, testing_session = _make_client()
    try:
        admin_auth = _register_web(client, "admin@example.com", app_version="2.0.0")
        admin_token = admin_auth["access_token"]

        db = testing_session()
        try:
            admin_user = db.scalar(select(User).where(User.id == admin_auth["user"]["id"]))
            assert admin_user is not None
            admin_user.is_admin = True
            db.add(admin_user)
            db.commit()
        finally:
            db.close()

        member_auth = _register_web(client, "member@example.com", app_version="3.1.0")
        member_token = member_auth["access_token"]
        member_login_again = client.post(
            "/api/v1/auth/login",
            json={
                "email": "member@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "member@example.com-device",
                "platform": "web",
                "app_version": "3.1.0",
                "os_version": "macOS",
                "device_model": "MacBook",
            },
        )
        assert member_login_again.status_code == 200
        member_token = member_login_again.json()["access_token"]

        admin_devices = client.get(
            "/api/v1/admin/devices",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_devices.status_code == 200
        admin_items = admin_devices.json()["items"]
        emails = {row["user_email"] for row in admin_items}
        assert "admin@example.com" in emails
        assert "member@example.com" in emails
        assert any(row["app_version"] in {"2.0.0", "3.1.0"} for row in admin_items)

        member_devices = client.get(
            "/api/v1/admin/devices",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert member_devices.status_code == 200
        member_items = member_devices.json()["items"]
        assert len(member_items) >= 1
        assert all(row["user_email"] == "member@example.com" for row in member_items)

        app_devices = client.get(
            "/api/v1/devices",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert app_devices.status_code == 200
        app_items = app_devices.json()
        assert len(app_items) >= 1
        assert all("session_count" in row for row in app_items)
        assert any((row.get("session_count") or 0) >= 2 for row in app_items)
        assert all("app_version" in row for row in app_items)
        assert all("os_version" in row for row in app_items)
        assert all("device_model" in row for row in app_items)
        assert all("last_ip" in row for row in app_items)

        app_device_sessions = client.get(
            "/api/v1/devices?view=sessions&active_within_days=0",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert app_device_sessions.status_code == 200
        session_items = app_device_sessions.json()
        assert len(session_items) >= 2
        assert all(row.get("session_count") == 1 for row in session_items)
    finally:
        app.dependency_overrides.clear()


def test_admin_can_soft_delete_user_and_filter_status() -> None:
    client, testing_session = _make_client()
    try:
        admin_auth = _register_web(client, "admin@example.com")
        admin_token = admin_auth["access_token"]
        member_auth = _register_web(client, "member@example.com")
        member_user_id = member_auth["user"]["id"]

        db = testing_session()
        try:
            admin_user = db.scalar(select(User).where(User.id == admin_auth["user"]["id"]))
            assert admin_user is not None
            admin_user.is_admin = True
            db.add(admin_user)
            db.commit()
        finally:
            db.close()

        deleted = client.delete(
            f"/api/v1/admin/users/{member_user_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert deleted.status_code == 200
        payload = deleted.json()
        assert payload["id"] == member_user_id
        assert payload["is_enabled"] is False
        assert payload["is_admin"] is False

        default_users = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert default_users.status_code == 200
        assert all(item["id"] != member_user_id for item in default_users.json()["items"])

        all_users = client.get(
            "/api/v1/admin/users?status=all",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert all_users.status_code == 200
        all_map = {item["id"]: item for item in all_users.json()["items"]}
        assert member_user_id in all_map
        assert all_map[member_user_id]["is_enabled"] is False

        disabled_only = client.get(
            "/api/v1/admin/users?status=disabled",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert disabled_only.status_code == 200
        disabled_ids = {item["id"] for item in disabled_only.json()["items"]}
        assert member_user_id in disabled_ids

        denied_login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "member@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "member-disabled",
                "platform": "web",
            },
        )
        assert denied_login.status_code == 403

        delete_self = client.delete(
            f"/api/v1/admin/users/{admin_auth['user']['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert delete_self.status_code == 400
    finally:
        app.dependency_overrides.clear()
