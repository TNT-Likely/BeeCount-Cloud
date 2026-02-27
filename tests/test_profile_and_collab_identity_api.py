from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


def _register(
    client: TestClient,
    *,
    email: str,
    password: str = "123456",
    client_type: str = "app",
) -> dict:
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


def _login_web(client: TestClient, *, email: str, password: str = "123456") -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200
    return res.json()


def _create_ledger_snapshot(
    client: TestClient,
    *,
    token: str,
    device_id: str,
    ledger_id: str,
) -> None:
    push_res = client.post(
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
                    "payload": {
                        "content": '{"ledgerName":"Shared","currency":"CNY","count":0,"items":[]}'
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        },
    )
    assert push_res.status_code == 200


def _upload_avatar(
    client: TestClient,
    *,
    token: str,
    file_name: str = "avatar.png",
) -> dict:
    response = client.post(
        "/api/v1/profile/avatar",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (file_name, b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image/png"),
        },
    )
    assert response.status_code == 200
    return response.json()


def test_profile_me_patch_and_avatar_upload() -> None:
    client, _ = _make_client()
    try:
        app_auth = _register(client, email="owner@example.com", client_type="app")
        app_token = app_auth["access_token"]

        # App token cannot patch display name (web-only mutation).
        denied = client.patch(
            "/api/v1/profile/me",
            headers={"Authorization": f"Bearer {app_token}"},
            json={"display_name": "Owner A"},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "AUTH_INSUFFICIENT_SCOPE"

        web_auth = _login_web(client, email="owner@example.com")
        web_token = web_auth["access_token"]
        patch_res = client.patch(
            "/api/v1/profile/me",
            headers={"Authorization": f"Bearer {web_token}"},
            json={"display_name": "Owner A"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["display_name"] == "Owner A"
        assert patch_res.json()["avatar_url"] is None
        assert patch_res.json()["avatar_version"] == 0

        uploaded = _upload_avatar(client, token=app_token)
        assert uploaded["avatar_version"] == 1
        assert uploaded["avatar_url"].startswith("/api/v1/profile/avatar/")

        profile = client.get(
            "/api/v1/profile/me",
            headers={"Authorization": f"Bearer {app_token}"},
        )
        assert profile.status_code == 200
        payload = profile.json()
        assert payload["display_name"] == "Owner A"
        assert payload["avatar_version"] == 1
        assert payload["avatar_url"].startswith("/api/v1/profile/avatar/")

        # Avatar file is publicly fetchable by URL for cross-end display.
        avatar_path = payload["avatar_url"].split("?")[0]
        avatar_file = client.get(avatar_path)
        assert avatar_file.status_code == 200
        assert avatar_file.headers["content-type"].startswith("image/")
    finally:
        app.dependency_overrides.clear()


def test_share_members_and_workspace_transactions_include_profile_identity() -> None:
    client, _ = _make_client()
    try:
        owner_app = _register(client, email="owner@example.com", client_type="app")
        owner_app_token = owner_app["access_token"]
        owner_device = owner_app["device_id"]
        owner_id = owner_app["user"]["id"]
        ledger_id = "ledger-profile-shared"
        _create_ledger_snapshot(
            client,
            token=owner_app_token,
            device_id=owner_device,
            ledger_id=ledger_id,
        )

        owner_web = _login_web(client, email="owner@example.com")
        owner_web_token = owner_web["access_token"]
        patch_owner = client.patch(
            "/api/v1/profile/me",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"display_name": "Owner Display"},
        )
        assert patch_owner.status_code == 200
        _upload_avatar(client, token=owner_app_token)

        member_app = _register(client, email="editor@example.com", client_type="app")
        member_web = _login_web(client, email="editor@example.com")
        patch_member = client.patch(
            "/api/v1/profile/me",
            headers={"Authorization": f"Bearer {member_web['access_token']}"},
            json={"display_name": "Editor Display"},
        )
        assert patch_member.status_code == 200

        add_member = client.post(
            "/api/v1/share/member/add",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={
                "ledger_id": ledger_id,
                "member_email": "editor@example.com",
                "role": "editor",
            },
        )
        assert add_member.status_code == 200

        members = client.get(
            "/api/v1/share/members",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            params={"ledger_id": ledger_id},
        )
        assert members.status_code == 200
        members_by_id = {row["user_id"]: row for row in members.json()}
        assert members_by_id[owner_id]["user_display_name"] == "Owner Display"
        assert members_by_id[owner_id]["user_avatar_url"].startswith("/api/v1/profile/avatar/")
        assert members_by_id[owner_id]["user_avatar_version"] == 1
        assert (
            members_by_id[member_app["user"]["id"]]["user_display_name"]
            == "Editor Display"
        )

        detail = client.get(
            f"/api/v1/read/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert detail.status_code == 200
        base_change_id = detail.json()["source_change_id"]

        create_tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={
                "base_change_id": base_change_id,
                "tx_type": "expense",
                "amount": 66.8,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "note": "with profile",
            },
        )
        assert create_tx.status_code == 200

        workspace = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            params={"ledger_id": ledger_id, "limit": 20, "offset": 0},
        )
        assert workspace.status_code == 200
        items = workspace.json()["items"]
        assert len(items) == 1
        assert items[0]["created_by_user_id"] == owner_id
        assert items[0]["created_by_email"] == "owner@example.com"
        assert items[0]["created_by_display_name"] == "Owner Display"
        assert items[0]["created_by_avatar_url"].startswith("/api/v1/profile/avatar/")
        assert items[0]["created_by_avatar_version"] == 1
    finally:
        app.dependency_overrides.clear()
