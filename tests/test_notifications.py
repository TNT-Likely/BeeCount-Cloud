from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_category_delete_validation import _login_web, _register
from test_user_global_sync import _make_client

from src.config import get_settings
from src.main import app
from src.models import (
    BackupRun,
    BackupSchedule,
    Device,
    DeviceSyncFailure,
    User,
    UserCategoryProjection,
    UserNotificationRead,
)
from src.routers.sync import pull, push


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


def plan(workspace, owner, *, since=None, cron="0 4 * * *"):
    with workspace[1]() as db:
        row = BackupSchedule(
            name="家庭夜间备份",
            user_id=owner,
            cron_expr=cron,
            cron_effective_at=since or datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        return row.id


def run(workspace, owner, schedule, status):
    with workspace[1]() as db:
        now = datetime.now(timezone.utc)
        row = BackupRun(
            user_id=owner,
            schedule_id=schedule,
            status=status,
            started_at=now,
            finished_at=None if status == "running" else now,
            log_text="private log",
        )
        db.add(row)
        db.commit()
        return row.id


def test_backup_incident_lifecycle_and_scope(workspace):
    client, sessions = workspace
    owner, headers = _create_user(workspace, "backup-admin@test.com", admin=True)
    _, other_headers = _create_user(workspace, "backup-other@test.com", admin=True)
    member, member_headers = _create_user(workspace, "backup-member@test.com")
    schedule = plan(workspace, owner)
    run(workspace, owner, schedule, "failed")
    assert _fetch(client, headers) == []
    failed = run(workspace, owner, schedule, "failed")
    [notice] = _fetch(client, headers)
    assert (notice["reason"], notice["count"], notice["run_id"]) == ("failed", 2, failed)
    assert "private log" not in str(notice)
    _mark(client, headers, notice["id"])
    run(workspace, owner, schedule, "running")
    partial = run(workspace, owner, schedule, "partial")
    [again] = _fetch(client, headers)
    assert (again["id"], again["unread"], again["run_id"]) == (notice["id"], False, partial)
    assert _fetch(client, other_headers) == []
    run(workspace, member, plan(workspace, member), "partial")
    assert _fetch(client, member_headers) == []
    run(workspace, owner, schedule, "succeeded")
    assert _fetch(client, headers) == []
    run(workspace, owner, schedule, "partial")
    [new] = _fetch(client, headers)
    assert new["id"] != notice["id"] and new["unread"]
    with sessions() as db:
        db.get(BackupSchedule, schedule).enabled = False
        db.commit()
    assert _fetch(client, headers) == []


def test_overdue_uses_effective_cron_not_cached_next_run(workspace, monkeypatch):
    client, sessions = workspace
    owner, headers = _create_user(workspace, "overdue@test.com", admin=True)
    now = datetime.now(timezone.utc)
    schedule = plan(workspace, owner, since=now - timedelta(hours=2), cron="* * * * *")
    with sessions() as db:
        row = db.get(BackupSchedule, schedule)
        row.next_run_at = now + timedelta(minutes=1)  # a restart recalculated the future slot
        db.commit()
    monkeypatch.setattr(get_settings(), "backup_scheduler_enabled", True)
    [notice] = _fetch(client, headers)
    assert notice["reason"] == "overdue" and notice["run_id"] is None
    assert (
        now - timedelta(hours=2) < datetime.fromisoformat(notice["at"]) < now - timedelta(hours=1)
    )
    monkeypatch.setattr(get_settings(), "backup_scheduler_enabled", False)
    assert _fetch(client, headers) == []
    monkeypatch.setattr(get_settings(), "backup_scheduler_enabled", True)
    run(workspace, owner, schedule, "succeeded")
    assert _fetch(client, headers) == []
    assert (
        client.patch(
            f"/api/v1/admin/backup/schedules/{schedule}",
            headers=headers,
            json={"cron_expr": "0 5 * * *"},
        ).status_code
        == 200
    )
    assert _fetch(client, headers) == []


def device_login(client, email):
    app = _register(client, email)
    web = _login_web(client, email)
    return (
        app["device_id"],
        {"Authorization": "Bearer " + app["access_token"]},
        {"Authorization": "Bearer " + web["access_token"]},
    )


def send(client, device, auth, changes):
    res = client.post(
        "/api/v1/sync/push", headers=auth, json={"device_id": device, "changes": changes}
    )
    assert res.status_code == 200, res.text
    return res.json()


def tx(ledger=None):
    return dict(
        ledger_id=ledger,
        entity_type="transaction",
        entity_sync_id="test-payment",
        action="upsert",
        updated_at=datetime.now(timezone.utc).isoformat(),
        payload={"amount": 1},
    )


def test_device_threshold_read_recovery_conflicts_and_isolation(workspace):
    client, sessions = workspace
    device, app, web = device_login(client, "notices-device@test.com")
    _, other_app, other_web = device_login(client, "notices-other@test.com")
    detail = f"/api/v1/devices/{device}/sync-health"
    for _ in range(2):
        assert send(client, device, app, [tx(), tx()])["rejected"] == 2
    assert _fetch(client, web) == []
    send(client, device, app, [tx()])
    [notice] = _fetch(client, web)
    assert notice["device_id"] == device and notice["failures"][0]["count"] == 3
    assert notice["failures"][0]["reason"] == "changes_rejected"
    _mark(client, web, notice["id"])
    send(client, device, app, [tx()])
    assert (
        client.get("/api/v1/sync/pull", params={"device_id": device}, headers=app).status_code
        == 200
    )
    send(client, device, app, [])
    [same] = _fetch(client, web)
    assert same["id"] == notice["id"] and not same["unread"]
    assert client.get(detail, headers=web).json()["failures"] == same["failures"]
    assert client.get(detail, headers=other_web).status_code == 404
    assert (
        client.post(
            "/api/v1/sync/push", headers=other_app, json={"device_id": device, "changes": [tx()]}
        ).status_code
        == 401
    )
    assert _fetch(client, other_web) == []
    good = tx("demo")
    assert send(client, device, app, [good])["accepted"] == 1
    assert _fetch(client, web) == [] and client.get(detail, headers=web).json()["failures"] == []
    for _ in range(3):
        assert (
            send(client, device, app, [{**good, "updated_at": "2000-01-01T00:00:00Z"}])[
                "conflict_count"
            ]
            == 1
        )
    assert _fetch(client, web) == []
    for _ in range(3):
        send(client, device, app, [tx()])
    [new] = _fetch(client, web)
    assert new["unread"] and new["id"] != notice["id"]
    with sessions() as db:
        db.get(Device, device).revoked_at = datetime.now(timezone.utc)
        db.commit()
    assert _fetch(client, web) == []
    assert client.get(detail, headers=web).status_code == 404


def test_server_failures_rollback_and_recover_independently(workspace, monkeypatch):
    client, sessions = workspace
    device, app, web = device_login(client, "notices-server@test.com")

    def temporarily_unavailable(*args, **kwargs):
        raise HTTPException(503, "synthetic private diagnostics")

    with monkeypatch.context() as patch:
        patch.setattr(push, "apply_change_to_projection", temporarily_unavailable)
        patch.setattr(pull, "list_accessible_ledgers", temporarily_unavailable)
        for _ in range(3):
            res = client.post(
                "/api/v1/sync/push",
                headers=app,
                json={"device_id": device, "changes": [tx("demo")]},
            )
            assert res.status_code == 503
            assert (
                client.get(
                    "/api/v1/sync/pull", headers=app, params={"device_id": device}
                ).status_code
                == 503
            )
    [notice] = _fetch(client, web)
    assert [f["direction"] for f in notice["failures"]] == ["pull", "push"]
    assert "private diagnostics" not in str(notice)
    assert "receipt_ids" not in notice
    _mark(client, web, notice["id"])
    assert (
        client.get("/api/v1/sync/pull", headers=app, params={"device_id": device}).status_code
        == 200
    )
    [remaining] = _fetch(client, web)
    assert [f["direction"] for f in remaining["failures"]] == ["push"]
    assert not remaining["unread"]
    assert send(client, device, app, [tx("demo")])["accepted"] == 1
    assert _fetch(client, web) == []
    with sessions() as db:
        assert not db.scalars(select(DeviceSyncFailure)).all()


def test_bulk_reads_only_acknowledge_displayed_incidents(workspace):
    client, _ = workspace
    owner, headers = _create_user(workspace, "many-plans@test.com", admin=True)
    for _ in range(22):
        run(workspace, owner, plan(workspace, owner), "partial")
    known = _fetch(client, headers)
    assert len(known) == 22
    _seed_orphan(workspace, owner, "后来出现的异常")
    _mark(client, headers, *(item["id"] for item in known))
    active = _fetch(client, headers)
    assert [item["type"] for item in active if item["unread"]] == ["category_missing_parent"]
