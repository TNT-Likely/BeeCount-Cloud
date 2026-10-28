"""0026 账户卡号 + 银行图标 Cloud 端契约。

- web 写端点带 card_number / icon → 落 user_account_projection 新列 +
  读端点 round-trip
- card_last_four 缺省时由 card_number 后四位派生(projection upsert 内)
- mobile /sync/push(camelCase cardNumber / icon)落库 + partial-update merge
  缺键保留(CLAUDE.md L74-80 新增字段 merge 契约)
- PATCH 显式清空(icon=null)生效
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


def _account_row(TS, email, sync_id) -> UserAccountProjection:
    with TS() as db:
        row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.sync_id == sync_id,
            )
        )
        assert row is not None, f"no account projection for {sync_id}"
        db.expunge(row)
        return row


def test_web_create_account_with_card_number_and_icon():
    """POST /write/ledgers/{id}/accounts 带 card_number / icon → 落库 + 读回;
    card_last_four 未显式传时由卡号后四位派生。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "card1@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "card1@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgc1", "ledger", "lgc1",
              {"syncId": "lgc1", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgc1/accounts",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "招行信用卡",
                "account_type": "credit_card",
                "bank_name": "招商银行",
                "card_number": "6225880212345678",
                "icon": "cmb",
            },
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]
        assert account_id

        row = _account_row(TS, "card1@t.com", account_id)
        assert row.card_number == "6225880212345678"
        assert row.icon == "cmb"
        # 后四位派生:没传 cardLastFour → 服务端从卡号取
        assert row.card_last_four == "5678"

        r2 = client.get("/api/v1/read/ledgers/lgc1/accounts", headers=hdr_web)
        assert r2.status_code == 200, r2.text
        acc = next(x for x in r2.json() if x["id"] == account_id)
        assert acc["card_number"] == "6225880212345678"
        assert acc["icon"] == "cmb"
        assert acc["card_last_four"] == "5678"
    finally:
        app.dependency_overrides.clear()


def test_explicit_card_last_four_wins_over_derived():
    """显式传 cardLastFour 时以用户为准(兼容 mobile 只传末四位的旧行为)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "card2@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "card2@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgc2", "ledger", "lgc2",
              {"syncId": "lgc2", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgc2/accounts",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "旧卡",
                "account_type": "bank_card",
                "card_number": "6225880212345678",
                "card_last_four": "9999",
            },
        )
        assert r.status_code == 200, r.text
        account_id = r.json()["entity_id"]
        row = _account_row(TS, "card2@t.com", account_id)
        assert row.card_last_four == "9999"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_account_card_number_icon_partial_update_keeps_fields():
    """mobile push 带 cardNumber / icon(camelCase)→ 落库;后续 partial update
    不带这些键 → merge 保留原值(新增字段 merge 契约)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "card3@t.com", device_id="d-app", client_type="app")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}

        _push(client, hdr_app, "lgc3", "ledger", "lgc3",
              {"syncId": "lgc3", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgc3", "account", "acc-c3",
              {
                  "syncId": "acc-c3",
                  "name": "工资卡",
                  "type": "bank_card",
                  "currency": "CNY",
                  "bankName": "工商银行",
                  "cardNumber": "6222021234567890",
                  "icon": "icbc",
              },
              device_id="d-app")

        row = _account_row(TS, "card3@t.com", "acc-c3")
        assert row.card_number == "6222021234567890"
        assert row.icon == "icbc"
        assert row.card_last_four == "7890"

        # partial update 只改 name,不带 cardNumber/icon → 保留
        _push(client, hdr_app, "lgc3", "account", "acc-c3",
              {"syncId": "acc-c3", "name": "工资卡( renamed)"},
              device_id="d-app")
        row2 = _account_row(TS, "card3@t.com", "acc-c3")
        assert row2.name == "工资卡( renamed)"
        assert row2.card_number == "6222021234567890"
        assert row2.icon == "icbc"
    finally:
        app.dependency_overrides.clear()


def test_subsequent_write_does_not_wipe_card_number_and_icon():
    """回归:同一账本内再建一个账户(或再写一笔交易)后,已建账户的
    card_number / icon 不被抹掉。

    根因(2026-10-25 部署后发现):snapshot_builder.build 的账户序列化漏选
    card_number/icon 两列,且 currency 按"有值才带 key"输出;而
    ensure_snapshot_v2 又无条件补 currency=None → 每次写入口所有账户 dict
    都判定为"变更"被全量重发,重发的 payload 缺 cardNumber/icon,投影 upsert
    直接把两列抹成 NULL。
    """
    client, TS = _make_client()
    try:
        app_tok = _login(client, "card5@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "card5@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgc5", "ledger", "lgc5",
              {"syncId": "lgc5", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")

        r = client.post(
            "/api/v1/write/ledgers/lgc5/accounts",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "name": "招行信用卡",
                "account_type": "credit_card",
                "bank_name": "招商银行",
                "card_number": "6225880212345678",
                "icon": "cmb",
            },
        )
        assert r.status_code == 200, r.text
        acc_a = r.json()["entity_id"]

        # 同一账本再建一个账户 → 走同样的 _commit_write → build/diff 链路
        r = client.post(
            "/api/v1/write/ledgers/lgc5/accounts",
            headers=hdr_web,
            json={"base_change_id": 0, "name": "第二张卡", "account_type": "bank_card"},
        )
        assert r.status_code == 200, r.text
        acc_b = r.json()["entity_id"]

        # 再写一笔交易(另一类 mutate,同样触发全量 diff)
        r = client.post(
            "/api/v1/write/ledgers/lgc5/transactions",
            headers=hdr_web,
            json={
                "base_change_id": 0,
                "tx_type": "expense",
                "amount": 10.0,
                "happened_at": _iso(),
                "account_id": acc_b,
                "account_name": "第二张卡",
            },
        )
        assert r.status_code == 200, r.text

        row_a = _account_row(TS, "card5@t.com", acc_a)
        assert row_a.card_number == "6225880212345678", "card_number 被后续写入抹掉"
        assert row_a.icon == "cmb", "icon 被后续写入抹掉"
        assert row_a.card_last_four == "5678"

        # 反向确认:A 的 upsert change 只有创建那一跳(未被无变化重发)
        with TS() as db:
            from sqlalchemy import text
            n = db.execute(
                text("SELECT COUNT(*) FROM sync_changes "
                     "WHERE entity_type='account' AND entity_sync_id=:sid"),
                {"sid": acc_a},
            ).scalar()
        assert n == 1, f"account A 被重发了 {n} 次(期望 1 次)"
    finally:
        app.dependency_overrides.clear()


def test_web_update_account_clears_icon():
    """PATCH 显式带 icon=null → 清空(回退按类型默认图标)。"""
    client, TS = _make_client()
    try:
        app_tok = _login(client, "card4@t.com", device_id="d-app", client_type="app")
        web_tok = _login(client, "card4@t.com", device_id="d-web", client_type="web")
        hdr_app = {"Authorization": f"Bearer {app_tok}"}
        hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}

        _push(client, hdr_app, "lgc4", "ledger", "lgc4",
              {"syncId": "lgc4", "ledgerName": "账本", "currency": "CNY"}, device_id="d-app")
        _push(client, hdr_app, "lgc4", "account", "acc-c4",
              {"syncId": "acc-c4", "name": "临时卡", "type": "bank_card",
               "currency": "CNY", "icon": "cmb"},
              device_id="d-app")

        r = client.patch(
            "/api/v1/write/ledgers/lgc4/accounts/acc-c4",
            headers=hdr_web,
            json={"base_change_id": 0, "icon": None},
        )
        assert r.status_code == 200, r.text
        row = _account_row(TS, "card4@t.com", "acc-c4")
        assert row.icon is None
    finally:
        app.dependency_overrides.clear()
