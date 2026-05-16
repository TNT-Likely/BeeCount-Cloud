"""Shared-ledger members + transfer integration tests.

覆盖 Sprint 1.8 (`routers/members.py`):list / remove(踢人 + 退出)/
transfer ownership / 边界(踢 owner、转给非成员、非 owner 操作)。
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
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest",
            "platform": "test",
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # AuthTokenResponse 把 user 嵌套在 user 字段里;平铺出 user_id 给 test 用。
    body["user_id"] = body["user"]["id"]
    return body


def _setup_owner_with_editor(client: TestClient) -> tuple[dict, dict, str]:
    """Helper: register owner + editor, create ledger, invite + accept.
    Returns (owner_session, editor_session, ledger_external_id).
    """
    owner = _register(client, "owner@example.com")
    editor = _register(client, "editor@example.com")
    res = client.post(
        "/api/v1/write/ledgers",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
        json={"ledger_id": "shared", "ledger_name": "Shared", "currency": "CNY"},
    )
    assert res.status_code == 200, res.text
    ledger_id = res.json()["ledger_id"]

    res = client.post(
        f"/api/v1/ledgers/{ledger_id}/invites",
        headers={"Authorization": f"Bearer {owner['access_token']}"},
        json={"role": "editor", "expires_in_hours": 24},
    )
    assert res.status_code == 201, res.text
    code = res.json()["code"]

    res = client.post(
        f"/api/v1/invites/{code}/accept",
        headers={"Authorization": f"Bearer {editor['access_token']}"},
    )
    assert res.status_code == 200, res.text

    return owner, editor, ledger_id


def test_list_members_returns_owner_and_editor() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_owner_with_editor(client)
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/members",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert res.status_code == 200, res.text
        members = res.json()
        assert len(members) == 2
        roles = {m["user_id"]: m["role"] for m in members}
        assert roles[owner["user_id"]] == "owner"
        assert roles[editor["user_id"]] == "editor"

        # Editor 也能列(任何 member 可读)
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/members",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 200, res.text
        assert len(res.json()) == 2
    finally:
        app.dependency_overrides.clear()


def test_outsider_cannot_list_members() -> None:
    client = _make_client()
    try:
        owner, _editor, ledger_id = _setup_owner_with_editor(client)
        outsider = _register(client, "out@example.com")
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/members",
            headers={"Authorization": f"Bearer {outsider['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_owner_can_remove_editor() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_owner_with_editor(client)
        res = client.delete(
            f"/api/v1/ledgers/{ledger_id}/members/{editor['user_id']}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert res.status_code == 204, res.text

        # Editor 现在不能再读
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/members",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_editor_can_remove_self() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_owner_with_editor(client)
        res = client.delete(
            f"/api/v1/ledgers/{ledger_id}/members/{editor['user_id']}",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        assert res.status_code == 204, res.text

        # 退出后 Owner 看到只有自己
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/members",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert len(res.json()) == 1
    finally:
        app.dependency_overrides.clear()


def test_editor_cannot_remove_owner() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_owner_with_editor(client)
        # Editor 试图踢 Owner → owner 是 owner 角色,会进 409
        res = client.delete(
            f"/api/v1/ledgers/{ledger_id}/members/{owner['user_id']}",
            headers={"Authorization": f"Bearer {editor['access_token']}"},
        )
        # Editor 不是自己,且不是 owner,先 404 拦下(member-not-found 语义混合)
        # 这里要求至少不是 204(成功删除)
        assert res.status_code in (403, 404, 409), res.text
        # Owner 还在
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/members",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert len(res.json()) == 2
    finally:
        app.dependency_overrides.clear()


def test_owner_cannot_remove_self_without_transfer() -> None:
    client = _make_client()
    try:
        owner, _editor, ledger_id = _setup_owner_with_editor(client)
        res = client.delete(
            f"/api/v1/ledgers/{ledger_id}/members/{owner['user_id']}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        # Owner 不能踢自己,必须先 transfer。返 409。
        assert res.status_code == 409, res.text
    finally:
        app.dependency_overrides.clear()


def test_transfer_ownership_swaps_roles() -> None:
    client = _make_client()
    try:
        owner, editor, ledger_id = _setup_owner_with_editor(client)
        res = client.post(
            f"/api/v1/ledgers/{ledger_id}/transfer",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={"new_owner_user_id": editor["user_id"]},
        )
        assert res.status_code == 200, res.text
        members = res.json()
        roles = {m["user_id"]: m["role"] for m in members}
        assert roles[editor["user_id"]] == "owner"
        assert roles[owner["user_id"]] == "editor"

        # 原 owner 现在不能再 transfer
        res = client.post(
            f"/api/v1/ledgers/{ledger_id}/transfer",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={"new_owner_user_id": editor["user_id"]},
        )
        assert res.status_code == 404, res.text  # 角色不够 → 404
    finally:
        app.dependency_overrides.clear()


def test_transfer_to_non_member_fails() -> None:
    client = _make_client()
    try:
        owner, _editor, ledger_id = _setup_owner_with_editor(client)
        outsider = _register(client, "out@example.com")
        res = client.post(
            f"/api/v1/ledgers/{ledger_id}/transfer",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={"new_owner_user_id": outsider["user_id"]},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()
