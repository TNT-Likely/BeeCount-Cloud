"""共享账本 Phase 1 e2e 冒烟测试。

覆盖:
1. Owner 创建账本 + 邀请 + Editor 接受 → ledger_members 写入正确
2. Editor pull 看到 Owner 的 ledger
3. shared-resources endpoint 返回 Owner 的 user-global 资源
4. Editor 拒绝 PATCH /ledgers/{id}/meta(owner-only)
5. Owner 踢 Editor → Editor 失去访问
"""
from __future__ import annotations

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


def _register(client: TestClient, email: str, device: str) -> tuple[str, str]:
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device,
            "client_type": "web",
            "device_name": f"pytest-{device}",
            "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return data["access_token"], data["user"]["id"]


def _auth(token: str, device: str) -> dict:
    return {"Authorization": f"Bearer {token}", "X-Device-ID": device}


def test_invite_accept_flow():
    client = _make_client()
    owner_token, owner_id = _register(client, "owner@e2e.test", "dev-owner")
    editor_token, editor_id = _register(client, "editor@e2e.test", "dev-editor")

    r = client.post(
        "/api/v1/write/ledgers",
        json={"ledger_id": "shared1", "ledger_name": "Family", "currency": "CNY"},
        headers=_auth(owner_token, "dev-owner"),
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/v1/ledgers/shared1/invites",
        json={"role": "editor", "expires_in_hours": 24},
        headers=_auth(owner_token, "dev-owner"),
    )
    assert r.status_code == 201, r.text
    code = r.json()["code"]
    assert len(code) == 6

    r = client.post(
        f"/api/v1/invites/{code}/preview", headers=_auth(editor_token, "dev-editor")
    )
    assert r.status_code == 200, r.text
    assert r.json()["ledger_external_id"] == "shared1"
    assert r.json()["target_role"] == "editor"

    r = client.post(
        f"/api/v1/invites/{code}/accept", headers=_auth(editor_token, "dev-editor")
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "editor"
    assert r.json()["member_count"] == 2

    r = client.get("/api/v1/sync/ledgers", headers=_auth(editor_token, "dev-editor"))
    assert r.status_code == 200, r.text
    ledger_ids = [lg["ledger_id"] for lg in r.json()]
    assert "shared1" in ledger_ids


def test_shared_resources_endpoint():
    """shared-resources endpoint:Editor 调 → 返 Owner 的 user-global 资源列表。

    本测试不通过 sync/push 灌 user-global category(scope 鉴权 fixture 复杂,留
    给主测试套),直接 DB 写入 UserCategoryProjection 后调 endpoint 验证。
    """
    from src.models import UserCategoryProjection

    client = _make_client()
    owner_token, owner_id = _register(client, "owner2@e2e.test", "d1")
    editor_token, _ = _register(client, "editor2@e2e.test", "d2")

    client.post(
        "/api/v1/write/ledgers",
        json={"ledger_id": "shared2", "ledger_name": "L", "currency": "CNY"},
        headers=_auth(owner_token, "d1"),
    )

    # 直接灌 user_category_projection(模拟 Owner 之前已通过 mobile push 同步过)
    from src.database import get_db as _get_db
    db_gen = app.dependency_overrides[_get_db]()
    db = next(db_gen)
    try:
        db.add(UserCategoryProjection(
            user_id=owner_id,
            sync_id="cat-1",
            name="早餐",
            kind="expense",
            icon_type="material",
            sort_order=0,
        ))
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/api/v1/ledgers/shared2/invites",
        json={"role": "editor", "expires_in_hours": 24},
        headers=_auth(owner_token, "d1"),
    )
    code = r.json()["code"]
    client.post(f"/api/v1/invites/{code}/accept", headers=_auth(editor_token, "d2"))

    r = client.get(
        "/api/v1/ledgers/shared2/shared-resources",
        headers=_auth(editor_token, "d2"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["owner_user_id"] == owner_id
    assert len(data["categories"]) == 1
    assert data["categories"][0]["name"] == "早餐"
    assert data["categories"][0]["kind"] == "expense"


def test_editor_cannot_patch_ledger_meta():
    client = _make_client()
    owner_token, _ = _register(client, "owner3@e2e.test", "d1")
    editor_token, _ = _register(client, "editor3@e2e.test", "d2")

    client.post(
        "/api/v1/write/ledgers",
        json={"ledger_id": "shared3", "ledger_name": "L", "currency": "CNY"},
        headers=_auth(owner_token, "d1"),
    )
    r = client.post(
        "/api/v1/ledgers/shared3/invites",
        json={"role": "editor", "expires_in_hours": 24},
        headers=_auth(owner_token, "d1"),
    )
    code = r.json()["code"]
    client.post(f"/api/v1/invites/{code}/accept", headers=_auth(editor_token, "d2"))

    r = client.patch(
        "/api/v1/write/ledgers/shared3/meta",
        json={"base_change_id": 0, "ledger_name": "ed"},
        headers=_auth(editor_token, "d2"),
    )
    assert r.status_code == 404, r.text


def test_owner_remove_editor():
    client = _make_client()
    owner_token, _ = _register(client, "owner4@e2e.test", "d1")
    editor_token, editor_id = _register(client, "editor4@e2e.test", "d2")

    client.post(
        "/api/v1/write/ledgers",
        json={"ledger_id": "shared4", "ledger_name": "L", "currency": "CNY"},
        headers=_auth(owner_token, "d1"),
    )
    r = client.post(
        "/api/v1/ledgers/shared4/invites",
        json={"role": "editor", "expires_in_hours": 24},
        headers=_auth(owner_token, "d1"),
    )
    code = r.json()["code"]
    client.post(f"/api/v1/invites/{code}/accept", headers=_auth(editor_token, "d2"))

    r = client.delete(
        f"/api/v1/ledgers/shared4/members/{editor_id}",
        headers=_auth(owner_token, "d1"),
    )
    assert r.status_code == 204, r.text

    r = client.get("/api/v1/sync/ledgers", headers=_auth(editor_token, "d2"))
    assert r.status_code == 200
    assert all(lg["ledger_id"] != "shared4" for lg in r.json())
