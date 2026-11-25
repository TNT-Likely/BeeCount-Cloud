"""账户不计入总资产(BeeCount #651)Cloud 端契约:push/merge/snapshot/Web 写入/净值历史。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import SyncChange, User, UserAccountProjection


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


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# Task 2: merge / upsert / snapshot                                           #
# --------------------------------------------------------------------------- #



def test_mobile_push_account_partial_update_keeps_exclude_from_total():
    """merge 契约:partial update 不带 excludeFromTotal 键时保留原值;显式 False 覆盖。"""
    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'ex1@t.com')}"}
        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "name": "个税", "type": "cash", "currency": "CNY",
               "excludeFromTotal": True})
        assert _account_row(TS, "ex1@t.com", "acc-1").exclude_from_total is True

        _push(client, hdr, "lg1", "account", "acc-1", {"syncId": "acc-1", "name": "个税改名"})
        row = _account_row(TS, "ex1@t.com", "acc-1")
        assert row.name == "个税改名"
        assert row.exclude_from_total is True

        _push(client, hdr, "lg1", "account", "acc-1",
              {"syncId": "acc-1", "excludeFromTotal": False})
        assert _account_row(TS, "ex1@t.com", "acc-1").exclude_from_total is False

        _push(client, hdr, "lg1", "account", "acc-2",
              {"syncId": "acc-2", "name": "现金", "type": "cash", "currency": "CNY"})
        assert _account_row(TS, "ex1@t.com", "acc-2").exclude_from_total is False
    finally:
        app.dependency_overrides.clear()


def test_snapshot_builder_outputs_exclude_from_total():
    from src.models import Ledger
    from src.snapshot_builder import build

    client, TS = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_login(client, 'ex2@t.com')}"}
        _push(client, hdr, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"})
        _push(client, hdr, "lg1", "account", "acc-ex",
              {"syncId": "acc-ex", "name": "个税", "type": "cash", "currency": "CNY",
               "excludeFromTotal": True})
        _push(client, hdr, "lg1", "account", "acc-in",
              {"syncId": "acc-in", "name": "现金", "type": "cash", "currency": "CNY"})

        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            snap = build(db, ledger)
        by_id = {acc["syncId"]: acc for acc in snap["accounts"]}
        assert by_id["acc-ex"]["excludeFromTotal"] is True
        assert by_id["acc-in"]["excludeFromTotal"] is False
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_exclude_from_total_and_read_back():
    """Web PATCH 写 exclude_from_total → 落投影 + 反向 SyncChange + 读端点可见;
    不带该键的 PATCH 保留原值。"""
    client, TS = _make_client()
    try:
        hdr_app = {"Authorization": f"Bearer {_login(client, 'ex3@t.com', device_id='d-app')}"}
        hdr_web = {
            "Authorization": f"Bearer {_login(client, 'ex3@t.com', device_id='d-web', client_type='web')}",
            "X-Device-ID": "d-web",
        }
        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-w",
              {"syncId": "acc-w", "name": "个税", "type": "cash", "currency": "CNY"},
              device_id="d-app")

        r = client.patch("/api/v1/write/ledgers/lg1/accounts/acc-w", headers=hdr_web,
                         json={"base_change_id": 0, "exclude_from_total": True})
        assert r.status_code == 200, r.text
        assert _account_row(TS, "ex3@t.com", "acc-w").exclude_from_total is True

        with TS() as db:
            change = db.scalar(
                select(SyncChange)
                .where(SyncChange.entity_type == "account", SyncChange.entity_sync_id == "acc-w")
                .order_by(SyncChange.change_id.desc())
            )
        assert change.payload_json.get("excludeFromTotal") is True

        r = client.patch("/api/v1/write/ledgers/lg1/accounts/acc-w", headers=hdr_web,
                         json={"base_change_id": 0, "note": "不可提现"})
        assert r.status_code == 200, r.text
        assert _account_row(TS, "ex3@t.com", "acc-w").exclude_from_total is True

        for url in ("/api/v1/read/ledgers/lg1/accounts", "/api/v1/read/workspace/accounts"):
            r = client.get(url, headers=hdr_web)
            assert r.status_code == 200, r.text
            acc = next(x for x in r.json() if x["id"] == "acc-w")
            assert acc["exclude_from_total"] is True
    finally:
        app.dependency_overrides.clear()


def test_net_worth_history_skips_excluded_account():
    """净值历史跳过不计入账户:现金 1000 - 转入个税 300 = 700(个税账户的 300 不计入)。"""
    client, TS = _make_client()
    try:
        hdr_app = {"Authorization": f"Bearer {_login(client, 'ex4@t.com', device_id='d-app')}"}
        hdr_web = {"Authorization": f"Bearer {_login(client, 'ex4@t.com', device_id='d-web', client_type='web')}"}
        _push(client, hdr_app, "lg1", "ledger", "lg1",
              {"syncId": "lg1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-cash",
              {"syncId": "acc-cash", "name": "现金", "type": "cash",
               "initialBalance": 1000.0, "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lg1", "account", "acc-tax",
              {"syncId": "acc-tax", "name": "个税", "type": "cash",
               "initialBalance": 5000.0, "currency": "CNY", "excludeFromTotal": True},
              device_id="d-app")
        _push(client, hdr_app, "lg1", "transaction", "tx-1",
              {"syncId": "tx-1", "type": "transfer", "amount": 300,
               "fromAccountId": "acc-cash", "toAccountId": "acc-tax",
               "happenedAt": "2026-01-15T00:00:00+00:00"}, device_id="d-app")

        r = client.get("/api/v1/read/workspace/net-worth-history", headers=hdr_web,
                       params={"scope": "all"})
        assert r.status_code == 200, r.text
        series = {s["bucket"]: s["net_worth"] for s in r.json()["series"]}
        assert series["2026-01"] == 700.0
    finally:
        app.dependency_overrides.clear()
