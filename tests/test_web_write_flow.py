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


def test_web_write_crud_conflict_and_viewer_forbidden() -> None:
    client = _make_client()
    try:
        owner = _auth(client, "owner@example.com", "123456", "app")
        owner_app_token = owner["access_token"]
        owner_device = owner["device_id"]

        init_snapshot = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={
                "device_id": owner_device,
                "changes": [
                    {
                        "ledger_id": "ledger-web",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-web",
                        "action": "upsert",
                        "payload": {
                            "content": (
                                '{"ledgerName":"Web Ledger","currency":"CNY","count":0,'
                                '"items":[],"accounts":[],"categories":[],"tags":[]}'
                            )
                        },
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        assert init_snapshot.status_code == 200

        owner_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        ).json()
        owner_web_token = owner_web["access_token"]

        detail = client.get(
            "/api/v1/read/ledgers/ledger-web",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert detail.status_code == 200
        base = detail.json()["source_change_id"]

        acc_res = client.post(
            "/api/v1/write/ledgers/ledger-web/accounts",
            headers={
                "Authorization": f"Bearer {owner_web_token}",
                "Idempotency-Key": "acc-create-1",
            },
            json={
                "base_change_id": base,
                "name": "Cash",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 100,
            },
        )
        assert acc_res.status_code == 200
        base = acc_res.json()["new_change_id"]

        acc_replay = client.post(
            "/api/v1/write/ledgers/ledger-web/accounts",
            headers={
                "Authorization": f"Bearer {owner_web_token}",
                "Idempotency-Key": "acc-create-1",
            },
            json={
                "base_change_id": detail.json()["source_change_id"],
                "name": "Cash",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 100,
            },
        )
        assert acc_replay.status_code == 200
        assert acc_replay.json()["idempotency_replayed"] is True
        assert acc_replay.json()["new_change_id"] == base

        cat_res = client.post(
            "/api/v1/write/ledgers/ledger-web/categories",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={
                "base_change_id": base,
                "name": "Food",
                "kind": "expense",
                "sort_order": 1,
            },
        )
        assert cat_res.status_code == 200
        base = cat_res.json()["new_change_id"]

        tag_res = client.post(
            "/api/v1/write/ledgers/ledger-web/tags",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={
                "base_change_id": base,
                "name": "daily",
                "color": "#222222",
            },
        )
        assert tag_res.status_code == 200
        base = tag_res.json()["new_change_id"]

        tx_res = client.post(
            "/api/v1/write/ledgers/ledger-web/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 18.5,
                "happened_at": datetime.now(timezone.utc).isoformat(),
                "note": "lunch",
                "category_name": "Food",
                "category_kind": "expense",
                "account_name": "Cash",
                "tags": "daily",
            },
        )
        assert tx_res.status_code == 200
        tx_id = tx_res.json()["entity_id"]
        base = tx_res.json()["new_change_id"]
        assert tx_id

        tx_rows = client.get(
            "/api/v1/read/ledgers/ledger-web/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert tx_rows.status_code == 200
        tx_list = tx_rows.json()
        assert len(tx_list) == 1
        assert tx_list[0]["id"] == tx_id
        assert tx_list[0]["last_change_id"] == base
        assert tx_list[0]["tags_list"] == ["daily"]

        tx_update = client.patch(
            f"/api/v1/write/ledgers/ledger-web/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={
                "base_change_id": base,
                "note": "lunch-updated",
                "amount": 20,
                "tags": ["daily", "work"],
            },
        )
        assert tx_update.status_code == 200
        base = tx_update.json()["new_change_id"]

        tx_updated_rows = client.get(
            "/api/v1/read/ledgers/ledger-web/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert tx_updated_rows.status_code == 200
        updated_list = tx_updated_rows.json()
        assert len(updated_list) == 1
        assert updated_list[0]["tags_list"] == ["daily", "work"]

        tx_delete = client.request(
            "DELETE",
            f"/api/v1/write/ledgers/ledger-web/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"base_change_id": base},
        )
        assert tx_delete.status_code == 200
        base = tx_delete.json()["new_change_id"]

        conflict = client.post(
            "/api/v1/write/ledgers/ledger-web/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={
                "base_change_id": base - 1,
                "tx_type": "expense",
                "amount": 1,
                "happened_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "WRITE_CONFLICT"
        assert conflict.json()["latest_change_id"] == base
        assert isinstance(conflict.json()["latest_server_timestamp"], str)

        invite = client.post(
            "/api/v1/share/invite",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={"ledger_id": "ledger-web", "role": "viewer", "max_uses": 1},
        )
        assert invite.status_code == 200
        invite_code = invite.json()["invite_code"]

        _ = _auth(client, "viewer@example.com", "123456", "app")
        viewer_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "viewer@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "viewer-web",
                "platform": "web",
            },
        ).json()
        viewer_web_token = viewer_web["access_token"]

        viewer_join = client.post(
            "/api/v1/share/join",
            headers={"Authorization": f"Bearer {viewer_web_token}"},
            json={"invite_code": invite_code},
        )
        assert viewer_join.status_code == 200

        viewer_denied = client.post(
            "/api/v1/write/ledgers/ledger-web/transactions",
            headers={"Authorization": f"Bearer {viewer_web_token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 9,
                "happened_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        assert viewer_denied.status_code == 403
        assert viewer_denied.json()["error"]["code"] == "WRITE_ROLE_FORBIDDEN"
    finally:
        app.dependency_overrides.clear()
