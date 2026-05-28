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


def test_batch_create_with_image_attachment(monkeypatch, tmp_path):
    """attach_image_id → server 转 attachment + 关联到所有 tx。"""
    from src.services.ai.image_cache import store_image
    from src import config

    # 把 attachment_storage_dir 重定向到 pytest tmp_path,避免 test 跑完留下
    # 13B 的 "fakejpegbytes" 文件污染 dev 的 ./data/attachments/(scanner 会
    # 把它当成磁盘孤儿报上来)。tmp_path 在 test session 结束自动清理。
    settings = config.get_settings()
    monkeypatch.setattr(
        settings, "attachment_storage_dir", str(tmp_path / "attachments"),
    )

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


def test_batch_create_materializes_auto_tag_entities():
    """B2/B3 LLM 记账场景:auto_ai_tag + extra_tag_name 之前只是字符串塞 tags_csv,
    UserTagProjection 没行,Tags 详情页查不到关联 tx。

    现在 batch 路径会:
    1. 在 snapshot 里实际 create_tag 实体(走 snapshot_mutator + diff emit)
    2. tx payload 引用其 sync_id
    3. UserTagProjection 写入 + ReadTxProjection.tag_sync_ids_json 完整填充
    """
    from sqlalchemy import select
    from src.models import ReadTxProjection, UserTagProjection

    client = _make_client()
    try:
        token, uid = _register_and_login(client, "bat-auto-tag@test.com", client_type="web")
        hdr = {"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"}

        r = client.post(
            "/api/v1/write/ledgers",
            json={"ledger_name": "default", "currency": "CNY"},
            headers=hdr,
        )
        assert r.status_code == 200, r.text
        ledger_id = r.json()["entity_id"]
        change_id = r.json()["new_change_id"]

        # batch 创建,auto_ai_tag=True + extra_tag_name="文字记账"
        # 完全没预先创建任何 tag,期望 server 自动建实体
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch",
            json={
                "base_change_id": change_id,
                "transactions": [{
                    "tx_type": "expense",
                    "amount": 20.0,
                    "happened_at": "2026-05-21T09:47:00Z",
                    "note": "买菜",
                    "tags": [],
                }],
                "auto_ai_tag": True,
                "extra_tag_name": "文字记账",
                "locale": "zh",
            },
            headers=hdr,
        )
        assert r.status_code == 200, r.text
        tx_sync_id = r.json()["created_sync_ids"][0]

        # 验证 1:UserTagProjection 里有「AI记账」+「文字记账」两个 tag 实体
        db = next(app.dependency_overrides[get_db]())
        try:
            tag_rows = db.scalars(
                select(UserTagProjection).where(UserTagProjection.user_id == uid)
            ).all()
            tag_names = {t.name for t in tag_rows}
            assert "AI记账" in tag_names, f"AI记账 tag 实体未创建, 现有={tag_names}"
            assert "文字记账" in tag_names, f"文字记账 tag 实体未创建, 现有={tag_names}"
            name_to_sync_id = {t.name: t.sync_id for t in tag_rows}

            # 验证 2:这笔 tx 的 projection tag_sync_ids_json 包含两个 sync_id
            tx = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_sync_id)
            )
            assert tx is not None
            assert tx.tags_csv and "AI记账" in tx.tags_csv and "文字记账" in tx.tags_csv
            assert tx.tag_sync_ids_json is not None, (
                "tag_sync_ids_json 应被填充,实际 NULL = 修复未生效"
            )
            sync_ids = json.loads(tx.tag_sync_ids_json)
            assert name_to_sync_id["AI记账"] in sync_ids
            assert name_to_sync_id["文字记账"] in sync_ids
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()


def test_batch_create_fills_tag_sync_ids_for_existing_tags():
    """Issue #5 根因修复:batch create 路径(B2/B3 LLM 记账)只接受 tags 名字,
    之前不反查 sync_id,导致 ReadTxProjection.tag_sync_ids_json 永远 NULL。

    现在 server 主动 lookup 已有 tag 的 sync_id,把它和名字一起写入 projection,
    后续 tag rename 走 sync_id 路径不再漏掉这笔 tx。
    """
    from sqlalchemy import select
    from src.models import ReadTxProjection

    client = _make_client()
    try:
        token, _uid = _register_and_login(client, "bat-tag-id@test.com", client_type="web")
        hdr = {"Authorization": f"Bearer {token}", "X-Device-ID": "d-web"}

        # 1. 创建 ledger
        r = client.post(
            "/api/v1/write/ledgers",
            json={"ledger_name": "default", "currency": "CNY"},
            headers=hdr,
        )
        assert r.status_code == 200, r.text
        ledger_id = r.json()["entity_id"]
        change_id = r.json()["new_change_id"]

        # 2. 先创建一个已存在的 tag「旅行」,记下 sync_id
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/tags",
            json={"base_change_id": change_id, "name": "旅行"},
            headers=hdr,
        )
        assert r.status_code == 200, r.text
        travel_tag_sync_id = r.json()["entity_id"]
        change_id = r.json()["new_change_id"]

        # 3. 走 batch 路径创建 tx,只传 tags 名字(模拟 B2/B3 LLM 记账场景)
        r = client.post(
            f"/api/v1/write/ledgers/{ledger_id}/transactions/batch",
            json={
                "base_change_id": change_id,
                "transactions": [{
                    "tx_type": "expense",
                    "amount": 1280.0,
                    "happened_at": "2026-05-21T12:00:00Z",
                    "note": "酒店",
                    "tags": ["旅行"],
                }],
                "auto_ai_tag": False,
                "locale": "zh",
            },
            headers=hdr,
        )
        assert r.status_code == 200, r.text
        tx_sync_id = r.json()["created_sync_ids"][0]

        # 4. 验证 projection 的 tag_sync_ids_json 已被填充(修复目标)
        db = next(app.dependency_overrides[get_db]())
        try:
            tx = db.scalar(
                select(ReadTxProjection).where(ReadTxProjection.sync_id == tx_sync_id)
            )
            assert tx is not None, "projection row missing"
            assert tx.tags_csv == "旅行", f"tags_csv 应保留名字, 实际={tx.tags_csv}"
            assert tx.tag_sync_ids_json is not None, (
                "tag_sync_ids_json 应被 batch 路径 lookup 填充, 当前 NULL "
                "= issue #5 根因未修"
            )
            sync_ids = json.loads(tx.tag_sync_ids_json)
            assert travel_tag_sync_id in sync_ids, (
                f"期望 {travel_tag_sync_id} ∈ {sync_ids}"
            )
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
