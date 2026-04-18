"""P1.1: read endpoints resolve tx tag/category/account names by id.

Contract: `/read/workspace/transactions` and `/read/ledgers/{id}/transactions`
- If snapshot.items[i].tagIds is a non-empty list, resolve each id against
  snapshot.tags and return CURRENT names. This means entity renames reflect in
  tx listing without any cascade rewrite of snapshot.items.
- If no ids (legacy tx from mobile pre-id-support), fall back to the names
  stored in the tx item.
- If an id is in the list but not found in snapshot.tags (e.g., deleted tag),
  skip it; if all ids fail, fall back to stored names.

Same for accountId / fromAccountId / toAccountId and categoryId.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
    # 每个测试用例独立 in-memory SQLite。绝不能用 src.database.engine —— 那是
    # 服务端真实 DB，drop_all 会清掉用户数据。
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


def _register_and_token(client: TestClient, email: str, *, device_id: str, client_type: str) -> str:
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _push(client, hdr, device_id, ledger_id, changes):
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": device_id, "changes": changes},
    )
    assert r.status_code == 200, r.text
    return r


def test_tag_rename_reflects_in_tx_via_id_resolution():
    client = _make_client()
    try:
        app_token = _register_and_token(client, "a@test.com", device_id="mobile-1", client_type="app")
        web_token = _register_and_token(client, "a@test.com", device_id="web-1", client_type="web")
        app_hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}

        ledger_id = "lg_1"
        tag_sync_id = "tag-alpha"
        tx_sync_id = "tx-uuid-1"

        _push(
            client, app_hdr, "mobile-1", ledger_id,
            [
                {
                    "ledger_id": ledger_id, "entity_type": "tag", "entity_sync_id": tag_sync_id,
                    "action": "upsert", "updated_at": _iso(),
                    "payload": {"syncId": tag_sync_id, "name": "A"},
                },
                {
                    "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": tx_sync_id,
                    "action": "upsert", "updated_at": _iso(),
                    "payload": {
                        "syncId": tx_sync_id, "type": "expense", "amount": 10,
                        "happenedAt": _iso(), "note": "x",
                        "tags": "A",
                        "tagIds": [tag_sync_id],
                    },
                },
            ],
        )

        r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
        assert r.status_code == 200, r.text
        tx = r.json()["items"][0]
        assert tx["tags_list"] == ["A"]

        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(
            client, app_hdr, "mobile-1", ledger_id,
            [
                {
                    "ledger_id": ledger_id, "entity_type": "tag", "entity_sync_id": tag_sync_id,
                    "action": "upsert", "updated_at": _iso(later),
                    "payload": {"syncId": tag_sync_id, "name": "B"},
                },
            ],
        )

        r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
        tx = r.json()["items"][0]
        assert tx["tags_list"] == ["B"], f"expected ['B'], got {tx['tags_list']}"
    finally:
        app.dependency_overrides.clear()


def test_account_rename_reflects_in_tx_via_id_resolution():
    client = _make_client()
    try:
        app_token = _register_and_token(client, "b@test.com", device_id="m1", client_type="app")
        web_token = _register_and_token(client, "b@test.com", device_id="w1", client_type="web")
        app_hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}

        ledger_id = "lg_b"
        acc_sync_id = "acc-uuid-1"
        tx_sync_id = "tx-b-1"

        _push(client, app_hdr, "m1", ledger_id, [
            {
                "ledger_id": ledger_id, "entity_type": "account", "entity_sync_id": acc_sync_id,
                "action": "upsert", "updated_at": _iso(),
                "payload": {"syncId": acc_sync_id, "name": "招商", "type": "bank_card", "currency": "CNY"},
            },
            {
                "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": tx_sync_id,
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": tx_sync_id, "type": "expense", "amount": 5,
                    "happenedAt": _iso(),
                    "accountName": "招商",
                    "accountId": acc_sync_id,
                },
            },
        ])

        later = datetime.now(timezone.utc) + timedelta(seconds=2)
        _push(client, app_hdr, "m1", ledger_id, [
            {
                "ledger_id": ledger_id, "entity_type": "account", "entity_sync_id": acc_sync_id,
                "action": "upsert", "updated_at": _iso(later),
                "payload": {"syncId": acc_sync_id, "name": "招商银行", "type": "bank_card", "currency": "CNY"},
            },
        ])

        r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
        tx = r.json()["items"][0]
        assert tx["account_name"] == "招商银行", f"got {tx['account_name']}"
    finally:
        app.dependency_overrides.clear()


def test_legacy_tx_without_ids_falls_back_to_name():
    client = _make_client()
    try:
        app_token = _register_and_token(client, "c@test.com", device_id="m1", client_type="app")
        web_token = _register_and_token(client, "c@test.com", device_id="w1", client_type="web")
        app_hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}

        ledger_id = "lg_c"
        tx_sync_id = "tx-c-1"

        _push(client, app_hdr, "m1", ledger_id, [
            {
                "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": tx_sync_id,
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": tx_sync_id, "type": "expense", "amount": 1,
                    "happenedAt": _iso(),
                    "accountName": "现金",
                    "categoryName": "餐饮", "categoryKind": "expense",
                    "tags": "午餐,外卖",
                },
            },
        ])

        r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
        tx = r.json()["items"][0]
        assert tx["account_name"] == "现金"
        assert tx["category_name"] == "餐饮"
        assert tx["tags_list"] == ["午餐", "外卖"]
    finally:
        app.dependency_overrides.clear()


def test_tag_id_not_found_falls_back_to_stored_names():
    client = _make_client()
    try:
        app_token = _register_and_token(client, "d@test.com", device_id="m1", client_type="app")
        web_token = _register_and_token(client, "d@test.com", device_id="w1", client_type="web")
        app_hdr = {"Authorization": f"Bearer {app_token}"}
        web_hdr = {"Authorization": f"Bearer {web_token}"}

        ledger_id = "lg_d"
        tx_sync_id = "tx-d-1"

        _push(client, app_hdr, "m1", ledger_id, [
            {
                "ledger_id": ledger_id, "entity_type": "transaction", "entity_sync_id": tx_sync_id,
                "action": "upsert", "updated_at": _iso(),
                "payload": {
                    "syncId": tx_sync_id, "type": "expense", "amount": 1,
                    "happenedAt": _iso(),
                    "tags": "历史标签",
                    "tagIds": ["tag-nonexistent"],
                },
            },
        ])

        r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
        tx = r.json()["items"][0]
        assert tx["tags_list"] == ["历史标签"], f"got {tx['tags_list']}"
    finally:
        app.dependency_overrides.clear()
