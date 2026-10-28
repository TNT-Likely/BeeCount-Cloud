"""成员(0024)图标 + 关联记账人 Cloud 端契约。

- web 写端点带 icon / linked_user_id → 落 read_member_projection 新列 +
  读端点 round-trip(camelCase: icon / linkedUserId)
- PATCH 显式清空(icon=null / linked_user_id=null)生效,不留旧值
- member-share-stats 口径:**没有分摊**的交易原按 created_by 归记账人;
  若该记账人已被某个成员 linked_user_id 关联,则并入该成员(kind='member'),
  不再单独出一条 kind='user' —— 避免同一个人被算两遍
- 未关联用户的记账人维持原行为(kind='user')
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import ReadMemberProjection, User


def _make_client():
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
    return TestClient(app), TS


def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat()


def _login(client, email, *, device_id="d1", client_type="app"):
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Pa$$word1!"},
    )
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "Pa$$word1!",
            "device_id": device_id,
            "client_type": client_type,
            "device_name": "pytest",
            "platform": "test",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d1", action="upsert"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": action,
        "updated_at": _iso(),
        "payload": payload,
    }
    r = client.post(
        "/api/v1/sync/push",
        headers=hdr,
        json={"device_id": device_id, "changes": [body]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _user_id(TS, email) -> str:
    with TS() as db:
        row = db.scalar(select(User).where(User.email == email))
        assert row is not None, f"no user {email}"
        return row.id


def _member_row(TS, sync_id) -> ReadMemberProjection:
    with TS() as db:
        row = db.scalar(
            select(ReadMemberProjection).where(
                ReadMemberProjection.sync_id == sync_id,
            )
        )
        assert row is not None, f"no member projection for {sync_id}"
        db.expunge(row)
        return row


def test_web_create_member_with_icon_and_linked_user():
    """POST /write/ledgers/{id}/members 带 icon / linked_user_id → 落库 + 读回。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "mb1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "mb1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        uid = _user_id(TS, "mb1@t.com")

        _push(client, hdr_app, "lgm1", "ledger", "lgm1",
              {"syncId": "lgm1", "ledgerName": "家", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgm1/members",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "爸爸",
                "icon": "man",
                "linked_user_id": uid,
            },
        )
        assert r.status_code == 200, r.text
        member_id = r.json()["entity_id"]
        assert member_id

        row = _member_row(TS, member_id)
        assert row.icon == "man"
        assert row.linked_user_id == uid

        r2 = client.get("/api/v1/read/ledgers/lgm1/members", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        got = next(x for x in r2.json() if x["id"] == member_id)
        assert got["icon"] == "man"
        assert got["linked_user_id"] == uid
    finally:
        app.dependency_overrides.clear()


def test_web_update_member_clears_icon_and_link():
    """PATCH 显式 icon=null / linked_user_id=null → 两列清空(不留旧值)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "mb2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "mb2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        uid = _user_id(TS, "mb2@t.com")

        _push(client, hdr_app, "lgm2", "ledger", "lgm2",
              {"syncId": "lgm2", "ledgerName": "家", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgm2/members",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "妈妈",
                "icon": "woman",
                "linked_user_id": uid,
            },
        )
        assert r.status_code == 200, r.text
        member_id = r.json()["entity_id"]
        assert _member_row(TS, member_id).icon == "woman"

        r2 = client.patch(
            f"/api/v1/write/ledgers/lgm2/members/{member_id}",
            headers=hdr_web,
            json={"base_change_id": 0, "icon": None, "linked_user_id": None},
        )
        assert r2.status_code == 200, r2.text
        row = _member_row(TS, member_id)
        assert row.icon is None, "icon 未清空"
        assert row.linked_user_id is None, "linked_user_id 未清空"
        assert row.name == "妈妈", "只改图标不该动到名字"
    finally:
        app.dependency_overrides.clear()


def test_member_share_stats_merges_linked_bookkeeper():
    """没有分摊的交易:记账人已被成员关联 → 并入该成员(kind='member'),
    不再单独出一条 kind='user'。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "mb3@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "mb3@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        uid = _user_id(TS, "mb3@t.com")

        _push(client, hdr_app, "lgm3", "ledger", "lgm3",
              {"syncId": "lgm3", "ledgerName": "家", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgm3/members",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "爸爸",
                "icon": "man",
                "linked_user_id": uid,
            },
        )
        assert r.status_code == 200, r.text
        member_id = r.json()["entity_id"]

        acc = client.post(
            "/api/v1/write/ledgers/lgm3/accounts",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "现金", "account_type": "cash"},
        )
        assert acc.status_code == 200, acc.text
        account_id = acc.json()["entity_id"]

        tx = client.post(
            "/api/v1/write/ledgers/lgm3/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "tx_type": "expense",
                "amount": 120.0,
                "happened_at": _iso(),
                "account_id": account_id,
                "account_name": "现金",
            },
        )
        assert tx.status_code == 200, tx.text

        stats = client.get(
            "/api/v1/ledgers/lgm3/member-share-stats?scope=all", headers=hdr_web
        )
        assert stats.status_code == 200, stats.text
        items = stats.json()["items"]
        member_rows = [x for x in items if x["kind"] == "member"]
        user_rows = [x for x in items if x["kind"] == "user"]
        assert len(member_rows) == 1, items
        assert member_rows[0]["id"] == member_id
        assert member_rows[0]["name"] == "爸爸"
        assert abs(member_rows[0]["expense_total"] - 120.0) < 0.01, member_rows[0]
        assert user_rows == [], f"记账人不该再单独出一条: {user_rows}"
    finally:
        app.dependency_overrides.clear()


def test_member_share_stats_keeps_unlinked_bookkeeper():
    """回归:成员未关联记账人时,无分摊交易仍按记账人归属(kind='user')。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "mb4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "mb4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
        uid = _user_id(TS, "mb4@t.com")

        _push(client, hdr_app, "lgm4", "ledger", "lgm4",
              {"syncId": "lgm4", "ledgerName": "家", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgm4/members",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "妈妈", "icon": "woman"},
        )
        assert r.status_code == 200, r.text

        acc = client.post(
            "/api/v1/write/ledgers/lgm4/accounts",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "现金", "account_type": "cash"},
        )
        assert acc.status_code == 200, acc.text
        account_id = acc.json()["entity_id"]

        tx = client.post(
            "/api/v1/write/ledgers/lgm4/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "tx_type": "expense",
                "amount": 30.0,
                "happened_at": _iso(),
                "account_id": account_id,
                "account_name": "现金",
            },
        )
        assert tx.status_code == 200, tx.text

        stats = client.get(
            "/api/v1/ledgers/lgm4/member-share-stats?scope=all", headers=hdr_web
        )
        assert stats.status_code == 200, stats.text
        items = stats.json()["items"]
        user_rows = [x for x in items if x["kind"] == "user"]
        assert len(user_rows) == 1, items
        assert user_rows[0]["id"] == uid
        assert abs(user_rows[0]["expense_total"] - 30.0) < 0.01, user_rows[0]
    finally:
        app.dependency_overrides.clear()
