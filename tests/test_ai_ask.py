"""POST /api/v1/ai/ask 端到端测试。

设计:.docs/web-cmdk-ai-doc-search.md

覆盖:
1. 用户没配 chat provider → 400 + AI_NO_CHAT_PROVIDER
2. 索引为空 → 503 + AI_DOCS_INDEX_EMPTY
3. server 没配 EMBEDDING_API_KEY → 503 + AI_EMBEDDING_UNAVAILABLE
4. 配齐了 → SSE stream(mock 上游 API)
5. provider 返 401 / network error → SSE error event(stream 已开)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import UserProfile
from src.services.ai import docs_index as docs_index_module


# ──────────────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────────────


def _make_client() -> TestClient:
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
    return TestClient(app)


def _register_and_token(client: TestClient, email: str = "ai@test.com") -> tuple[str, str]:
    """注册 web client_type 用户。返回 (access_token, user_id)。

    user_id 通过查 DB 拿 — login 接口只返 access_token,不返 user 信息。"""
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "ai-dev",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "ai-dev",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    token = r.json()["access_token"]
    # 查 DB 拿 user_id
    from sqlalchemy import select
    from src.models import User
    db = next(app.dependency_overrides[get_db]())
    try:
        user = db.scalar(select(User).where(User.email == email))
        return token, user.id
    finally:
        db.close()


def _make_fake_index(tmp_path: Path, lang: str = "zh", chunks_count: int = 3) -> Path:
    """造一个假的索引文件,跟 build_docs_index.py 输出格式一样。
    向量是随机的,反正 mock embedding 返回的也是兼容 dim 的随机向量。"""
    db_path = tmp_path / f"docs-index.{lang}.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                doc_path TEXT NOT NULL,
                doc_title TEXT,
                section TEXT,
                url TEXT,
                vector BLOB NOT NULL
            );
            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        rng = np.random.default_rng(42)
        dim = 8  # 小一点,测试用够了
        for i in range(chunks_count):
            vec = rng.standard_normal(dim).astype(np.float32).tobytes()
            conn.execute(
                "INSERT INTO chunks (content, doc_path, doc_title, section, url, vector) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"chunk {i} content about 2FA setup step {i}",
                    f"security/two-factor.md#{i}",
                    "2FA 启用",
                    f"section-{i}",
                    f"https://count.beejz.com/docs/security/two-factor#{i}",
                    vec,
                ),
            )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?), (?, ?)",
            ("dim", str(dim), "embedding_model", "fake-test"),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture(autouse=True)
def reset_index_cache():
    docs_index_module.reset_docs_index_cache()
    yield
    docs_index_module.reset_docs_index_cache()


@pytest.fixture
def fake_index_dir(tmp_path, monkeypatch):
    """指向 tmp_path 作为索引目录。"""
    monkeypatch.setattr(docs_index_module, "_DATA_DIR", tmp_path)
    return tmp_path


# ──────────────────────────────────────────────────────────────────────
# tests
# ──────────────────────────────────────────────────────────────────────


def test_ask_requires_auth():
    client = _make_client()
    try:
        r = client.post("/api/v1/ai/ask", json={"query": "hi"})
        assert r.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()


def test_ask_no_chat_provider_returns_400(fake_index_dir):
    """用户没配 ai_config → AI_NO_CHAT_PROVIDER。索引必须 exist 否则会先 503。"""
    _make_fake_index(fake_index_dir)
    client = _make_client()
    try:
        token, _ = _register_and_token(client)
        r = client.post(
            "/api/v1/ai/ask",
            json={"query": "怎么开 2FA", "locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400, r.text
        # error_handling.py 把 dict detail 拆开:message → error.message,
        # 其它字段(error_code)上抛到 body 顶层
        body = r.json()
        assert body["error_code"] == "AI_NO_CHAT_PROVIDER", body
    finally:
        app.dependency_overrides.clear()


def test_ask_docs_index_empty_returns_503(fake_index_dir, monkeypatch):
    """索引文件不存在 → AI_DOCS_INDEX_EMPTY。"""
    # 不调 _make_fake_index → fake_index_dir 是空目录 → DocsIndex.is_empty
    monkeypatch.setenv("EMBEDDING_API_KEY", "fake")
    client = _make_client()
    try:
        token, user_id = _register_and_token(client)
        # 即便用户配了 ai_config,index 没 build 也是 503
        from src.database import get_db
        db = next(app.dependency_overrides[get_db]())
        db.add(UserProfile(
            user_id=user_id,
            ai_config_json=json.dumps({
                "providers": [{
                    "id": "zhipu_glm", "apiKey": "sk-x", "baseUrl": "https://x",
                    "textModel": "glm-4-flash",
                }],
                "binding": {"textProviderId": "zhipu_glm"},
            }),
        ))
        db.commit()
        db.close()
        r = client.post(
            "/api/v1/ai/ask",
            json={"query": "怎么开 2FA", "locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 503
        body = r.json()
        assert body["error_code"] == "AI_DOCS_INDEX_EMPTY", body
    finally:
        app.dependency_overrides.clear()


def test_ask_embedding_unconfigured_returns_503(fake_index_dir, monkeypatch):
    """server 没配 EMBEDDING_API_KEY → AI_EMBEDDING_UNAVAILABLE。"""
    _make_fake_index(fake_index_dir)
    # 强制 settings.embedding_api_key 为空 — Settings 是 lru_cached,改 attr 即可
    from src.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "embedding_api_key", "")

    client = _make_client()
    try:
        token, user_id = _register_and_token(client)
        from src.database import get_db
        db = next(app.dependency_overrides[get_db]())
        db.add(UserProfile(
            user_id=user_id,
            ai_config_json=json.dumps({
                "providers": [{
                    "id": "zhipu_glm", "apiKey": "sk-x", "baseUrl": "https://x",
                    "textModel": "glm-4-flash",
                }],
                "binding": {"textProviderId": "zhipu_glm"},
            }),
        ))
        db.commit()
        db.close()
        r = client.post(
            "/api/v1/ai/ask",
            json={"query": "怎么开 2FA"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 503
        body = r.json()
        assert body["error_code"] == "AI_EMBEDDING_UNAVAILABLE", body
    finally:
        app.dependency_overrides.clear()


def test_ask_happy_path_streams_chunks_and_sources(fake_index_dir, monkeypatch):
    """配齐 + index + provider + embedding 都 mock,验证 SSE event 序列。"""
    _make_fake_index(fake_index_dir, chunks_count=3)
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "embedding_api_key", "fake-server-key")

    # mock embed_query → 8 维(跟假索引 dim 对齐)
    async def fake_embed(query):
        return [0.1] * 8

    # mock stream_chat_completion → yield 几段文字
    async def fake_stream(*, config, messages, timeout=30.0):
        yield "在"
        yield "个人资料页"
        yield "点开 2FA。"

    monkeypatch.setattr("src.routers.ai.ask.embed_query", fake_embed)
    monkeypatch.setattr("src.routers.ai.ask.stream_chat_completion", fake_stream)

    client = _make_client()
    try:
        token, user_id = _register_and_token(client)
        from src.database import get_db
        db = next(app.dependency_overrides[get_db]())
        db.add(UserProfile(
            user_id=user_id,
            ai_config_json=json.dumps({
                "providers": [{
                    "id": "zhipu_glm", "apiKey": "sk-real", "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                    "textModel": "glm-4-flash",
                }],
                "binding": {"textProviderId": "zhipu_glm"},
            }),
        ))
        db.commit()
        db.close()

        r = client.post(
            "/api/v1/ai/ask",
            json={"query": "怎么开 2FA", "locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        body = r.content.decode("utf-8")
        # 应包含 3 个 chunk + 1 个 sources + 1 个 done
        events = [json.loads(line[len("data: "):])
                  for line in body.split("\n\n")
                  if line.startswith("data: ")]
        types = [e["type"] for e in events]
        assert types == ["chunk", "chunk", "chunk", "sources", "done"]
        # chunk 文字凑回去 = mock 的全文
        full = "".join(e["text"] for e in events if e["type"] == "chunk")
        assert full == "在个人资料页点开 2FA。"
        # sources 含 url
        sources = [e for e in events if e["type"] == "sources"][0]["items"]
        assert len(sources) == 3
        assert all(s["url"].startswith("https://count.beejz.com/docs/") for s in sources)
    finally:
        app.dependency_overrides.clear()


def test_ask_provider_error_emits_error_event(fake_index_dir, monkeypatch):
    """chat provider 返错 → stream 里 emit error event,不抛 5xx。"""
    _make_fake_index(fake_index_dir)
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "embedding_api_key", "fake-server-key")

    async def fake_embed(query):
        return [0.1] * 8

    async def fake_stream_fails(*, config, messages, timeout=30.0):
        from src.services.ai.provider_client import ChatProviderError
        # 必须在 yield 前抛(模拟 provider 401),验证错误 event 也能下发
        raise ChatProviderError("provider returned 401: invalid api_key")
        yield  # noqa  让 mypy 当 async generator

    monkeypatch.setattr("src.routers.ai.ask.embed_query", fake_embed)
    monkeypatch.setattr("src.routers.ai.ask.stream_chat_completion", fake_stream_fails)

    client = _make_client()
    try:
        token, user_id = _register_and_token(client)
        from src.database import get_db
        db = next(app.dependency_overrides[get_db]())
        db.add(UserProfile(
            user_id=user_id,
            ai_config_json=json.dumps({
                "providers": [{
                    "id": "zhipu_glm", "apiKey": "sk-bad", "baseUrl": "https://x",
                    "textModel": "glm-4-flash",
                }],
                "binding": {"textProviderId": "zhipu_glm"},
            }),
        ))
        db.commit()
        db.close()

        r = client.post(
            "/api/v1/ai/ask",
            json={"query": "test", "locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        body = r.content.decode("utf-8")
        events = [json.loads(line[len("data: "):])
                  for line in body.split("\n\n")
                  if line.startswith("data: ")]
        types = [e["type"] for e in events]
        assert "error" in types
        err = [e for e in events if e["type"] == "error"][0]
        assert err["error_code"] == "AI_PROVIDER_ERROR"
        assert "401" in err["message"]
    finally:
        app.dependency_overrides.clear()
