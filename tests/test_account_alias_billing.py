"""0027 账户别名 + 信用卡「出账日计入当期」Cloud 端契约。

- web 写端点带 alias / billing_day_in_current → 落 user_account_projection
  新列 + 读端点 round-trip
- 缺省 billing_day_in_current = True(主流口径:账单日当天入本期)
- mobile /sync/push(camelCase alias / billingDayInCurrent)落库 +
  partial-update merge 缺键保留(CLAUDE.md L74-80 新增字段 merge 契约)
- PATCH 显式清空(alias=null)生效;billing_day_in_current=false 不会被
  默认值吞掉(False ≠ None,update 走 exclude_unset)
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import UserAccountProjection


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


def _account_row(TS, sync_id) -> UserAccountProjection:
    with TS() as db:
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None, f"no account projection for {sync_id}"
        db.expunge(row)
        return row


def _bootstrap(client, email, ledger_id):
    """app 端建账本 + web 端登录,返回两个 header。"""
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger_id, "ledger", ledger_id,
          {"syncId": ledger_id, "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
    return hdr_app, hdr_web


def test_web_create_account_with_alias_and_billing_day_in_current():
    """POST 带 alias / billing_day_in_current=false → 落库 + 读端点 round-trip。"""
    client, TS = _make_client()
    try:
        _, hdr_web = _bootstrap(client, "alias1@t.com", "lga1")

        r = client.post(
            "/api/v1/write/ledgers/lga1/accounts",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "招商银行信用卡(5678)",
                "account_type": "credit_card",
                "alias": "招行卡",
                "billing_day": 5,
                "payment_due_day": 25,
                "billing_day_in_current": False,
            },
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]

        row = _account_row(TS, account_id)
        assert row.alias == "招行卡"
        assert row.billing_day_in_current is False

        r2 = client.get("/api/v1/read/ledgers/lga1/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        acc = next(x for x in r2.json() if x["id"] == account_id)
        # 别名不改变账户名,读端两个字段都原样返回
        assert acc["name"] == "招商银行信用卡(5678)"
        assert acc["alias"] == "招行卡"
        assert acc["billing_day_in_current"] is False
    finally:
        app.dependency_overrides.clear()


def test_alias_defaults_none_and_billing_in_current_defaults_true():
    """不传这两个字段:alias=None(显示账户名)、billing_day_in_current=True。"""
    client, TS = _make_client()
    try:
        _, hdr_web = _bootstrap(client, "alias2@t.com", "lga2")

        r = client.post(
            "/api/v1/write/ledgers/lga2/accounts",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "现金", "account_type": "cash"},
        )
        assert r.status_code == 200, r.text
        row = _account_row(TS, r.json()["entity_id"])
        assert row.alias is None
        assert row.billing_day_in_current is True

        r2 = client.get("/api/v1/read/ledgers/lga2/accounts", headers=hdr_web)
        acc = next(x for x in r2.json() if x["id"] == r.json()["entity_id"])
        assert acc["alias"] is None
        assert acc["billing_day_in_current"] is True
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_alias_billing_partial_update_keeps_fields():
    """mobile push 带 alias / billingDayInCurrent(camelCase)→ 落库;后续
    partial update 不带这些键 → merge 保留原值(新增字段 merge 契约)。"""
    client, TS = _make_client()
    try:
        hdr_app, _ = _bootstrap(client, "alias3@t.com", "lga3")

        _push(client, hdr_app, "lga3", "account", "acc-a3",
              {
                  "syncId": "acc-a3",
                  "name": "招行信用卡",
                  "type": "credit_card",
                  "currency": "CNY",
                  "alias": "招行卡",
                  "billingDay": 5,
                  "paymentDueDay": 25,
                  "billingDayInCurrent": False,
              },
              device_id="d-app")

        row = _account_row(TS, "acc-a3")
        assert row.alias == "招行卡"
        assert row.billing_day_in_current is False

        # partial update 只改 name,不带 alias / billingDayInCurrent → 保留
        _push(client, hdr_app, "lga3", "account", "acc-a3",
              {"syncId": "acc-a3", "name": "招行信用卡(新)"}, device_id="d-app")
        row2 = _account_row(TS, "acc-a3")
        assert row2.name == "招行信用卡(新)"
        assert row2.alias == "招行卡"
        assert row2.billing_day_in_current is False
    finally:
        app.dependency_overrides.clear()


def test_patch_alias_null_clears_and_false_is_not_swallowed():
    """PATCH:alias=null 显式清空;billing_day_in_current=false 显式改(不能
    被 update schema 的 None 默认值吞成 True)。"""
    client, TS = _make_client()
    try:
        _, hdr_web = _bootstrap(client, "alias4@t.com", "lga4")

        r = client.post(
            "/api/v1/write/ledgers/lga4/accounts",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "中信信用卡",
                "account_type": "credit_card",
                "alias": "中信卡",
                "billing_day": 10,
                "payment_due_day": 28,
            },
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]
        assert _account_row(TS, account_id).billing_day_in_current is True

        # 改成「计入下期」
        r2 = client.patch(
            f"/api/v1/write/ledgers/lga4/accounts/{account_id}",
            headers=hdr_web,
            json={"base_change_id": 0, "billing_day_in_current": False},
        )
        assert r2.status_code == 200, r2.text
        row = _account_row(TS, account_id)
        assert row.billing_day_in_current is False
        # 不带 alias key → 别名保留
        assert row.alias == "中信卡"

        # 显式清空别名
        r3 = client.patch(
            f"/api/v1/write/ledgers/lga4/accounts/{account_id}",
            headers=hdr_web,
            json={"base_change_id": 0, "alias": None},
        )
        assert r3.status_code == 200, r3.text
        row2 = _account_row(TS, account_id)
        assert row2.alias is None
        # 别名清空不影响账单口径
        assert row2.billing_day_in_current is False
        assert row2.name == "中信信用卡"
    finally:
        app.dependency_overrides.clear()
