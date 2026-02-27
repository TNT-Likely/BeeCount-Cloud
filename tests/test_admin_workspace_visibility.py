from datetime import datetime, timezone

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


def test_admin_can_view_all_workspace_data_while_normal_user_only_sees_own() -> None:
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

        create_ledger = client.post(
            "/api/v1/write/ledgers",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ledger_name": "Workspace Ledger", "currency": "CNY"},
        )
        assert create_ledger.status_code == 200
        ledger_id = create_ledger.json()["ledger_id"]
        base = create_ledger.json()["new_change_id"]

        create_account = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/accounts",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "base_change_id": base,
                "name": "SharedCash",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 0,
            },
        )
        assert create_account.status_code == 200
        base = create_account.json()["new_change_id"]

        create_admin_tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 12.3,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "note": "admin-tx",
                "account_name": "SharedCash",
            },
        )
        assert create_admin_tx.status_code == 200

        member_auth = _register_web(client, "member@example.com")
        member_token = member_auth["access_token"]

        invite = client.post(
            "/api/v1/share/invite",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ledger_id": ledger_id, "role": "editor", "max_uses": 1},
        )
        assert invite.status_code == 200

        join = client.post(
            "/api/v1/share/join",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"invite_code": invite.json()["invite_code"]},
        )
        assert join.status_code == 200

        member_detail = client.get(
            f"/api/v1/read/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert member_detail.status_code == 200
        member_base = member_detail.json()["source_change_id"]

        create_member_tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers={"Authorization": f"Bearer {member_token}"},
            json={
                "base_change_id": member_base,
                "tx_type": "expense",
                "amount": 7.8,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "note": "member-tx",
                "account_name": "SharedCash",
            },
        )
        assert create_member_tx.status_code == 200

        member_workspace = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert member_workspace.status_code == 200
        member_items = member_workspace.json()["items"]
        assert len(member_items) == 1
        assert member_items[0]["note"] == "member-tx"
        assert member_items[0]["created_by_user_id"] == member_auth["user"]["id"]

        admin_workspace = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert admin_workspace.status_code == 200
        notes = {item["note"] for item in admin_workspace.json()["items"]}
        assert notes == {"admin-tx", "member-tx"}

        admin_filtered = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"user_id": member_auth["user"]["id"]},
        )
        assert admin_filtered.status_code == 200
        filtered_items = admin_filtered.json()["items"]
        assert len(filtered_items) == 1
        assert filtered_items[0]["note"] == "member-tx"

        admin_combined_filtered = client.get(
            "/api/v1/read/workspace/transactions",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={
                "ledger_id": ledger_id,
                "tx_type": "expense",
                "account_name": "SharedCash",
                "q": "member-tx",
            },
        )
        assert admin_combined_filtered.status_code == 200
        combined_items = admin_combined_filtered.json()["items"]
        assert len(combined_items) == 1
        assert combined_items[0]["note"] == "member-tx"

        non_admin_users_api = client.get(
            "/api/v1/admin/users",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert non_admin_users_api.status_code == 403
    finally:
        app.dependency_overrides.clear()
