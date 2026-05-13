"""PAT lifecycle tests + 严格分流保证.

覆盖:
1. 创建 / 列表 / 撤销
2. 列表只返 prefix,不返明文
3. PAT 不能调常规 API endpoint (`/profile/me` 等) — 必须 403
4. JWT access token 不能调 MCP endpoint (`/api/v1/mcp/*`) — 必须 403
5. 无 auth 头 / 错 PAT 调 MCP — 必须 401
6. PAT 自己不能创新 PAT — 防止泄露后自我续期(由 _require_jwt_only 保证)
"""
from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.mcp import auth as mcp_auth


def _make_client(monkeypatch) -> TestClient:
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
    # 关键:MCP middleware 不走 FastAPI dep tree,直接用 `SessionLocal()`。
    # 必须把 mcp.auth 模块绑定的引用也指向 test engine,否则 PAT 校验会落到
    # 真实 DB,测试看不到刚 POST 创建的 token。
    monkeypatch.setattr(mcp_auth, "SessionLocal", testing_session)
    return TestClient(app)


def _register(client: TestClient, email: str = "owner@example.com") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_create_pat_returns_plaintext_once_only(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]

        res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Claude Desktop", "scopes": ["mcp:read"], "expires_in_days": 30},
        )
        assert res.status_code == 201, res.text
        body = res.json()
        # 明文 token 仅创建时返
        assert body["token"].startswith("bcmcp_")
        assert body["prefix"].startswith("bcmcp_")
        assert len(body["prefix"]) == 14
        assert body["name"] == "Claude Desktop"
        assert body["scopes"] == ["mcp:read"]
        assert body["expires_at"] is not None

        # 列表里**不**包含明文
        res = client.get(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) == 1
        assert items[0]["id"] == body["id"]
        assert items[0]["prefix"] == body["prefix"]
        assert "token" not in items[0]
    finally:
        app.dependency_overrides.clear()


def test_create_pat_with_no_expiration(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Long-lived", "scopes": ["mcp:read"], "expires_in_days": None},
        )
        assert res.status_code == 201, res.text
        assert res.json()["expires_at"] is None
    finally:
        app.dependency_overrides.clear()


def test_create_pat_rejects_invalid_scope(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Bad", "scopes": ["app_write"]},
        )
        # Pydantic Literal 校验失败
        assert res.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_delete_pat_one_shot_hard_delete(monkeypatch) -> None:
    """DELETE 一发即物理移除,token 立刻失效,行从列表消失。"""
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "ToDelete", "scopes": ["mcp:read"]},
        )
        pat_id = create_res.json()["id"]

        res = client.delete(
            f"/api/v1/profile/pats/{pat_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 204
        listed = client.get(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert listed == []

        # 再 DELETE → 404,已经没了
        res2 = client.delete(
            f"/api/v1/profile/pats/{pat_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res2.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_update_pat_changes_name_and_scopes(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "old", "scopes": ["mcp:read"]},
        )
        pat_id = create_res.json()["id"]

        res = client.patch(
            f"/api/v1/profile/pats/{pat_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "new", "scopes": ["mcp:read", "mcp:write"]},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["name"] == "new"
        assert set(body["scopes"]) == {"mcp:read", "mcp:write"}

        # GET 列表也反映新值
        listed = client.get(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert listed[0]["name"] == "new"
        assert set(listed[0]["scopes"]) == {"mcp:read", "mcp:write"}
    finally:
        app.dependency_overrides.clear()


# 注:`test_update_pat_rejects_revoked_token` 已移除 —— DELETE 一发就物理删除,
# 没有"撤销但未删除"的中间状态可达,409 分支无法通过正常 API 触发。PATCH
# 内的 revoked_at 检查作为防御性代码保留,但不写测试。


def test_pat_list_returns_utc_aware_iso_timestamps(monkeypatch) -> None:
    """回归:naive datetime 序列化时必须强制带 UTC tz,前端才能正确转本地时间。

    过去 SQLite 不保留 tzinfo,Pydantic .isoformat() 输出 `"...:35"` 无 Z,
    浏览器按本地时间解析 → 显示偏移 8 小时。修法是 field_serializer 强制
    `replace(tzinfo=timezone.utc)`。
    """
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "tz", "scopes": ["mcp:read"], "expires_in_days": 30},
        )
        # CreateResponse 的 datetime 也必须有 tz
        body = create_res.json()
        for field in ("expires_at", "created_at"):
            assert body[field] is not None
            assert ("+00:00" in body[field]) or body[field].endswith("Z"), (
                f"{field}={body[field]!r} missing UTC marker"
            )

        listed = client.get(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        for field in ("expires_at", "created_at"):
            v = listed[0][field]
            assert v is not None
            assert ("+00:00" in v) or v.endswith("Z"), f"{field}={v!r}"
    finally:
        app.dependency_overrides.clear()


def test_user_a_cannot_see_user_b_pat(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        user_a = _register(client, email="alice@example.com")
        user_b = _register(client, email="bob@example.com")
        token_a = user_a["access_token"]
        token_b = user_b["access_token"]

        # A 创建
        a_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"name": "A's", "scopes": ["mcp:read"]},
        )
        a_pat_id = a_res.json()["id"]

        # B 列出 → 空
        b_list = client.get(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_list.status_code == 200
        assert b_list.json() == []

        # B 撤销 A 的 → 404
        b_revoke = client.delete(
            f"/api/v1/profile/pats/{a_pat_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b_revoke.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 严格分流:PAT 不能调常规 API
# ---------------------------------------------------------------------------


def test_pat_cannot_call_profile_me(monkeypatch) -> None:
    """PAT 走非 MCP 路径必须 403 — 防止 LLM 客户端用 mcp token 调常规 API。"""
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "MCP only", "scopes": ["mcp:read", "mcp:write"]},
        )
        pat_plaintext = create_res.json()["token"]

        # 用 PAT 调 /profile/me — 必须 403
        res = client.get(
            "/api/v1/profile/me",
            headers={"Authorization": f"Bearer {pat_plaintext}"},
        )
        assert res.status_code == 403
        assert "PAT" in res.json().get("detail", "")
    finally:
        app.dependency_overrides.clear()


def test_pat_cannot_create_another_pat(monkeypatch) -> None:
    """PAT 自己不能调 POST /profile/pats — 防止泄露后无限续期/升权。"""
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Seed", "scopes": ["mcp:read", "mcp:write"]},
        )
        pat_plaintext = create_res.json()["token"]

        # 用 PAT 创建新 PAT — 403
        res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {pat_plaintext}"},
            json={"name": "Escalated", "scopes": ["mcp:read", "mcp:write"]},
        )
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 严格分流:MCP middleware 拒绝非 PAT
# ---------------------------------------------------------------------------


def test_mcp_endpoint_rejects_missing_auth(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        # 直接 GET /api/v1/mcp/sse 不带 Authorization
        res = client.get("/api/v1/mcp/sse")
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_mcp_endpoint_rejects_jwt_access_token(monkeypatch) -> None:
    """JWT access token 不能走 MCP — 严格分流。"""
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        res = client.get(
            "/api/v1/mcp/sse",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_mcp_endpoint_rejects_garbage_pat(monkeypatch) -> None:
    """乱写一个 bcmcp_ 开头但 DB 里不存在的 token — 401。"""
    client = _make_client(monkeypatch)
    try:
        res = client.get(
            "/api/v1/mcp/sse",
            headers={"Authorization": "Bearer bcmcp_garbage_token_xxxxxxxxxxxxxxxxxx"},
        )
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_mcp_endpoint_rejects_revoked_pat(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Will be revoked", "scopes": ["mcp:read"]},
        )
        pat_plaintext = create_res.json()["token"]
        pat_id = create_res.json()["id"]

        # 撤销
        client.delete(
            f"/api/v1/profile/pats/{pat_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        # 用已撤销 PAT 调 MCP — 401
        res = client.get(
            "/api/v1/mcp/sse",
            headers={"Authorization": f"Bearer {pat_plaintext}"},
        )
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Hash 一致性 — 防止 schema 漂移
# ---------------------------------------------------------------------------


def test_pat_token_hash_matches_sha256_hex(monkeypatch) -> None:
    """db 落的 token_hash 必须是 sha256(plaintext) hex,跟 middleware 校验
    一致;否则任何一边的 hash 算法变了都对不上,LLM 就连不上 MCP。"""
    client = _make_client(monkeypatch)
    try:
        user = _register(client)
        token = user["access_token"]
        create_res = client.post(
            "/api/v1/profile/pats",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "HashCheck", "scopes": ["mcp:read"]},
        )
        plaintext = create_res.json()["token"]
        expected_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

        # 直接查 DB(用 mcp_auth.SessionLocal,已被 monkeypatch 到 test engine)
        from src.models import PersonalAccessToken
        from sqlalchemy import select

        with mcp_auth.SessionLocal() as db:
            row = db.scalar(
                select(PersonalAccessToken).where(
                    PersonalAccessToken.id == create_res.json()["id"]
                )
            )
            assert row is not None
            assert row.token_hash == expected_hash
    finally:
        app.dependency_overrides.clear()
