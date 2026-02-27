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


def _register_app_user(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "ios",
        },
    )
    assert res.status_code == 200
    return res.json()


def _login_web_user(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
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


def test_app_scope_guardrail_blocks_read_write_devices_by_default() -> None:
    client = _make_client()
    try:
        app_user = _register_app_user(client, "guardrail@example.com")
        app_token = app_user["access_token"]
        device_id = app_user["device_id"]

        push = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {app_token}"},
            json={
                "device_id": device_id,
                "changes": [
                    {
                        "ledger_id": "ledger-guardrail",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-guardrail",
                        "action": "upsert",
                        "payload": {
                            "content": (
                                '{"ledgerName":"Guardrail","currency":"CNY","count":0,'
                                '"items":[],"accounts":[],"categories":[],"tags":[]}'
                            )
                        },
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
            },
        )
        assert push.status_code == 200

        read_blocked = client.get(
            "/api/v1/read/ledgers",
            headers={"Authorization": f"Bearer {app_token}"},
        )
        assert read_blocked.status_code == 403

        write_blocked = client.post(
            "/api/v1/write/ledgers/ledger-guardrail/accounts",
            headers={"Authorization": f"Bearer {app_token}"},
            json={
                "base_change_id": 1,
                "name": "Cash",
                "account_type": "cash",
                "currency": "CNY",
                "initial_balance": 0,
            },
        )
        assert write_blocked.status_code == 403

        devices_blocked = client.get(
            "/api/v1/devices",
            headers={"Authorization": f"Bearer {app_token}"},
        )
        assert devices_blocked.status_code == 403

        web_token = _login_web_user(client, "guardrail@example.com")["access_token"]
        read_allowed = client.get(
            "/api/v1/read/ledgers",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert read_allowed.status_code == 200
    finally:
        app.dependency_overrides.clear()
