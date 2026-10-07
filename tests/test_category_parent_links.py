"""#101: stable category parents survive Web/App renames and partial updates."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from src.main import app
from src.models import UserCategoryProjection
from test_category_delete_validation import _latest_change_id, _login_web, _register, _seed_ledger
from test_user_global_sync import _make_client


@pytest.fixture
def workspace():
    client, sessions = _make_client()
    owner = _register(client, "parent-links@example.com")
    ledger_id = "L_PARENT_LINKS"
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    web = _login_web(client, "parent-links@example.com")
    try:
        yield client, web["access_token"], owner, ledger_id, sessions
    finally:
        app.dependency_overrides.clear()


def categories(ws):
    client, token, _, ledger, _ = ws
    res = client.get(f"/api/v1/read/ledgers/{ledger}/categories",
                     headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def write(ws, method, suffix="", **payload):
    client, token, _, ledger, _ = ws
    payload.setdefault("base_change_id", _latest_change_id(client, token, ledger))
    res = client.request(method, f"/api/v1/write/ledgers/{ledger}/categories{suffix}",
                         headers={"Authorization": f"Bearer {token}"}, json=payload)
    return res


def create(ws, name, **kwargs):
    response = write(ws, "POST", name=name, kind=kwargs.pop("kind", "expense"),
                     level=kwargs.pop("level", 1), **kwargs)
    assert response.status_code == 200, response.text
    return next(c["id"] for c in categories(ws) if c["name"] == name)


def full_snapshot(ws):
    client, token, _, ledger, _ = ws
    response = client.get(f"/api/v1/sync/full?ledger_id={ledger}",
                          headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return json.loads(response.json()["snapshot"]["payload"]["content"])


def push(ws, sync_id, payload):
    client, _, owner, _, _ = ws
    response = client.post("/api/v1/sync/push",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
        json={"device_id": owner["device_id"], "changes": [{
            "ledger_id": "__user_global__", "scope": "user", "entity_type": "category",
            "entity_sync_id": sync_id, "action": "upsert", "payload": {"syncId": sync_id, **payload},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }]})
    assert response.status_code == 200, response.text


def test_web_rename_keeps_children_and_blocks_parent_delete(workspace):
    parent = create(workspace, "餐饮")
    child = create(workspace, "早餐", level=2, parent_name="餐饮")
    response = write(workspace, "PATCH", f"/{parent}", name="伙食")
    assert response.status_code == 200, response.text
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert (row["parent_sync_id"], row["parent_name"]) == (parent, "伙食")
    response = write(workspace, "DELETE", f"/{parent}")
    assert response.status_code == 400 and "child" in response.text
    assert len(categories(workspace)) == 2


def test_stable_id_wins_over_stale_name_and_round_trips(workspace):
    parent = create(workspace, "伙食")
    child = create(workspace, "午餐", level=2, parent_sync_id=parent, parent_name="旧餐饮")
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert (row["parent_sync_id"], row["parent_name"]) == (parent, "伙食")
    exported = next(c for c in full_snapshot(workspace)["categories"] if c["syncId"] == child)
    assert (exported["parentSyncId"], exported["parentName"]) == (parent, "伙食")


def test_mobile_rename_and_partial_child_update_keep_parent(workspace):
    parent = create(workspace, "餐饮")
    child = create(workspace, "早餐", level=2, parent_name="餐饮")
    push(workspace, parent, {"name": "伙食"})
    push(workspace, child, {"icon": "restaurant"})
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert (row["parent_sync_id"], row["parent_name"]) == (parent, "伙食")
    assert write(workspace, "DELETE", f"/{parent}").status_code == 400


def test_partial_web_edit_and_legacy_reparent(workspace):
    parent = create(workspace, "餐饮")
    other = create(workspace, "购物")
    child = create(workspace, "早餐", level=2, parent_sync_id=parent)
    assert write(workspace, "PATCH", f"/{child}", icon="restaurant").status_code == 200
    assert next(c for c in categories(workspace) if c["id"] == child)["parent_sync_id"] == parent
    assert write(workspace, "PATCH", f"/{child}", parent_name="购物").status_code == 200
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert (row["parent_sync_id"], row["parent_name"]) == (other, "购物")


def test_legacy_mobile_reparent_and_stale_name_preservation(workspace):
    parent = create(workspace, "餐饮")
    other = create(workspace, "购物")
    child = create(workspace, "早餐", level=2, parent_name="餐饮")
    push(workspace, parent, {"name": "伙食"})
    push(workspace, child, {"parentName": "餐饮"})
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert (row["parent_sync_id"], row["parent_name"]) == (parent, "伙食")
    push(workspace, child, {"parentName": "购物"})
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert (row["parent_sync_id"], row["parent_name"]) == (other, "购物")


def test_ambiguous_legacy_mobile_parent_name_is_not_guessed(workspace):
    parent = create(workspace, "餐饮")
    with workspace[4]() as db:
        row = db.scalar(select(UserCategoryProjection).where(UserCategoryProjection.sync_id == parent))
        db.add(UserCategoryProjection(user_id=row.user_id, sync_id="duplicate-parent", name=row.name,
                                      kind=row.kind, level=1))
        db.commit()
    push(workspace, "legacy-child", {"name": "早餐", "kind": "expense", "level": 2, "parentName": "餐饮"})
    row = next(c for c in categories(workspace) if c["id"] == "legacy-child")
    assert row["parent_sync_id"] is None


def test_explicit_reparent_and_clear_parent(workspace):
    parent = create(workspace, "餐饮")
    other = create(workspace, "购物")
    child = create(workspace, "早餐", level=2, parent_sync_id=parent)
    assert write(workspace, "PATCH", f"/{child}", parent_sync_id=other).status_code == 200
    assert next(c for c in categories(workspace) if c["id"] == child)["parent_sync_id"] == other
    assert write(workspace, "PATCH", f"/{child}", level=1, parent_sync_id=None, parent_name=None).status_code == 200
    row = next(c for c in categories(workspace) if c["id"] == child)
    assert row["parent_sync_id"] is None and row["parent_name"] is None and row["level"] == 1


def test_delete_guard_uses_id_even_with_stale_name(workspace):
    parent = create(workspace, "餐饮")
    child = create(workspace, "早餐", level=2, parent_name="餐饮")
    with workspace[4]() as db:
        row = db.scalar(select(UserCategoryProjection).where(UserCategoryProjection.sync_id == child))
        row.parent_name = "过期名称"
        db.commit()
    assert write(workspace, "DELETE", f"/{parent}").status_code == 400


@pytest.mark.parametrize("target", ["missing", "wrong-kind", "self", "child"])
def test_invalid_parent_is_rejected_without_changing_data(workspace, target):
    parent = create(workspace, "餐饮")
    income = create(workspace, "工资", kind="income")
    child = create(workspace, "早餐", level=2, parent_name="餐饮")
    parent_id = {"missing": "cat_missing", "wrong-kind": income, "self": child, "child": child}[target]
    editing = parent if target == "child" else child
    before = categories(workspace)
    response = write(workspace, "PATCH", f"/{editing}", parent_sync_id=parent_id, level=2)
    assert response.status_code == 400, response.text
    assert categories(workspace) == before
