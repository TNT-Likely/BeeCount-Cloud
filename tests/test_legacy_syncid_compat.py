from __future__ import annotations

import re
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


def test_legacy_projection_ids_can_update_entities() -> None:
    client = _make_client()
    try:
        owner = _auth(client, "owner@example.com", "123456", "app")
        owner_app_token = owner["access_token"]
        owner_device = owner["device_id"]

        now = datetime.now(timezone.utc).isoformat()
        legacy_snapshot = (
            '{"ledgerName":"Legacy Ledger","currency":"CNY","count":1,'
            f'"items":[{{"type":"expense","amount":10,"happenedAt":"{now}","note":"legacy tx",'
            '"categoryName":"Food","categoryKind":"expense","accountName":"Cash","tags":"daily"}],'
            '"accounts":[{"name":"Cash","type":"cash","currency":"CNY","initialBalance":100}],'
            '"categories":[{"name":"Food","kind":"expense"}],'
            '"tags":[{"name":"daily","color":"#222222"}]}'
        )

        seed = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={
                "device_id": owner_device,
                "changes": [
                    {
                        "ledger_id": "ledger-legacy",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-legacy",
                        "action": "upsert",
                        "payload": {"content": legacy_snapshot},
                        "updated_at": now,
                    }
                ],
            },
        )
        assert seed.status_code == 200

        owner_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        )
        assert owner_web.status_code == 200
        owner_web_token = owner_web.json()["access_token"]

        detail = client.get(
            "/api/v1/read/ledgers/ledger-legacy",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert detail.status_code == 200
        base = detail.json()["source_change_id"]

        tx_rows = client.get(
            "/api/v1/read/ledgers/ledger-legacy/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert tx_rows.status_code == 200
        tx_id = tx_rows.json()[0]["id"]
        assert re.fullmatch(r"tx_0_[a-z0-9]{8}", tx_id)

        acc_rows = client.get(
            "/api/v1/read/ledgers/ledger-legacy/accounts",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert acc_rows.status_code == 200
        account_id = acc_rows.json()[0]["id"]
        assert re.fullmatch(r"acc_0_[a-z0-9]{8}", account_id)

        cat_rows = client.get(
            "/api/v1/read/ledgers/ledger-legacy/categories",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert cat_rows.status_code == 200
        category_id = cat_rows.json()[0]["id"]
        assert re.fullmatch(r"cat_0_[a-z0-9]{8}", category_id)

        tag_rows = client.get(
            "/api/v1/read/ledgers/ledger-legacy/tags",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert tag_rows.status_code == 200
        tag_id = tag_rows.json()[0]["id"]
        assert re.fullmatch(r"tag_0_[a-z0-9]{8}", tag_id)

        tx_update = client.patch(
            f"/api/v1/write/ledgers/ledger-legacy/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"base_change_id": base, "note": "updated tx"},
        )
        assert tx_update.status_code == 200
        base = tx_update.json()["new_change_id"]

        second_tx_update = client.patch(
            f"/api/v1/write/ledgers/ledger-legacy/transactions/{tx_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"base_change_id": base, "amount": 15},
        )
        assert second_tx_update.status_code == 200
        base = second_tx_update.json()["new_change_id"]

        account_update = client.patch(
            f"/api/v1/write/ledgers/ledger-legacy/accounts/{account_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"base_change_id": base, "name": "Cash Wallet"},
        )
        assert account_update.status_code == 200
        base = account_update.json()["new_change_id"]

        category_update = client.patch(
            f"/api/v1/write/ledgers/ledger-legacy/categories/{category_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"base_change_id": base, "name": "Food Updated"},
        )
        assert category_update.status_code == 200
        base = category_update.json()["new_change_id"]

        tag_update = client.patch(
            f"/api/v1/write/ledgers/ledger-legacy/tags/{tag_id}",
            headers={"Authorization": f"Bearer {owner_web_token}"},
            json={"base_change_id": base, "name": "daily-updated"},
        )
        assert tag_update.status_code == 200

        tx_rows_after = client.get(
            "/api/v1/read/ledgers/ledger-legacy/transactions",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert tx_rows_after.status_code == 200
        assert tx_rows_after.json()[0]["id"] == tx_id
    finally:
        app.dependency_overrides.clear()


def test_legacy_snapshot_duplicate_account_names_do_not_break_projection() -> None:
    client = _make_client()
    try:
        owner = _auth(client, "owner@example.com", "123456", "app")
        owner_app_token = owner["access_token"]
        owner_device = owner["device_id"]

        now = datetime.now(timezone.utc).isoformat()
        legacy_snapshot = (
            '{"ledgerName":"Legacy Dedupe","currency":"CNY","count":1,'
            f'"items":[{{"type":"expense","amount":10,"happenedAt":"{now}","note":"legacy tx",'
            '"categoryName":"Food","categoryKind":"expense","accountName":"支付宝","tags":"daily"}],'
            '"accounts":['
            '{"name":"支付宝","type":"alipay","currency":"CNY","initialBalance":100},'
            '{"name":" 支付宝 ","type":"alipay","currency":"CNY","initialBalance":200}'
            '],'
            '"categories":[{"name":"Food","kind":"expense"}],'
            '"tags":[{"name":"daily","color":"#222222"}]}'
        )

        seed = client.post(
            "/api/v1/sync/push",
            headers={"Authorization": f"Bearer {owner_app_token}"},
            json={
                "device_id": owner_device,
                "changes": [
                    {
                        "ledger_id": "ledger-legacy-dedupe",
                        "entity_type": "ledger_snapshot",
                        "entity_sync_id": "ledger-legacy-dedupe",
                        "action": "upsert",
                        "payload": {"content": legacy_snapshot},
                        "updated_at": now,
                    }
                ],
            },
        )
        assert seed.status_code == 200

        owner_web = client.post(
            "/api/v1/auth/login",
            json={
                "email": "owner@example.com",
                "password": "123456",
                "client_type": "web",
                "device_name": "pytest-web",
                "platform": "web",
            },
        )
        assert owner_web.status_code == 200
        owner_web_token = owner_web.json()["access_token"]

        acc_rows = client.get(
            "/api/v1/read/ledgers/ledger-legacy-dedupe/accounts",
            headers={"Authorization": f"Bearer {owner_web_token}"},
        )
        assert acc_rows.status_code == 200
        payload = acc_rows.json()
        normalized_names = [row["name"].strip() for row in payload]
        assert normalized_names == ["支付宝"]
    finally:
        app.dependency_overrides.clear()
