from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import User


def _make_client() -> tuple[TestClient, sessionmaker[Session]]:
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


def _register(client: TestClient, email: str, password: str, *, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "web" if client_type == "web" else "ios",
        },
    )
    assert res.status_code == 200
    return res.json()


def _create_shared_ledger(client: TestClient, owner_token: str, owner_device: str, ledger_id: str) -> None:
    push_res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={
            "device_id": owner_device,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {
                        "content": '{"ledgerName":"Shared","currency":"CNY","count":0,"items":[]}'
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        },
    )
    assert push_res.status_code == 200


def test_owner_can_add_update_remove_member_by_email() -> None:
    client, _ = _make_client()
    try:
        owner = _register(client, "owner@example.com", "123456")
        owner_token = owner["access_token"]
        owner_device = owner["device_id"]
        ledger_id = "ledger-shared"
        _create_shared_ledger(client, owner_token, owner_device, ledger_id)

        editor = _register(client, "editor@example.com", "123456")

        add_member = client.post(
            "/api/v1/share/member/add",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "editor@example.com",
                "role": "viewer",
            },
        )
        assert add_member.status_code == 200
        assert add_member.json()["result"] == "created"
        assert add_member.json()["role"] == "viewer"

        members = client.get(
            "/api/v1/share/members",
            headers={"Authorization": f"Bearer {owner_token}"},
            params={"ledger_id": ledger_id},
        )
        assert members.status_code == 200
        by_email = {row.get("user_email"): row for row in members.json()}
        assert "editor@example.com" in by_email
        assert by_email["editor@example.com"]["role"] == "viewer"

        update_member = client.post(
            "/api/v1/share/member/add",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "editor@example.com",
                "role": "editor",
            },
        )
        assert update_member.status_code == 200
        assert update_member.json()["result"] == "updated"
        assert update_member.json()["role"] == "editor"

        remove_member = client.post(
            "/api/v1/share/member/remove",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "editor@example.com",
            },
        )
        assert remove_member.status_code == 200
        assert remove_member.json()["removed"] is True
        assert remove_member.json()["status"] == "left"

        remove_member_again = client.post(
            "/api/v1/share/member/remove",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "editor@example.com",
            },
        )
        assert remove_member_again.status_code == 200
        assert remove_member_again.json()["removed"] is False
        assert remove_member_again.json()["status"] == "left"

        cannot_change_owner = client.post(
            "/api/v1/share/member/add",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "owner@example.com",
                "role": "editor",
            },
        )
        assert cannot_change_owner.status_code == 400
        assert cannot_change_owner.json()["error"]["code"] == "SHARE_OWNER_ROLE_IMMUTABLE"
    finally:
        app.dependency_overrides.clear()


def test_admin_can_manage_member_without_membership() -> None:
    client, session_factory = _make_client()
    try:
        owner = _register(client, "owner@example.com", "123456")
        owner_token = owner["access_token"]
        owner_device = owner["device_id"]
        ledger_id = "ledger-shared-admin"
        _create_shared_ledger(client, owner_token, owner_device, ledger_id)

        _register(client, "admin@example.com", "123456", client_type="web")
        _register(client, "target@example.com", "123456", client_type="web")

        with session_factory() as db:
            admin_user = db.scalar(select(User).where(User.email == "admin@example.com"))
            assert admin_user is not None
            admin_user.is_admin = True
            db.commit()

        login_admin = client.post(
            "/api/v1/auth/login",
            json={
                "email": "admin@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        )
        assert login_admin.status_code == 200
        admin_token = login_admin.json()["access_token"]

        add_member = client.post(
            "/api/v1/share/member/add",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "target@example.com",
                "role": "editor",
            },
        )
        assert add_member.status_code == 200
        assert add_member.json()["result"] == "created"

        remove_member = client.post(
            "/api/v1/share/member/remove",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "target@example.com",
            },
        )
        assert remove_member.status_code == 200
        assert remove_member.json()["removed"] is True
    finally:
        app.dependency_overrides.clear()


def test_add_member_requires_registered_email() -> None:
    client, _ = _make_client()
    try:
        owner = _register(client, "owner@example.com", "123456")
        owner_token = owner["access_token"]
        owner_device = owner["device_id"]
        ledger_id = "ledger-shared-missing-user"
        _create_shared_ledger(client, owner_token, owner_device, ledger_id)

        add_member = client.post(
            "/api/v1/share/member/add",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "not-registered@example.com",
                "role": "editor",
            },
        )
        assert add_member.status_code == 404
        payload = add_member.json()
        assert payload["error"]["code"] == "SHARE_MEMBER_USER_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()
