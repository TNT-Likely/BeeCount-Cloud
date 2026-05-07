"""POST /api/v1/ai/test-provider 单元测试。

mock httpx 上游响应,验证 endpoint 行为:
1. text/vision/speech happy path → 200 + success=true + preview/latency
2. 401 → success=false + AI_TEST_AUTH
3. 404 / "model not found" → AI_TEST_MODEL_NOT_FOUND
4. timeout → AI_TEST_TIMEOUT
5. 速率限制(60s 内 31 次)→ 429 AI_TEST_RATE_LIMITED
6. capability=vision 但 visionModel 空 → AI_TEST_MISSING_FIELDS
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


def _register_and_login(client: TestClient, email: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": "d-web",
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
            "device_id": "d-web",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "test",
        },
    )
    return r.json()["access_token"]


@pytest.fixture(autouse=True)
def _embedding_key(monkeypatch):
    """跟其它 ai test 一样,跳过 embedding key 校验(不用)。"""
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "embedding_api_key", "fake")
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """每个 case 开始前清空速率限制 deque,避免上一个 case 残留影响。"""
    from src.routers.ai.test_provider import _RATE_WINDOWS
    _RATE_WINDOWS.clear()
    yield
    _RATE_WINDOWS.clear()


# ──────────────────── happy path ────────────────────


def test_text_capability_happy_path():
    async def fake_post(self, url, headers=None, json=None, **_):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Hi! How can I help?"}}]},
        )

    client = _make_client()
    try:
        token = _register_and_login(client, "tp1@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            r = client.post(
                "/api/v1/ai/test-provider",
                json={
                    "provider": {
                        "name": "X",
                        "apiKey": "sk-xx",
                        "baseUrl": "https://example.com/v1",
                        "textModel": "gpt-test",
                    },
                    "capability": "text",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert "Hi" in body["preview"]
        assert body["latency_ms"] >= 0
    finally:
        app.dependency_overrides.clear()


def test_vision_capability_happy_path():
    async def fake_post(self, url, headers=None, json=None, **_):
        # 验证调用方传了 image_url
        assert json is not None
        msg = json["messages"][0]["content"]
        assert any(part.get("type") == "image_url" for part in msg)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "a red square"}}]},
        )

    client = _make_client()
    try:
        token = _register_and_login(client, "tp2@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            r = client.post(
                "/api/v1/ai/test-provider",
                json={
                    "provider": {
                        "apiKey": "sk-xx",
                        "baseUrl": "https://example.com/v1",
                        "visionModel": "gpt-vision",
                    },
                    "capability": "vision",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert "red" in body["preview"]
    finally:
        app.dependency_overrides.clear()


def test_speech_capability_happy_path():
    async def fake_post(self, url, headers=None, files=None, data=None, **_):
        # 静音 WAV 上游可能返空 text → 仍算成功
        return httpx.Response(200, json={"text": ""})

    client = _make_client()
    try:
        token = _register_and_login(client, "tp3@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            r = client.post(
                "/api/v1/ai/test-provider",
                json={
                    "provider": {
                        "apiKey": "sk-xx",
                        "baseUrl": "https://example.com/v1",
                        "audioModel": "whisper-1",
                    },
                    "capability": "speech",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert body["preview"] == ""
    finally:
        app.dependency_overrides.clear()


# ──────────────────── 错误归类 ────────────────────


def test_401_returns_auth_error():
    async def fake_post(self, url, **_):
        return httpx.Response(401, text='{"error":"Invalid API key"}')

    client = _make_client()
    try:
        token = _register_and_login(client, "tp4@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            r = client.post(
                "/api/v1/ai/test-provider",
                json={
                    "provider": {
                        "apiKey": "sk-bad",
                        "baseUrl": "https://example.com/v1",
                        "textModel": "x",
                    },
                    "capability": "text",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "AI_TEST_AUTH"
        assert "401" in body["error_message"]
    finally:
        app.dependency_overrides.clear()


def test_404_model_not_found():
    async def fake_post(self, url, **_):
        return httpx.Response(404, text='{"error":"model not found"}')

    client = _make_client()
    try:
        token = _register_and_login(client, "tp5@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            r = client.post(
                "/api/v1/ai/test-provider",
                json={
                    "provider": {
                        "apiKey": "sk-x",
                        "baseUrl": "https://example.com/v1",
                        "textModel": "missing-model",
                    },
                    "capability": "text",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "AI_TEST_MODEL_NOT_FOUND"
    finally:
        app.dependency_overrides.clear()


def test_timeout_returns_timeout_code():
    async def fake_post(self, url, **_):
        raise httpx.TimeoutException("connect timeout")

    client = _make_client()
    try:
        token = _register_and_login(client, "tp6@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            r = client.post(
                "/api/v1/ai/test-provider",
                json={
                    "provider": {
                        "apiKey": "sk-x",
                        "baseUrl": "https://example.com/v1",
                        "textModel": "x",
                    },
                    "capability": "text",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "AI_TEST_TIMEOUT"
    finally:
        app.dependency_overrides.clear()


# ──────────────────── 速率限制 ────────────────────


def test_rate_limit_after_30_requests():
    async def fake_post(self, url, **_):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    client = _make_client()
    try:
        token = _register_and_login(client, "tp7@test.com")
        with patch("httpx.AsyncClient.post", fake_post):
            payload = {
                "provider": {
                    "apiKey": "sk-x",
                    "baseUrl": "https://example.com/v1",
                    "textModel": "x",
                },
                "capability": "text",
            }
            # 前 30 次都 200
            for i in range(30):
                r = client.post(
                    "/api/v1/ai/test-provider",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert r.status_code == 200, f"call {i} failed: {r.text}"
            # 第 31 次 429
            r = client.post(
                "/api/v1/ai/test-provider",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 429, r.text
            # 全局 ApiError 处理器把 HTTPException(detail={...}) 摊平成 root.error_code
            assert r.json()["error_code"] == "AI_TEST_RATE_LIMITED"
    finally:
        app.dependency_overrides.clear()


# ──────────────────── 字段校验 ────────────────────


def test_vision_capability_with_empty_model_returns_missing_fields():
    client = _make_client()
    try:
        token = _register_and_login(client, "tp8@test.com")
        r = client.post(
            "/api/v1/ai/test-provider",
            json={
                "provider": {
                    "apiKey": "sk-x",
                    "baseUrl": "https://example.com/v1",
                    "visionModel": "",  # 空
                },
                "capability": "vision",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == "AI_TEST_MISSING_FIELDS"
    finally:
        app.dependency_overrides.clear()
