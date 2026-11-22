"""关联人资料(头像 / 多联系方式 / 多银行卡)回归测试。

覆盖：normalize/encode/decode 清洗、旧单值列的双向兼容、成员 snapshot
改名时资料不丢、联系人 REST 创建/清空 profile。
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.party_profile import (
    decode_profile,
    encode_profile,
    legacy_values,
    normalize_profile,
    profile_with_legacy,
)
from src.snapshot_mutator import create_member, update_member

PROFILE = {
    "avatarFileId": "file-1",
    "contactMethods": [
        {"type": "phone", "value": "13800000001", "label": "工作"},
        {"type": "wechat", "value": "wx_1", "label": ""},
        {"type": "unknown", "value": "abc", "label": "兜底"},
        {"type": "alipay", "value": "  ", "label": ""},
    ],
    "bankCards": [
        {"bank": "招商银行", "cardNo": "6225888812345678", "holder": "张三", "note": "工资卡"},
        {"bank": "", "cardNo": "", "holder": "", "note": ""},
    ],
}


def test_normalize_profile_cleans_and_falls_back():
    p = normalize_profile(PROFILE)
    assert p is not None
    assert p["avatarFileId"] == "file-1"
    # 空值条目被丢弃，未知类型兜底成 other。
    assert len(p["contactMethods"]) == 3
    assert p["contactMethods"][2]["type"] == "other"
    assert len(p["bankCards"]) == 1
    assert p["bankCards"][0]["bank"] == "招商银行"


def test_normalize_profile_empty_and_dirty():
    assert normalize_profile(None) is None
    assert normalize_profile({}) is None
    assert normalize_profile("not json") is None
    assert normalize_profile({"contactMethods": None}) is None
    assert normalize_profile({"contactMethods": [{"type": "phone", "value": ""}]}) is None


def test_encode_decode_roundtrip():
    raw = encode_profile(PROFILE)
    assert raw is not None and '"avatarFileId":"file-1"' in raw
    assert decode_profile(raw) == normalize_profile(PROFILE)
    assert decode_profile("{broken") is None


def test_legacy_bridge():
    legacy = legacy_values(normalize_profile(PROFILE))
    assert legacy["phone"] == "13800000001"
    assert legacy["wechat"] == "wx_1"
    assert legacy["alipay"] is None
    assert legacy["avatar_file_id"] == "file-1"
    synth = profile_with_legacy(
        None, phone="111", wechat="wx", alipay="ali", avatar_file_id=None
    )
    assert [m["type"] for m in synth["contactMethods"]] == ["phone", "wechat", "alipay"]
    assert profile_with_legacy(None) is None


def test_member_snapshot_keeps_profile_on_rename():
    target, mid = create_member({}, {"name": "张三", "color": "#fff", "profile": PROFILE})
    item = next(m for m in target["members"] if m["syncId"] == mid)
    assert item["profile"]["avatarFileId"] == "file-1"

    # 不带 profile 的改名（老客户端 / 只改名）不能清空资料。
    target2 = update_member(target, mid, {"name": "张三2"})
    item2 = next(m for m in target2["members"] if m["syncId"] == mid)
    assert len(item2["profile"]["contactMethods"]) == 3

    # 显式传空对象才清空。
    target3 = update_member(target2, mid, {"profile": {}})
    item3 = next(m for m in target3["members"] if m["syncId"] == mid)
    assert item3["profile"] is None


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def test_contact_profile_rest_roundtrip():
    client = _make_client()
    try:
        res = client.post(
            "/api/v1/auth/register",
            json={
                "email": "party@example.com",
                "password": "123456",
                "client_type": "app",
                "device_name": "pytest",
                "platform": "app",
            },
        )
        assert res.status_code == 200, res.text
        headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

        created = client.post(
            "/api/v1/contacts",
            headers=headers,
            json={"name": "资料联系人", "profile": PROFILE},
        )
        assert created.status_code == 201, created.text
        row = created.json()
        assert row["profile"]["avatarFileId"] == "file-1"
        assert len(row["profile"]["bankCards"]) == 1
        # 旧单值列同步（老客户端仍可读取首选联系方式）。
        assert row["phone"] == "13800000001"
        assert row["wechat"] == "wx_1"
        assert row["avatar_file_id"] == "file-1"

        # 老数据：只传单值字段时，读视图会合成 profile。
        legacy = client.post(
            "/api/v1/contacts", headers=headers, json={"name": "旧联系人", "phone": "999"}
        )
        assert legacy.status_code == 201, legacy.text
        assert legacy.json()["profile"]["contactMethods"][0]["value"] == "999"

        # 显式 null 清空 profile 与所有旧单值列。
        cleared = client.patch(
            f"/api/v1/contacts/{row['id']}", headers=headers, json={"profile": None}
        )
        assert cleared.status_code == 200, cleared.text
        body = cleared.json()
        assert body["profile"] is None
        assert body["phone"] is None
        assert body["avatar_file_id"] is None
    finally:
        app.dependency_overrides.clear()
