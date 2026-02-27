from datetime import datetime, timezone

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


def _register(client: TestClient, email: str, password: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "client_type": "app",
            "device_name": "pytest-device",
            "platform": "ios",
        },
    )
    assert res.status_code == 200
    return res.json()


def test_share_invite_join_and_role_permission() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com", "123456")
        owner_token = owner["access_token"]
        owner_device = owner["device_id"]

        push_res = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "device_id": owner_device,
                "changes": [
                    {
                        "ledger_id": "ledger-shared",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-shared",
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

        invite_res = client.post(
            "/api/v1/share/invite",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"ledger_id": "ledger-shared", "role": "viewer", "max_uses": 3},
        )
        assert invite_res.status_code == 200
        invite_code = invite_res.json()["invite_code"]

        viewer = _register(client, "viewer@example.com", "123456")
        viewer_token = viewer["access_token"]
        viewer_device = viewer["device_id"]

        join_res = client.post(
            "/api/v1/share/join",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"invite_code": invite_code},
        )
        assert join_res.status_code == 200
        assert join_res.json()["ledger_id"] == "ledger-shared"

        members_res = client.get(
            "/api/v1/share/members",
            headers={"Authorization": f"Bearer {owner_token}"},
            params={"ledger_id": "ledger-shared"},
        )
        assert members_res.status_code == 200
        assert len(members_res.json()) == 2

        viewer_pull_res = client.get(
            "/api/v1/sync/pull",
            headers={"Authorization": f"Bearer {viewer_token}"},
            params={"device_id": viewer_device, "since": 0},
        )
        assert viewer_pull_res.status_code == 200
        assert len(viewer_pull_res.json()["changes"]) == 1

        viewer_push_res = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "device_id": viewer_device,
                "changes": [
                    {
                        "ledger_id": "ledger-shared",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-shared",
                        "action": "upsert",
                        "payload": {
                            "content": '{"ledgerName":"Shared","currency":"CNY","count":1,"items":[]}'
                        },
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        assert viewer_push_res.status_code == 403
        assert viewer_push_res.json()["error"]["code"] == "SYNC_VIEWER_WRITE_FORBIDDEN"

        viewer_manage_denied = client.post(
            "/api/v1/share/invite",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"ledger_id": "ledger-shared", "role": "viewer", "max_uses": 1},
        )
        assert viewer_manage_denied.status_code == 403
        assert viewer_manage_denied.json()["error"]["code"] == "SHARE_ROLE_FORBIDDEN"

        role_update_res = client.post(
            "/api/v1/share/member/role",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_id": "ledger-shared",
                "user_id": viewer["user"]["id"],
                "role": "editor",
            },
        )
        assert role_update_res.status_code == 200

        editor_manage_denied = client.post(
            "/api/v1/share/member/role",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "ledger_id": "ledger-shared",
                "user_id": owner["user"]["id"],
                "role": "viewer",
            },
        )
        assert editor_manage_denied.status_code == 403
        assert editor_manage_denied.json()["error"]["code"] == "SHARE_ROLE_FORBIDDEN"

        viewer_push_after_role = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "device_id": viewer_device,
                "changes": [
                    {
                        "ledger_id": "ledger-shared",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-shared",
                        "action": "upsert",
                        "payload": {
                            "content": '{"ledgerName":"Shared","currency":"CNY","count":2,"items":[]}'
                        },
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        assert viewer_push_after_role.status_code == 200

        leave_res = client.post(
            "/api/v1/share/leave",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"ledger_id": "ledger-shared"},
        )
        assert leave_res.status_code == 200

        viewer_pull_after_leave = client.get(
            "/api/v1/sync/pull",
            headers={"Authorization": f"Bearer {viewer_token}"},
            params={"device_id": viewer_device, "since": 0},
        )
        assert viewer_pull_after_leave.status_code == 200
        assert len(viewer_pull_after_leave.json()["changes"]) == 0

        viewer_push_after_leave = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "device_id": viewer_device,
                "changes": [
                    {
                        "ledger_id": "ledger-shared",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-shared",
                        "action": "upsert",
                        "payload": {
                            "content": '{"ledgerName":"Shared","currency":"CNY","count":3,"items":[]}'
                        },
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        assert viewer_push_after_leave.status_code == 403
        assert viewer_push_after_leave.json()["error"]["code"] == "SYNC_LEDGER_WRITE_FORBIDDEN"
    finally:
        app.dependency_overrides.clear()
