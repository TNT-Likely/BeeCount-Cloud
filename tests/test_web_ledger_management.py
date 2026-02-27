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


def _register_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200
    return res.json()


def test_write_ledger_create_meta_and_share_invites() -> None:
    client = _make_client()
    try:
        owner = _register_web(client, "owner@example.com")
        owner_token = owner["access_token"]

        create = client.post(
            "/api/v1/write/ledgers",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "ledger_name": "My Family",
                "currency": "cny",
            },
        )
        assert create.status_code == 200
        ledger_id = create.json()["ledger_id"]
        create_change = create.json()["new_change_id"]
        assert ledger_id
        assert create.json()["base_change_id"] == 0

        detail = client.get(
            f"/api/v1/read/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert detail.status_code == 200
        assert detail.json()["ledger_name"] == "My Family"
        assert detail.json()["currency"] == "CNY"
        assert detail.json()["source_change_id"] == create_change

        update_meta = client.patch(
            f"/api/v1/write/ledgers/{ledger_id}/meta",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "base_change_id": detail.json()["source_change_id"],
                "ledger_name": "Family Budget",
                "currency": "usd",
            },
        )
        assert update_meta.status_code == 200
        meta_change = update_meta.json()["new_change_id"]
        assert meta_change > create_change

        detail_after_meta = client.get(
            f"/api/v1/read/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert detail_after_meta.status_code == 200
        assert detail_after_meta.json()["ledger_name"] == "Family Budget"
        assert detail_after_meta.json()["currency"] == "USD"

        invite = client.post(
            "/api/v1/share/invite",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"ledger_id": ledger_id, "role": "viewer", "max_uses": 1},
        )
        assert invite.status_code == 200
        invite_code = invite.json()["invite_code"]
        invite_id = invite.json()["invite_id"]

        invites_initial = client.get(
            "/api/v1/share/invites",
            headers={"Authorization": f"Bearer {owner_token}"},
            params={"ledger_id": ledger_id},
        )
        assert invites_initial.status_code == 200
        assert len(invites_initial.json()) == 1
        assert invites_initial.json()[0]["status"] == "active"

        viewer = _register_web(client, "viewer@example.com")
        viewer_token = viewer["access_token"]
        join = client.post(
            "/api/v1/share/join",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={"invite_code": invite_code},
        )
        assert join.status_code == 200

        invites_exhausted = client.get(
            "/api/v1/share/invites",
            headers={"Authorization": f"Bearer {owner_token}"},
            params={"ledger_id": ledger_id},
        )
        assert invites_exhausted.status_code == 200
        assert invites_exhausted.json()[0]["status"] == "exhausted"

        revoke = client.post(
            "/api/v1/share/invite/revoke",
            headers={"Authorization": f"Bearer {owner_token}"},
            json={"invite_id": invite_id},
        )
        assert revoke.status_code == 200

        invites_revoked = client.get(
            "/api/v1/share/invites",
            headers={"Authorization": f"Bearer {owner_token}"},
            params={"ledger_id": ledger_id},
        )
        assert invites_revoked.status_code == 200
        assert invites_revoked.json()[0]["status"] == "revoked"

        viewer_forbidden = client.get(
            "/api/v1/share/invites",
            headers={"Authorization": f"Bearer {viewer_token}"},
            params={"ledger_id": ledger_id},
        )
        assert viewer_forbidden.status_code == 403
        assert viewer_forbidden.json()["error"]["code"] == "SHARE_ROLE_FORBIDDEN"
    finally:
        app.dependency_overrides.clear()
