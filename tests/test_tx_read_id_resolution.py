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

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.database import Base, engine
from server import app


@pytest.fixture(autouse=True)
def _fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


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
    """Scenario A: mobile pushes tx with tagIds + tag 'A', then renames tag
    to 'B' via a subsequent push that does NOT touch the tx. Web list should
    show the new name."""
    client = TestClient(app)
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

    # Read initial — tag name is "A"
    r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
    assert r.status_code == 200, r.text
    tx = r.json()["items"][0]
    assert tx["tags_list"] == ["A"]

    # Rename tag to "B" (only tag change in this push, tx untouched)
    from datetime import timedelta
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

    # Read again — id-resolution should give us the new name
    r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
    tx = r.json()["items"][0]
    assert tx["tags_list"] == ["B"], f"expected ['B'], got {tx['tags_list']}"


def test_account_rename_reflects_in_tx_via_id_resolution():
    """Scenario A for account: rename propagates to tx.account_name."""
    client = TestClient(app)
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

    from datetime import timedelta
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


def test_legacy_tx_without_ids_falls_back_to_name():
    """Scenario B: older tx in snapshot has only `accountName` / `tags` string,
    no ids. Read endpoint falls back to those values."""
    client = TestClient(app)
    app_token = _register_and_token(client, "c@test.com", device_id="m1", client_type="app")
    web_token = _register_and_token(client, "c@test.com", device_id="w1", client_type="web")
    app_hdr = {"Authorization": f"Bearer {app_token}"}
    web_hdr = {"Authorization": f"Bearer {web_token}"}

    ledger_id = "lg_c"
    tx_sync_id = "tx-c-1"

    # Tx pushed without any id fields (legacy shape)
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
                # no accountId / categoryId / tagIds
            },
        },
    ])

    r = client.get("/api/v1/read/workspace/transactions", headers=web_hdr)
    tx = r.json()["items"][0]
    assert tx["account_name"] == "现金"
    assert tx["category_name"] == "餐饮"
    assert tx["tags_list"] == ["午餐", "外卖"]


def test_tag_id_not_found_falls_back_to_stored_names():
    """Scenario C: tx's tagIds contains an id that's not in snapshot.tags
    (e.g., tag deleted). Should fall back to item.tags string rather than
    returning empty."""
    client = TestClient(app)
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
