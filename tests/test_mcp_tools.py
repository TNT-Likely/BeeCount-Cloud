"""MCP read tools 的 happy-path 测试.

只覆盖 read 侧:write tools 走 HTTP self-call(in-process ASGI),那个链路
依赖 main.app 实例 + 真实 router,集成度比较高,留在 e2e 测试覆盖。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.mcp.tools import read_tools
from src.models import User


def _make_client_and_engine(monkeypatch):
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
    # read_tools 直接 `with SessionLocal() as db:` —— 不走 dep tree。
    monkeypatch.setattr(read_tools, "SessionLocal", testing_session)
    return TestClient(app), testing_session


def _register(client: TestClient, email: str = "tools@example.com") -> dict:
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


def _make_ledger(client: TestClient, token: str, name: str = "Main") -> str:
    res = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_name": name, "currency": "CNY"},
        headers={"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"},
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _fetch_user(session_maker, email: str) -> User:
    with session_maker() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        db.expunge(user)
        return user


def test_list_ledgers_returns_user_ledgers(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, email="lister@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "Family")
        _make_ledger(client, token, "Personal")
        user = _fetch_user(session_maker, "lister@example.com")

        ledgers = read_tools.list_ledgers(user)
        assert len(ledgers) == 2
        names = {l["name"] for l in ledgers}
        assert names == {"Family", "Personal"}
        for led in ledgers:
            assert led["id"]
            assert led["currency"] == "CNY"
    finally:
        app.dependency_overrides.clear()


def test_get_active_ledger_returns_earliest(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, email="active@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "First")
        _make_ledger(client, token, "Second")
        user = _fetch_user(session_maker, "active@example.com")

        active = read_tools.get_active_ledger(user)
        assert active is not None
        assert active["name"] == "First"
    finally:
        app.dependency_overrides.clear()


def test_get_active_ledger_returns_none_for_no_ledger(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        _register(client, email="empty@example.com")
        user = _fetch_user(session_maker, "empty@example.com")

        active = read_tools.get_active_ledger(user)
        assert active is None
    finally:
        app.dependency_overrides.clear()


def test_list_transactions_empty_ledger(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, email="emptytx@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "Empty")
        user = _fetch_user(session_maker, "emptytx@example.com")

        result = read_tools.list_transactions(user, limit=10)
        assert result["total"] == 0
        assert result["items"] == []
        assert result["ledger"] == "Empty"
    finally:
        app.dependency_overrides.clear()


def test_get_ledger_stats_returns_zero_for_fresh_ledger(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, email="stats@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "Stats")
        user = _fetch_user(session_maker, "stats@example.com")

        stats = read_tools.get_ledger_stats(user)
        assert stats is not None
        assert stats["ledger"] == "Stats"
        assert stats["transaction_count"] == 0
        # 分类/账户/tag 在创建账本时 ensure_default 可能种,允许 >= 0
        assert stats["category_count"] >= 0
        assert stats["account_count"] >= 0
    finally:
        app.dependency_overrides.clear()


def test_search_empty_query_returns_empty(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, email="search@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "S")
        user = _fetch_user(session_maker, "search@example.com")

        # 空 query 直接短路
        out = read_tools.search(user, q="")
        assert out == []
        out = read_tools.search(user, q="   ")
        assert out == []
    finally:
        app.dependency_overrides.clear()


def test_merge_default_tag_dedupes_and_preserves_order() -> None:
    """单元层校验 MCP 默认标签合并逻辑 — 不需要 DB。"""
    from src.mcp.tools.write_tools import _MCP_DEFAULT_TAG, _merge_default_tag

    # LLM 没传 → 只有 MCP
    assert _merge_default_tag(None) == [_MCP_DEFAULT_TAG]
    assert _merge_default_tag([]) == [_MCP_DEFAULT_TAG]

    # LLM 传若干 → MCP 在末尾
    assert _merge_default_tag(["coffee", "work"]) == ["coffee", "work", _MCP_DEFAULT_TAG]

    # LLM 已经传了 MCP → 不重复
    out = _merge_default_tag([_MCP_DEFAULT_TAG, "coffee"])
    assert out == [_MCP_DEFAULT_TAG, "coffee"]

    # 空白 / 空字符串过滤
    assert _merge_default_tag(["  ", "", "x"]) == ["x", _MCP_DEFAULT_TAG]


def test_get_analytics_summary_empty_ledger(monkeypatch) -> None:
    client, session_maker = _make_client_and_engine(monkeypatch)
    try:
        u = _register(client, email="analytics@example.com")
        token = u["access_token"]
        _make_ledger(client, token, "A")
        user = _fetch_user(session_maker, "analytics@example.com")

        summary = read_tools.get_analytics_summary(user, scope="month")
        assert summary["ledger"] == "A"
        assert summary["income"] == 0
        assert summary["expense"] == 0
        assert summary["balance"] == 0
        assert summary["transaction_count"] == 0
        assert summary["top_categories"] == []
    finally:
        app.dependency_overrides.clear()
