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
            "device_name": f"{email}-device",
            "platform": "web",
        },
    )
    assert res.status_code == 200
    return res.json()


def test_workspace_analytics_respects_admin_and_user_visibility() -> None:
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

        member_auth = _register_web(client, "member@example.com")
        member_token = member_auth["access_token"]

        create_ledger = client.post(
            "/api/v1/write/ledgers",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"ledger_name": "Analytics Ledger", "currency": "CNY"},
        )
        assert create_ledger.status_code == 200
        ledger_id = create_ledger.json()["ledger_id"]
        base = create_ledger.json()["new_change_id"]

        create_account = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/accounts",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "base_change_id": base,
                "name": "Cash",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 0,
            },
        )
        assert create_account.status_code == 200
        base = create_account.json()["new_change_id"]

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

        admin_tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 20,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "note": "admin-expense",
                "account_name": "Cash",
                "category_name": "Food",
                "category_kind": "expense",
            },
        )
        assert admin_tx.status_code == 200

        member_detail = client.get(
            f"/api/v1/read/ledgers/{ledger_id}",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert member_detail.status_code == 200
        member_base = member_detail.json()["source_change_id"]

        member_tx = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions",
            headers={"Authorization": f"Bearer {member_token}"},
            json={
                "base_change_id": member_base,
                "tx_type": "expense",
                "amount": 35,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "note": "member-expense",
                "account_name": "Cash",
                "category_name": "Transport",
                "category_kind": "expense",
            },
        )
        assert member_tx.status_code == 200

        admin_analytics = client.get(
            "/api/v1/read/workspace/analytics",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"scope": "all", "metric": "expense"},
        )
        assert admin_analytics.status_code == 200
        admin_payload = admin_analytics.json()
        assert admin_payload["summary"]["transaction_count"] == 2
        assert admin_payload["summary"]["expense_total"] == 55
        categories = {row["category_name"] for row in admin_payload["category_ranks"]}
        assert {"Food", "Transport"} <= categories

        admin_member_only = client.get(
            "/api/v1/read/workspace/analytics",
            headers={"Authorization": f"Bearer {admin_token}"},
            params={"scope": "all", "metric": "expense", "user_id": member_auth["user"]["id"]},
        )
        assert admin_member_only.status_code == 200
        member_only_payload = admin_member_only.json()
        assert member_only_payload["summary"]["transaction_count"] == 1
        assert member_only_payload["summary"]["expense_total"] == 35

        member_analytics = client.get(
            "/api/v1/read/workspace/analytics",
            headers={"Authorization": f"Bearer {member_token}"},
            params={"scope": "all", "metric": "expense"},
        )
        assert member_analytics.status_code == 200
        member_payload = member_analytics.json()
        assert member_payload["summary"]["transaction_count"] == 1
        assert member_payload["summary"]["expense_total"] == 35
    finally:
        app.dependency_overrides.clear()
