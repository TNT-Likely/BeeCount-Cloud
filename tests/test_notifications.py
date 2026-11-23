from collections.abc import Iterator

import pytest
from test_category_delete_validation import _login_web, _register
from test_user_global_sync import _make_client

from src.main import app
from src.models import User, UserCategoryProjection, UserNotificationRead


@pytest.fixture
def workspace() -> Iterator:
    client, sessions = _make_client()
    try:
        yield client, sessions
    finally:
        app.dependency_overrides.clear()


def _create_user(workspace, email, *, admin=False):
    client, sessions = workspace
    registered = _register(client, email)
    user_id = registered["user"]["id"]
    if admin:
        with sessions() as db:
            db.get(User, user_id).is_admin = True
            db.commit()
    device = _login_web(client, email)
    return user_id, {"Authorization": f"Bearer {device['access_token']}"}


def _seed_orphan(workspace, user_id, name, *, sync_id="child", parent_id="parent"):
    with workspace[1]() as db:
        db.add(
            UserCategoryProjection(
                user_id=user_id,
                sync_id=sync_id,
                name=name,
                kind="expense",
                level=2,
                parent_sync_id=parent_id,
                parent_name="旧分类",
            )
        )
        db.commit()


def _fetch(client, headers):
    response = client.get("/api/v1/notifications", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return response.json()


def _mark(client, headers, *ids):
    response = client.post("/api/v1/notifications/read", headers=headers, json={"ids": list(ids)})
    assert response.status_code == 204, response.text


def test_notification_scope_and_read_receipt_are_personal(workspace):
    client, sessions = workspace
    owner, owner_headers = _create_user(workspace, "notifications-owner@example.com")
    admin, admin_headers = _create_user(workspace, "notifications-admin@example.com", admin=True)
    _seed_orphan(workspace, owner, "用户的子分类")
    _seed_orphan(workspace, admin, "管理员自己的子分类")

    assert client.get("/api/v1/notifications").status_code == 401
    [personal] = _fetch(client, owner_headers)
    [admin_notice] = _fetch(client, admin_headers)
    assert personal["type"] == "category_missing_parent"
    assert personal["unread"] is True and personal["count"] == 1
    assert personal["categories"] == [
        {"id": "child", "name": "用户的子分类", "parent_name": "旧分类"}
    ]
    assert admin_notice["categories"][0]["name"] == "管理员自己的子分类"
    assert admin_notice["id"] != personal["id"]
    _mark(client, admin_headers, personal["id"])
    assert _fetch(client, owner_headers)[0]["unread"] is True
    with sessions() as db:
        assert db.get(UserNotificationRead, (admin, personal["id"])) is None

    _mark(client, owner_headers, personal["id"], personal["id"])
    _mark(client, owner_headers, personal["id"])
    second_device = _login_web(client, "notifications-owner@example.com")
    headers = {"Authorization": f"Bearer {second_device['access_token']}"}
    assert _fetch(client, headers) == [{**personal, "unread": False}]


def test_notifications_resolve_and_new_issues_remain_unread(workspace):
    client, sessions = workspace
    user_id, headers = _create_user(workspace, "notifications-resolution@example.com")
    assert _fetch(client, headers) == []
    _seed_orphan(workspace, user_id, "旧子分类")
    [notice] = _fetch(client, headers)
    _mark(client, headers, notice["id"])

    with sessions() as db:
        # 重新关联父分类后，已读通知从中心消失。
        db.add(
            UserCategoryProjection(
                user_id=user_id, sync_id="parent", name="父分类", kind="expense", level=1
            )
        )
        db.commit()
    assert _fetch(client, headers) == []

    _seed_orphan(workspace, user_id, "新子分类", sync_id="new-child", parent_id="missing")
    [new_notice] = _fetch(client, headers)
    assert new_notice["id"] != notice["id"]
    _mark(client, headers, notice["id"])
    assert _fetch(client, headers) == [new_notice]
    assert new_notice["unread"] is True and new_notice["categories"][0]["name"] == "新子分类"
