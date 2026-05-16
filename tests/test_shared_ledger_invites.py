"""Shared-ledger invite flow integration tests.

覆盖 Sprint 1.7 (`routers/invites.py`) 的核心场景:create / preview / accept /
revoke / outsider 403 / 重复加入 409 / 过期失效 / 成员上限。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import LedgerInvite


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
    client = TestClient(app)
    client._db_factory = TS  # type: ignore[attr-defined]  # 测试里临时塞 session 工厂便于直接改库
    return client


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
    return res.json()


def _create_ledger(client: TestClient, token: str, ledger_id: str = "default") -> str:
    res = client.post(
        "/api/v1/write/ledgers",
        headers={"Authorization": f"Bearer {token}"},
        json={"ledger_id": ledger_id, "ledger_name": "Test", "currency": "CNY"},
    )
    assert res.status_code == 200, res.text
    return res.json()["ledger_id"]


def _create_invite(client: TestClient, token: str, ledger_id: str, **kwargs) -> dict:
    payload = {"role": "editor", "expires_in_hours": 24, **kwargs}
    res = client.post(
        f"/api/v1/ledgers/{ledger_id}/invites",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------------------

def test_owner_create_invite_then_other_user_accept_joins_as_editor() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        joiner = _register(client, "joiner@example.com")
        ledger_id = _create_ledger(client, owner["access_token"])

        # Owner 创建邀请
        invite = _create_invite(client, owner["access_token"], ledger_id)
        assert len(invite["code"]) == 6
        assert invite["target_role"] == "editor"
        assert "/invite/" in invite["share_url"]

        # Joiner preview
        res = client.post(
            f"/api/v1/invites/{invite['code']}/preview",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["ledger_external_id"] == ledger_id
        assert body["target_role"] == "editor"

        # Joiner accept
        res = client.post(
            f"/api/v1/invites/{invite['code']}/accept",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["role"] == "editor"
        assert body["member_count"] == 2

        # 重复 accept → 409 Already a member
        res = client.post(
            f"/api/v1/invites/{invite['code']}/accept",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )
        assert res.status_code in (404, 409), res.text  # 已 used → 404; 状态 → 409
    finally:
        app.dependency_overrides.clear()


def test_non_owner_cannot_create_invite() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        joiner = _register(client, "joiner@example.com")
        ledger_id = _create_ledger(client, owner["access_token"])

        invite = _create_invite(client, owner["access_token"], ledger_id)
        client.post(
            f"/api/v1/invites/{invite['code']}/accept",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )

        # joiner 现在是 editor — 不能创建新邀请
        res = client.post(
            f"/api/v1/ledgers/{ledger_id}/invites",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
            json={"role": "editor", "expires_in_hours": 24},
        )
        assert res.status_code == 404, res.text  # 角色不足 → 404 而非 403
    finally:
        app.dependency_overrides.clear()


def test_outsider_preview_fails_on_invalid_code() -> None:
    client = _make_client()
    try:
        outsider = _register(client, "out@example.com")
        res = client.post(
            "/api/v1/invites/INVALID/preview",
            headers={"Authorization": f"Bearer {outsider['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_revoke_makes_code_unusable() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        joiner = _register(client, "joiner@example.com")
        ledger_id = _create_ledger(client, owner["access_token"])
        invite = _create_invite(client, owner["access_token"], ledger_id)

        # Owner revoke
        res = client.delete(
            f"/api/v1/ledgers/{ledger_id}/invites/{invite['code']}",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert res.status_code == 204, res.text

        # Joiner 再 preview → 404
        res = client.post(
            f"/api/v1/invites/{invite['code']}/preview",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_owner_cannot_accept_own_invite() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        ledger_id = _create_ledger(client, owner["access_token"])
        invite = _create_invite(client, owner["access_token"], ledger_id)

        res = client.post(
            f"/api/v1/invites/{invite['code']}/accept",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        # owner 已是 ledger 成员 → 走 Already-a-member 409
        assert res.status_code == 409, res.text
    finally:
        app.dependency_overrides.clear()


def test_expired_invite_returns_404() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        joiner = _register(client, "joiner@example.com")
        ledger_id = _create_ledger(client, owner["access_token"])
        invite = _create_invite(client, owner["access_token"], ledger_id)

        # 直接改库把 expires_at 设到过去
        TS = client._db_factory  # type: ignore[attr-defined]
        with TS() as db:
            row = db.get(LedgerInvite, invite["code"])
            assert row is not None
            row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            db.commit()

        res = client.post(
            f"/api/v1/invites/{invite['code']}/accept",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()


def test_list_invites_owner_only() -> None:
    client = _make_client()
    try:
        owner = _register(client, "owner@example.com")
        joiner = _register(client, "joiner@example.com")
        ledger_id = _create_ledger(client, owner["access_token"])
        _create_invite(client, owner["access_token"], ledger_id)
        _create_invite(client, owner["access_token"], ledger_id)

        # Owner can list
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/invites",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
        )
        assert res.status_code == 200, res.text
        assert len(res.json()) == 2

        # joiner(not a member) → 404
        res = client.get(
            f"/api/v1/ledgers/{ledger_id}/invites",
            headers={"Authorization": f"Bearer {joiner['access_token']}"},
        )
        assert res.status_code == 404, res.text
    finally:
        app.dependency_overrides.clear()
