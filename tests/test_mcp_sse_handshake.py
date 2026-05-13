"""MCP SSE handshake smoke tests.

历史:Claude Code / Cursor 客户端连 BeeCount Cloud MCP 时报两个错:
  1. `GET /api/v1/mcp/sse` → 500 ValueError 'Request validation failed'
     原因:FastMCP 默认 host=127.0.0.1 时自动开 DNS rebinding 保护,
     allowed_hosts 只放行 `127.0.0.1:* / localhost:* / [::1]:*`,反代或
     测试 Host=testserver 一律 421;PATAuthMiddleware 不拦异常,FastAPI
     兜底返 500。
  2. `GET /.well-known/oauth-protected-resource` → 404,SDK 把 body 当
     OAuth 错误解,FastAPI 默认 `{"detail":"..."}` 缺 `error` 字段,
     SDK 抛 ZodError "SDK auth failed"。

修法:server.py 关掉 transport_security,main.py 加 well-known 路由。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.mcp import auth as mcp_auth
from src.models import PersonalAccessToken, User
from src.security import generate_pat, hash_password


def _bootstrap(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(mcp_auth, "SessionLocal", Session)
    return Session


def _seed_pat(Session) -> str:
    plaintext, h, pfx = generate_pat()
    with Session() as db:
        u = User(
            id=str(uuid4()),
            email="t@x",
            password_hash=hash_password("x"),
            is_admin=False,
            is_enabled=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(u)
        db.flush()
        db.add(
            PersonalAccessToken(
                id=str(uuid4()),
                user_id=u.id,
                name="probe",
                token_hash=h,
                prefix=pfx,
                scopes_json=json.dumps(["mcp:read", "mcp:write"]),
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    return plaintext


def test_oauth_protected_resource_metadata_returns_valid_shape(monkeypatch) -> None:
    """Claude SDK 探测必须拿到能解析的 JSON,否则连 Bearer 静态 fallback 都到
    不了。校验响应体含 `resource` / `bearer_methods_supported`。"""
    _bootstrap(monkeypatch)
    try:
        client = TestClient(app)
        res = client.get("/.well-known/oauth-protected-resource")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["resource"].endswith("/api/v1/mcp")
        assert body["bearer_methods_supported"] == ["header"]
        assert isinstance(body["authorization_servers"], list)

        # 兼容 SDK 按 resource-scoped 路径探测的形式
        res2 = client.get("/.well-known/oauth-protected-resource/api/v1/mcp/sse")
        assert res2.status_code == 200, res2.text
    finally:
        app.dependency_overrides.clear()


def test_dns_rebinding_protection_disabled_via_settings(monkeypatch) -> None:
    """单元层面:确认 server.py 关掉了 transport_security 的 DNS rebinding 保护。
    这是真正决定 421/500 vs 200 的开关。
    """
    _bootstrap(monkeypatch)
    try:
        from src.mcp.server import mcp

        ts = mcp.settings.transport_security
        assert ts is not None, "transport_security should be explicitly set"
        assert ts.enable_dns_rebinding_protection is False, (
            "DNS rebinding protection must be off — Host header is reverse-proxy "
            "controlled, can't be enumerated"
        )
    finally:
        app.dependency_overrides.clear()


def test_pat_with_future_expires_at_resolves(monkeypatch) -> None:
    """回归:`row.expires_at < datetime.now(timezone.utc)` 在 SQLite 上原先
    抛 `TypeError: can't compare offset-naive and offset-aware datetimes`,
    后果是任何带过期日的 PAT 一律 500。本测试种一个 90 天后过期的 PAT,
    确认 MCP middleware 校验通过、user.email 可读(没 detached instance error)。
    """
    from datetime import timedelta

    Session = _bootstrap(monkeypatch)
    plaintext, h, pfx = generate_pat()
    with Session() as db:
        u = User(
            id=str(uuid4()),
            email="future@x",
            password_hash=hash_password("x"),
            is_admin=False,
            is_enabled=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(u)
        db.flush()
        db.add(
            PersonalAccessToken(
                id=str(uuid4()),
                user_id=u.id,
                name="future",
                token_hash=h,
                prefix=pfx,
                scopes_json=json.dumps(["mcp:read"]),
                expires_at=datetime.now(timezone.utc) + timedelta(days=90),
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    try:
        from src.mcp.auth import _resolve_pat_sync

        class _FakeReq:
            client = None

        user, scopes = _resolve_pat_sync(plaintext, _FakeReq())
        # 这两个属性读取过去会因为 commit-expire + expunge 触发 DetachedInstanceError
        assert user.email == "future@x"
        assert user.is_enabled is True
        assert scopes == {"mcp:read"}
    finally:
        app.dependency_overrides.clear()


def test_pat_with_past_expires_at_rejects(monkeypatch) -> None:
    """同时确认过期 PAT 仍然按预期 401。"""
    from datetime import timedelta

    Session = _bootstrap(monkeypatch)
    plaintext, h, pfx = generate_pat()
    with Session() as db:
        u = User(
            id=str(uuid4()),
            email="past@x",
            password_hash=hash_password("x"),
            is_admin=False,
            is_enabled=True,
            created_at=datetime.now(timezone.utc),
        )
        db.add(u)
        db.flush()
        db.add(
            PersonalAccessToken(
                id=str(uuid4()),
                user_id=u.id,
                name="past",
                token_hash=h,
                prefix=pfx,
                scopes_json=json.dumps(["mcp:read"]),
                expires_at=datetime.now(timezone.utc) - timedelta(days=1),
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    try:
        from src.mcp.auth import _AuthError, _resolve_pat_sync

        class _FakeReq:
            client = None

        try:
            _resolve_pat_sync(plaintext, _FakeReq())
            raise AssertionError("Should have raised _AuthError")
        except _AuthError as exc:
            assert exc.code == 401
            assert "expired" in exc.detail.lower()
    finally:
        app.dependency_overrides.clear()


# 端到端 SSE handshake 测试故意不写在 pytest 里:
#   - httpx + ASGITransport 的 SSE stream 在 anyio task group 收尾时不易
#     干净退出,容易让整个 pytest 进程挂死。
#   - 真正的回归保护已经在 test_pat_with_future_expires_at_resolves 和
#     test_pat_with_past_expires_at_rejects 单元层覆盖了 — SSE 500 的两个
#     root cause (TZ-aware/naive 比较 + commit-expire 后 expunge 拿不回
#     attribute) 都在单元层 100% 复现。
#   - 真要看 live SSE,起 server 后 `curl -i http://127.0.0.1:8080/api/v1/
#     mcp/sse -H "Authorization: Bearer bcmcp_..."` 应看到 200 + endpoint
#     event。
