"""A cold restore includes this device's events on every page (#124)."""
from datetime import datetime, timezone

from test_category_delete_validation import _login_web, _make_client, _register, _seed_ledger

from src.main import app


def test_paginated_device_restore_and_incremental_echo_filter():
    client = _make_client()
    try:
        owner = _register(client, "device-recovery@example.com")
        outsider = _register(client, "unrelated-device@example.com")
        device = owner["device_id"]
        headers = {"Authorization": f"Bearer {owner['access_token']}"}
        ids = ["first", "second", "last"]
        response = client.post("/api/v1/sync/push", headers=headers, json={
            "device_id": device,
            "changes": [{
                "scope": "user", "entity_type": "tag", "entity_sync_id": tag,
                "action": "upsert", "updated_at": datetime.now(timezone.utc).isoformat(),
                "payload": {"name": tag},
            } for tag in ids],
        })
        assert response.status_code == 200, response.text
        assert response.json()["accepted"] == len(ids)

        def pull(**params):
            return client.get("/api/v1/sync/pull", headers=headers, params=params)

        first = pull(since=0, device_id=device, limit=2).json()
        assert [r["entity_sync_id"] for r in first["changes"]] == ids[:2]
        assert first["has_more"]
        since = first["server_cursor"]
        assert pull(since=since, device_id=device).json()["changes"] == []
        last = pull(since=since, restore_device_id=device, limit=2).json()
        assert [r["entity_sync_id"] for r in last["changes"]] == ids[2:]
        assert not last["has_more"] and last["server_cursor"] > since
        assert pull(since=last["server_cursor"], device_id=device).json()["changes"] == []
        assert pull(since=0, restore_device_id=outsider["device_id"]).status_code == 401
        assert pull(since=0, device_id=device, restore_device_id=device).status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_incremental_pull_includes_restore_published_without_a_device():
    client = _make_client()
    try:
        email = "server-restore@example.com"
        owner = _register(client, email)
        device = owner["device_id"]
        headers = {"Authorization": f"Bearer {owner['access_token']}"}
        ledger = "recover-without-publisher-device"
        _seed_ledger(client, owner["access_token"], device, ledger)
        baseline = client.get("/api/v1/sync/pull", headers=headers).json()["server_cursor"]
        assert baseline > 0
        web = {"Authorization": f"Bearer {_login_web(client, email)['access_token']}"}
        backup = client.post("/api/v1/admin/backups/create", headers=web, json={"ledger_id": ledger})
        assert backup.status_code == 200, backup.text
        restored = client.post("/api/v1/admin/backups/restore", headers=web,
                               json={"snapshot_id": backup.json()["snapshot_id"]})
        assert restored.status_code == 200, restored.text
        response = client.get("/api/v1/sync/pull", headers=headers,
                              params={"since": baseline, "device_id": device})
        assert response.status_code == 200, response.text
        assert [r["change_id"] for r in response.json()["changes"]] == [restored.json()["change_id"]]
        assert response.json()["changes"][0]["updated_by_device_id"] is None
    finally:
        app.dependency_overrides.clear()
