"""Admin backup API 端到端 — 不实际跑 rclone(没装 / 没远端),只验证
DB 写入 / 路由权限 / schema 校验。"""
from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


_TEST_SESSION: sessionmaker | None = None


def _make_client() -> TestClient:
    global _TEST_SESSION
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    _TEST_SESSION = TS

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _register_app(client: TestClient, email: str, *, is_admin: bool = True) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _login_web(client: TestClient, email: str) -> str:
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
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _bootstrap_admin(client: TestClient, email: str) -> str:
    """register an admin and return web token."""
    user_data = _register_app(client, email)
    user_id = user_data["user"]["id"]
    # 直接通过测试 DB 把 is_admin=True(不能用 SessionLocal,那是文件 DB)
    from src.models import User

    assert _TEST_SESSION is not None
    db = _TEST_SESSION()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        assert user is not None, "user just registered should exist in test DB"
        user.is_admin = True
        db.commit()
    finally:
        db.close()
    return _login_web(client, email)


@patch("src.routers.admin_backup._config_manager")
def test_create_remote_persists_and_writes_conf(mock_cfg_mgr) -> None:
    mock_cfg_mgr.return_value.rewrite_from_db.return_value = None
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "admin@example.com")
        res = client.post(
            "/api/v1/admin/backup/remotes",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "myb2",
                "backend_type": "b2",
                "config": {
                    "account": "acc-x",
                    "key": "key-secret",
                    "bucket": "my-bucket",
                },
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["name"] == "myb2"
        assert body["backend_type"] == "b2"
        # 敏感字段在 summary 里被打码
        assert body["config_summary"]["key"] == "******"
        # rclone.conf 写入被调用
        assert mock_cfg_mgr.return_value.rewrite_from_db.called

        # GET 列表能看到
        res = client.get(
            "/api/v1/admin/backup/remotes",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        rows = res.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "myb2"
    finally:
        app.dependency_overrides.clear()


@patch("src.routers.admin_backup._config_manager")
def test_create_schedule_with_remotes_validates_ownership(mock_cfg_mgr) -> None:
    mock_cfg_mgr.return_value.rewrite_from_db.return_value = None
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "a2@example.com")
        # 先建一个 remote
        res = client.post(
            "/api/v1/admin/backup/remotes",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "remote-a",
                "backend_type": "local",
                "config": {},
            },
        )
        assert res.status_code == 201
        rid = res.json()["id"]

        # 创建 schedule 引用它
        res = client.post(
            "/api/v1/admin/backup/schedules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "daily",
                "cron_expr": "0 4 * * *",
                "retention_days": 7,
                "include_attachments": True,
                "enabled": True,
                "remote_ids": [rid],
            },
        )
        assert res.status_code == 201, res.text
        sched = res.json()
        assert sched["name"] == "daily"
        assert sched["remote_ids"] == [rid]

        # 引用不存在的 remote → 400
        res = client.post(
            "/api/v1/admin/backup/schedules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "bad",
                "cron_expr": "0 4 * * *",
                "retention_days": 7,
                "include_attachments": True,
                "enabled": True,
                "remote_ids": [9999],
            },
        )
        assert res.status_code == 400

        # 非法 cron → 400
        res = client.post(
            "/api/v1/admin/backup/schedules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "bad",
                "cron_expr": "not a cron",
                "retention_days": 7,
                "include_attachments": True,
                "enabled": True,
                "remote_ids": [rid],
            },
        )
        assert res.status_code == 400
    finally:
        app.dependency_overrides.clear()


@patch("src.routers.admin_backup._config_manager")
def test_delete_remote_blocked_when_referenced_by_schedule(mock_cfg_mgr) -> None:
    mock_cfg_mgr.return_value.rewrite_from_db.return_value = None
    client = _make_client()
    try:
        token = _bootstrap_admin(client, "a3@example.com")
        res = client.post(
            "/api/v1/admin/backup/remotes",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "r1", "backend_type": "local", "config": {}},
        )
        rid = res.json()["id"]
        res = client.post(
            "/api/v1/admin/backup/schedules",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "name": "s",
                "cron_expr": "0 4 * * *",
                "retention_days": 7,
                "include_attachments": True,
                "enabled": True,
                "remote_ids": [rid],
            },
        )
        sid = res.json()["id"]

        res = client.delete(
            f"/api/v1/admin/backup/remotes/{rid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 409

        # 删 schedule 后再删 remote 就行
        client.delete(
            f"/api/v1/admin/backup/schedules/{sid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        res = client.delete(
            f"/api/v1/admin/backup/remotes/{rid}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 204
    finally:
        app.dependency_overrides.clear()


def test_non_admin_blocked() -> None:
    client = _make_client()
    try:
        # 非 admin user
        _register_app(client, "user@example.com")
        token = _login_web(client, "user@example.com")
        res = client.get(
            "/api/v1/admin/backup/remotes",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()
