"""MCP tool 调用历史落库 + 列表查询测试。

覆盖:
- 每次 tool 调用都落一行(成功 / 失败)
- 列表 endpoint 按时间倒序、支持 tool_name / status 过滤
- args_summary 不含 `note` / `text` 等敏感字段
- 用户隔离:用户 A 看不到 B 的日志
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.mcp import auth as mcp_auth
from src.mcp.server import _summarize_args, _write_call_log
from src.models import MCPCallLog, PersonalAccessToken, User
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
    # _write_call_log 也用 `database.SessionLocal` —— 替换模块级引用
    import src.mcp.server as mcp_server
    monkeypatch.setattr(mcp_server, "SessionLocal", Session)
    return Session


def _register(client: TestClient, email: str = "u@x") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_summarize_args_skips_sensitive_fields() -> None:
    """`note` / `text` 含自由文本,绝不进 args_summary。"""
    s = _summarize_args({
        "amount": 38.5,
        "tx_type": "expense",
        "category": "咖啡",
        "note": "今天心情不好,在咖啡店哭了一下午",  # 必须被跳过
        "text": "我今天在 xxx 花了 38",  # 必须被跳过
    })
    assert s is not None
    assert "amount=38.5" in s
    assert "tx_type=expense" in s
    assert "category=咖啡" in s
    assert "note" not in s
    assert "心情" not in s
    assert "text" not in s


def test_summarize_args_truncates_long_strings() -> None:
    s = _summarize_args({"category": "x" * 100})
    assert s is not None
    # 单字段被截到 30 字符内,总长度 < 200
    assert len(s) <= 200
    assert "category=" in s


def test_summarize_args_empty_returns_none() -> None:
    assert _summarize_args({}) is None
    assert _summarize_args({"x": None}) is None


def test_write_call_log_persists_row(monkeypatch) -> None:
    Session = _bootstrap(monkeypatch)
    try:
        # 种用户
        with Session() as db:
            u = User(
                id=str(uuid4()),
                email="logger@example.com",
                password_hash=hash_password("x"),
                is_admin=False,
                is_enabled=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(u)
            db.commit()
            user_id = u.id

        _write_call_log(
            user_id=user_id,
            pat_id=None,
            pat_prefix="bcmcp_abc123",
            pat_name="Claude Desktop",
            tool_name="list_ledgers",
            status="ok",
            error=None,
            args_summary="kind=expense",
            duration_ms=42,
            client_ip="127.0.0.1",
        )

        with Session() as db:
            rows = db.scalars(select(MCPCallLog)).all()
            assert len(rows) == 1
            r = rows[0]
            assert r.tool_name == "list_ledgers"
            assert r.status == "ok"
            assert r.error_message is None
            assert r.args_summary == "kind=expense"
            assert r.duration_ms == 42
            assert r.client_ip == "127.0.0.1"
            assert r.pat_prefix == "bcmcp_abc123"
            assert r.pat_name == "Claude Desktop"
    finally:
        app.dependency_overrides.clear()


def test_write_call_log_captures_error(monkeypatch) -> None:
    Session = _bootstrap(monkeypatch)
    try:
        with Session() as db:
            u = User(
                id=str(uuid4()),
                email="err@example.com",
                password_hash=hash_password("x"),
                is_admin=False,
                is_enabled=True,
                created_at=datetime.now(timezone.utc),
            )
            db.add(u)
            db.commit()
            user_id = u.id

        try:
            raise ValueError("not allowed")
        except ValueError as e:
            _write_call_log(
                user_id=user_id, pat_id=None, pat_prefix=None, pat_name=None,
                tool_name="create_transaction", status="error",
                error=e, args_summary=None, duration_ms=10, client_ip=None,
            )

        with Session() as db:
            r = db.scalar(select(MCPCallLog))
            assert r is not None
            assert r.status == "error"
            assert r.error_message is not None
            assert "ValueError" in r.error_message
            assert "not allowed" in r.error_message
    finally:
        app.dependency_overrides.clear()


def test_list_mcp_calls_endpoint(monkeypatch) -> None:
    """API:用户能看自己的调用历史,按时间倒序,支持过滤。"""
    Session = _bootstrap(monkeypatch)
    try:
        client = TestClient(app)
        user = _register(client, email="api@example.com")
        token = user["access_token"]

        # 种几条日志
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "api@example.com"))
            for i in range(5):
                db.add(
                    MCPCallLog(
                        user_id=uid,
                        pat_id=None,
                        pat_prefix="bcmcp_xxxxxxxx",
                        tool_name="list_ledgers" if i % 2 == 0 else "create_transaction",
                        status="ok" if i < 4 else "error",
                        error_message="ValueError: x" if i == 4 else None,
                        args_summary=f"i={i}",
                        duration_ms=10 + i,
                        client_ip="127.0.0.1",
                        called_at=datetime.now(timezone.utc),
                    )
                )
            db.commit()

        # 列出全部
        res = client.get(
            "/api/v1/profile/mcp-calls",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["total"] == 5
        assert len(body["items"]) == 5
        # called_at 倒序 — 大致按 i 倒序(同一时刻种的,顺序不严格,但有 5 条就 OK)

        # called_at 必须带 UTC tz 标记
        for item in body["items"]:
            assert "+00:00" in item["called_at"] or item["called_at"].endswith("Z")
            # 这些种 PAT 都是 None,server 应该降级到 prefix
            assert item["client_active"] is False
            assert item["client_label"] == "bcmcp_xxxxxxxx"

        # 按 tool_name 过滤
        res2 = client.get(
            "/api/v1/profile/mcp-calls?tool_name=list_ledgers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res2.status_code == 200
        items2 = res2.json()["items"]
        assert all(it["tool_name"] == "list_ledgers" for it in items2)

        # 按 status 过滤
        res3 = client.get(
            "/api/v1/profile/mcp-calls?status=error",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res3.status_code == 200
        items3 = res3.json()["items"]
        assert len(items3) == 1
        assert items3[0]["status"] == "error"

        # 分页
        res4 = client.get(
            "/api/v1/profile/mcp-calls?limit=2&offset=2",
            headers={"Authorization": f"Bearer {token}"},
        )
        body4 = res4.json()
        assert body4["total"] == 5
        assert len(body4["items"]) == 2
    finally:
        app.dependency_overrides.clear()


def test_list_mcp_calls_client_label_resolution(monkeypatch) -> None:
    """server 端 LEFT JOIN PAT 决定 client_label:
    - PAT 在 → 用当前 name(改名后立即反映,denorm 那份会被覆盖)
    - PAT 删 → 降级到日志当时缓存的 pat_name
    - 都没有 → 降到 prefix
    """
    Session = _bootstrap(monkeypatch)
    try:
        client = TestClient(app)
        user = _register(client, email="label@example.com")
        token = user["access_token"]

        # 1) 现存 PAT,name 故意跟日志缓存不同 → 期望返回 current name
        with Session() as db:
            uid = db.scalar(select(User.id).where(User.email == "label@example.com"))
            db.add(
                PersonalAccessToken(
                    id="pat-live",
                    user_id=uid,
                    name="Renamed Live",
                    token_hash="x" * 64,
                    prefix="bcmcp_live_xxxx",
                    scopes_json='["mcp:read"]',
                    created_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                MCPCallLog(
                    user_id=uid,
                    pat_id="pat-live",
                    pat_prefix="bcmcp_live_xxxx",
                    pat_name="Original Name",
                    tool_name="list_ledgers",
                    status="ok",
                    args_summary=None,
                    duration_ms=1,
                    client_ip=None,
                    called_at=datetime.now(timezone.utc),
                )
            )
            # 2) PAT 已删(pat_id=None) → 期望降级到 denorm 名字
            db.add(
                MCPCallLog(
                    user_id=uid,
                    pat_id=None,
                    pat_prefix="bcmcp_deld_xxxx",
                    pat_name="Cached Name",
                    tool_name="list_ledgers",
                    status="ok",
                    args_summary=None,
                    duration_ms=1,
                    client_ip=None,
                    called_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

        res = client.get(
            "/api/v1/profile/mcp-calls",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        items = {it["pat_prefix"]: it for it in res.json()["items"]}
        # 现存 PAT → 用 live 当前 name,不是 denorm
        assert items["bcmcp_live_xxxx"]["client_label"] == "Renamed Live"
        assert items["bcmcp_live_xxxx"]["client_active"] is True
        # 已删 PAT → 降级到 denorm
        assert items["bcmcp_deld_xxxx"]["client_label"] == "Cached Name"
        assert items["bcmcp_deld_xxxx"]["client_active"] is False
    finally:
        app.dependency_overrides.clear()


def test_list_mcp_calls_user_isolation(monkeypatch) -> None:
    """用户 A 不能看 B 的调用日志。"""
    Session = _bootstrap(monkeypatch)
    try:
        client = TestClient(app)
        user_a = _register(client, email="alice@example.com")
        user_b = _register(client, email="bob@example.com")
        token_a = user_a["access_token"]
        token_b = user_b["access_token"]

        with Session() as db:
            uid_a = db.scalar(select(User.id).where(User.email == "alice@example.com"))
            db.add(
                MCPCallLog(
                    user_id=uid_a, pat_id=None, pat_prefix=None,
                    tool_name="list_ledgers", status="ok",
                    args_summary="alice secret", duration_ms=1, client_ip=None,
                    called_at=datetime.now(timezone.utc),
                )
            )
            db.commit()

        # B 看不到
        res = client.get(
            "/api/v1/profile/mcp-calls",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert res.status_code == 200
        assert res.json() == {"total": 0, "items": []}

        # A 能看
        res2 = client.get(
            "/api/v1/profile/mcp-calls",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert res2.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()
