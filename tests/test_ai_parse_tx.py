"""B2/B3 端到端测试 — /ai/parse-tx-image + /ai/parse-tx-text + /write/.../transactions/batch。

mock LLM 返回(httpx call_chat_json),验证:
1. parse-tx-image:multipart 上传 + 用户没绑 vision → 400
2. parse-tx-image:happy path 返 tx_drafts + image_id
3. parse-tx-text:用户没绑 chat → 400
4. parse-tx-text:happy path
5. batch 创建:N 笔 + auto AI tag + extra tag + attach_image_id
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import UserProfile


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


def _register_and_login(
    client: TestClient,
    email: str,
    *,
    client_type: str = "web",
) -> tuple[str, str]:
    client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": f"d-{client_type}",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": f"d-{client_type}",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": "test",
        },
    )
    token = r.json()["access_token"]
    from sqlalchemy import select
    from src.models import User
    db = next(app.dependency_overrides[get_db]())
    try:
        user = db.scalar(select(User).where(User.email == email))
        return token, user.id
    finally:
        db.close()


def _seed_ai_config(
    user_id: str,
    *,
    text_model: str = "glm-4-flash",
    vision_model: str | None = "glm-4v-flash",
) -> None:
    from sqlalchemy import select
    cfg = {
        "providers": [{
            "id": "p1",
            "apiKey": "sk-test",
            "baseUrl": "https://example.com/v1",
            "textModel": text_model,
            "visionModel": vision_model or "",
        }],
        "binding": {
            "textProviderId": "p1",
            "visionProviderId": "p1" if vision_model else None,
        },
    }
    db = next(app.dependency_overrides[get_db]())
    try:
        existing = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
        if existing:
            existing.ai_config_json = json.dumps(cfg)
        else:
            db.add(UserProfile(user_id=user_id, ai_config_json=json.dumps(cfg)))
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _embedding_key(monkeypatch):
    """所有测试都默认有 embedding key(parse-tx 用不上,但启动时校验)。"""
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "embedding_api_key", "fake")
    yield


@pytest.fixture(autouse=True)
def _clear_image_cache():
    from src.services.ai.image_cache import clear_cache
    clear_cache()
    yield
    clear_cache()


# ──────────────────────────────────────────────────────────────────────
# parse-tx-image
# ──────────────────────────────────────────────────────────────────────


def test_parse_tx_image_no_vision_provider_returns_400():
    client = _make_client()
    try:
        token, uid = _register_and_login(client, "img1@test.com")
        # 故意不配 vision provider(只配 text)
        _seed_ai_config(uid, vision_model=None)

        files = {"image": ("test.jpg", io.BytesIO(b"fakejpegbytes"), "image/jpeg")}
        r = client.post(
            "/api/v1/ai/parse-tx-image",
            files=files,
            data={"locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "AI_NO_VISION_PROVIDER"
    finally:
        app.dependency_overrides.clear()


def test_parse_tx_image_happy_path(monkeypatch):
    """mock call_chat_json 返合规 tx_drafts → endpoint 返 normalized + image_id。"""
    async def fake_call(**kwargs):
        return {
            "tx_drafts": [
                {
                    "type": "expense",
                    "amount": 35.0,
                    "happened_at": "2026-05-06T12:30:00Z",
                    "category_name": "餐饮",
                    "account_name": "微信",
                    "note": "星巴克",
                    "tags": ["商务"],
                    "confidence": "high",
                },
                {
                    "type": "expense",
                    "amount": 28.0,
                    "happened_at": "2026-05-06T18:00:00Z",
                    "category_name": "交通",
                    "account_name": "",
                    "note": "滴滴",
                    "confidence": "medium",
                },
            ]
        }
    monkeypatch.setattr("src.routers.ai.parse_tx_image.call_chat_json", fake_call)

    client = _make_client()
    try:
        token, uid = _register_and_login(client, "img2@test.com")
        _seed_ai_config(uid)

        files = {"image": ("test.jpg", io.BytesIO(b"fakejpegbytes"), "image/jpeg")}
        r = client.post(
            "/api/v1/ai/parse-tx-image",
            files=files,
            data={"locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["tx_drafts"]) == 2
        assert body["tx_drafts"][0]["amount"] == 35.0
        assert body["tx_drafts"][0]["confidence"] == "high"
        assert body["image_id"]  # cache 应该返了 id
    finally:
        app.dependency_overrides.clear()


def test_parse_tx_image_oversize_rejected():
    client = _make_client()
    try:
        token, uid = _register_and_login(client, "img3@test.com")
        _seed_ai_config(uid)
        # 6MB 超限
        big = b"x" * (6 * 1024 * 1024)
        files = {"image": ("big.jpg", io.BytesIO(big), "image/jpeg")}
        r = client.post(
            "/api/v1/ai/parse-tx-image",
            files=files,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 413, r.text
        assert r.json()["error_code"] == "AI_IMAGE_TOO_LARGE"
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────────────────
# parse-tx-text
# ──────────────────────────────────────────────────────────────────────


def test_parse_tx_text_no_chat_provider_returns_400():
    client = _make_client()
    try:
        token, _ = _register_and_login(client, "txt1@test.com")
        # 不 seed ai_config
        r = client.post(
            "/api/v1/ai/parse-tx-text",
            json={"text": "昨天打车 30", "locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error_code"] == "AI_NO_CHAT_PROVIDER"
    finally:
        app.dependency_overrides.clear()


def test_parse_tx_text_happy_path(monkeypatch):
    async def fake_call(**kwargs):
        return {
            "tx_drafts": [
                {
                    "type": "expense",
                    "amount": 30.0,
                    "happened_at": "2026-05-06T18:00:00Z",
                    "category_name": "交通",
                    "note": "打车",
                    "confidence": "high",
                },
            ]
        }
    monkeypatch.setattr("src.routers.ai.parse_tx_text.call_chat_json", fake_call)

    client = _make_client()
    try:
        token, uid = _register_and_login(client, "txt2@test.com")
        _seed_ai_config(uid)

        r = client.post(
            "/api/v1/ai/parse-tx-text",
            json={"text": "昨天打车 30 块", "locale": "zh"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["tx_drafts"]) == 1
        assert body["tx_drafts"][0]["note"] == "打车"
    finally:
        app.dependency_overrides.clear()


# ──────────────────────────────────────────────────────────────────────
# batch transactions create
# ──────────────────────────────────────────────────────────────────────


def test_batch_create_with_ai_tag_and_extra_tag(monkeypatch):
    """N 笔创建 + 自动 AI 记账 tag + 额外 extra_tag。"""
    client = _make_client()
    try:
        # 用 web client 注册 + 拿 web 写权限
        token, uid = _register_and_login(client, "bat1@test.com", client_type="web")

        # 创建 ledger(走 write/ledgers POST)
        r = client.post(
            "/api/v1/write/ledgers",
            json={"ledger_name": "default", "currency": "CNY"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Device-ID": "d-web",
            },
        )
        assert r.status_code == 200, r.text
        ledger_id = r.json()["entity_id"]

        # batch 创建 2 笔
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch",
            json={
                "base_change_id": 0,
                "transactions": [
                    {
                        "tx_type": "expense",
                        "amount": 35.0,
                        "happened_at": "2026-05-06T12:30:00Z",
                        "note": "星巴克",
                        "tags": [],
                    },
                    {
                        "tx_type": "expense",
                        "amount": 28.0,
                        "happened_at": "2026-05-06T18:00:00Z",
                        "note": "滴滴",
                        "tags": [],
                    },
                ],
                "auto_ai_tag": True,
                "extra_tag_name": "图片记账",
                "locale": "zh",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Device-ID": "d-web",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["created_sync_ids"]) == 2
        assert body["new_change_id"] > 0
    finally:
        app.dependency_overrides.clear()


def test_batch_create_with_image_attachment(monkeypatch):
    """attach_image_id → server 转 attachment + 关联到所有 tx。"""
    from src.services.ai.image_cache import store_image

    client = _make_client()
    try:
        token, uid = _register_and_login(client, "bat2@test.com", client_type="web")

        # 创建 ledger
        r = client.post(
            "/api/v1/write/ledgers",
            json={"ledger_name": "default", "currency": "CNY"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Device-ID": "d-web",
            },
        )
        ledger_id = r.json()["entity_id"]

        # 模拟 ai/parse-tx-image 流程:store_image
        image_id = store_image(
            image_bytes=b"fakejpegbytes",
            mime_type="image/jpeg",
            user_id=uid,
        )

        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch",
            json={
                "base_change_id": 0,
                "transactions": [
                    {
                        "tx_type": "expense",
                        "amount": 35.0,
                        "happened_at": "2026-05-06T12:30:00Z",
                        "note": "星巴克",
                    },
                ],
                "auto_ai_tag": False,  # 简化,只测 attachment
                "attach_image_id": image_id,
                "locale": "zh",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Device-ID": "d-web",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["attachment_id"]  # 应该返了 attachment file_id
        assert len(body["created_sync_ids"]) == 1
    finally:
        app.dependency_overrides.clear()
