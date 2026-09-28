"""股票定期定額(`kind='stock_dca'`,2026-09-28,docs/STOCK_HOLDINGS_SD.md §9)
—— `recurring_rule` entity 新增分類的契约测试:

- `POST /write/ledgers/{id}/recurring-rules`(`kind='stock_dca'`)建立時的
  校驗:必須是 `tx_type='transfer'`、market/symbol 必填、`to_account_id`
  必須是投資理財帳戶,建立當下不預生成任何 occurrence(同自動扣繳
  transfer 規則)。
- `services.recurring_materializer.materialize_due_stock_rules`:到期當下
  抓本地報價快取算股數/手續費、檢查交割帳戶餘額,生成 `stock_trade` 明細 +
  綁定的轉帳交易;報價缺失/餘額不足各自跳過並各自去重通知。
- 規則層級手續費覆寫(`stock_fee_rate`/`stock_fee_min`)優先於投資理財帳戶
  的預設 `investment_settings_json`。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    Notification,
    ReadRecurringRuleProjection,
    ReadStockTradeProjection,
    ReadTxProjection,
    Security,
    SecurityQuote,
)
from src.services.recurring_materializer import materialize_due_stock_rules


def _make_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
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


def _register(client, email):
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": email, "password": "123456", "client_type": "app",
            "device_name": "pytest-app", "platform": "app", "device_id": "d-app",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _login_web(client, email):
    r = client.post(
        "/api/v1/auth/login",
        json={
            "email": email, "password": "123456", "client_type": "web",
            "device_name": "pytest-web", "platform": "web",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _seed_ledger(client, token, device_id, ledger_id):
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"TWD","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    r = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [{
                "ledger_id": ledger_id, "entity_type": "ledger_snapshot",
                "entity_sync_id": ledger_id, "action": "upsert",
                "payload": {"content": content}, "updated_at": _iso(),
            }],
        },
    )
    assert r.status_code == 200, r.text


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d-app", action="upsert"):
    body = {
        "ledger_id": ledger_id, "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": action, "updated_at": _iso(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _latest_change_id(client, token, ledger_id):
    r = client.get(f"/api/v1/read/ledgers/{ledger_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return int(r.json()["source_change_id"])


def _setup_accounts(client, hdr_app, ledger_id, *, settlement_balance=100000.0):
    _push(client, hdr_app, ledger_id, "account", "acc-bank",
          {"syncId": "acc-bank", "name": "交割戶", "type": "cash", "currency": "TWD",
           "initialBalance": settlement_balance})
    _push(client, hdr_app, ledger_id, "account", "acc-inv",
          {"syncId": "acc-inv", "name": "證券", "type": "investment", "currency": "TWD"})


def _insert_quote(TS, *, market="TW", symbol="0050", price=97.45):
    with TS() as db:
        sec = Security(market=market, symbol=symbol, name="元大台灣50", currency="TWD", kind="etf")
        db.add(sec)
        db.flush()
        db.add(SecurityQuote(
            security_id=sec.id, price=price, source="manual",
            fetched_at=datetime.now(timezone.utc),
        ))
        db.commit()


def _create_stock_dca_rule(client, hdr, ledger_id, token, *, overrides=None):
    next_run = datetime.now(timezone.utc) - timedelta(days=1)  # 已到期
    base = _latest_change_id(client, token, ledger_id)
    body = {
        "base_change_id": base,
        "tx_type": "transfer",
        "kind": "stock_dca",
        "amount": 3000.0,
        "frequency": "monthly",
        "next_run_at": next_run.isoformat(),
        "from_account_id": "acc-bank",
        "to_account_id": "acc-inv",
        "market": "TW",
        "symbol": "0050",
        "security_name": "元大台灣50",
    }
    if overrides:
        body.update(overrides)
    return client.post(f"/api/v1/write/ledgers/{ledger_id}/recurring-rules", headers=hdr, json=body)


def test_stock_dca_rule_not_bulk_generated_at_creation():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca1@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA1"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _setup_accounts(client, hdr_app, ledger_id)

        web = _login_web(client, "dca1@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(client, hdr, ledger_id, token)
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.kind == "stock_dca"
            assert rule_row.market == "TW"
            assert rule_row.symbol == "0050"
            assert rule_row.generated_until_at is None
            assert rule_row.enabled is True

            txs = db.scalars(
                select(ReadTxProjection).where(ReadTxProjection.recurring_rule_sync_id == rule_id)
            ).all()
            assert txs == []
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_rule_rejects_non_transfer_tx_type():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca2@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA2"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _setup_accounts(client, hdr_app, ledger_id)
        _push(client, hdr_app, ledger_id, "category", "cat-1", {"syncId": "cat-1", "name": "投資", "kind": "expense"})

        web = _login_web(client, "dca2@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(
            client, hdr, ledger_id, token,
            overrides={"tx_type": "expense", "category_id": "cat-1", "from_account_id": None, "to_account_id": None,
                       "account_id": "acc-bank"},
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_rule_rejects_non_investment_to_account():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca3@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA3"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _push(client, hdr_app, ledger_id, "account", "acc-bank",
              {"syncId": "acc-bank", "name": "交割戶", "type": "cash", "currency": "TWD", "initialBalance": 100000.0})
        _push(client, hdr_app, ledger_id, "account", "acc-other",
              {"syncId": "acc-other", "name": "非投資帳戶", "type": "cash", "currency": "TWD"})

        web = _login_web(client, "dca3@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(
            client, hdr, ledger_id, token, overrides={"to_account_id": "acc-other"},
        )
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_materializes_when_due_with_quote_and_balance():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca4@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA4"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _setup_accounts(client, hdr_app, ledger_id)
        _insert_quote(TS, price=97.45)

        web = _login_web(client, "dca4@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(client, hdr, ledger_id, token)
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 1
            assert result["skipped_insufficient"] == 0
            assert result["skipped_no_quote"] == 0

            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is not None

            ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_id))
            trades = db.scalars(
                select(ReadStockTradeProjection).where(ReadStockTradeProjection.ledger_id == ledger.id)
            ).all()
            assert len(trades) == 1
            trade = trades[0]
            assert trade.trade_type == "buy"
            assert trade.market == "TW"
            assert trade.symbol == "0050"
            assert abs(trade.shares - 3000 / 97.45) < 1e-6
            assert trade.tx_sync_id is not None

            tx = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == trade.tx_sync_id))
            assert tx is not None
            assert tx.tx_type == "transfer"
            assert tx.from_account_sync_id == "acc-bank"
            assert tx.to_account_sync_id == "acc-inv"
            assert tx.recurring_rule_sync_id == rule_id
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_skips_when_quote_missing():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca5@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA5"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _setup_accounts(client, hdr_app, ledger_id)
        # 刻意不插入報價。

        web = _login_web(client, "dca5@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(client, hdr, ledger_id, token)
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 0
            assert result["skipped_no_quote"] == 1

            rule_row = db.scalar(
                select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id)
            )
            assert rule_row.generated_until_at is None

            notif = db.scalar(
                select(Notification).where(Notification.user_id == rule_row.user_id)
            )
            assert notif is not None
            assert notif.payload_json.get("kind") == "quote_unavailable"
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_skips_when_balance_insufficient():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca6@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA6"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _setup_accounts(client, hdr_app, ledger_id, settlement_balance=100.0)
        _insert_quote(TS, price=97.45)

        web = _login_web(client, "dca6@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(client, hdr, ledger_id, token)
        assert res.status_code == 200, res.text

        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 0
            assert result["skipped_insufficient"] == 1
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_custom_fee_override_takes_priority():
    client, TS = _make_client()
    try:
        owner = _register(client, "dca7@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_DCA7"
        _seed_ledger(client, app_token, device, ledger_id)
        hdr_app = {"Authorization": f"Bearer {app_token}"}
        _setup_accounts(client, hdr_app, ledger_id)
        _insert_quote(TS, price=100.0)

        web = _login_web(client, "dca7@example.com")
        token = web["access_token"]
        hdr = {"Authorization": f"Bearer {token}"}

        res = _create_stock_dca_rule(
            client, hdr, ledger_id, token,
            overrides={"amount": 10000.0, "stock_fee_rate": 0, "stock_fee_min": 0},
        )
        assert res.status_code == 200, res.text

        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 1

            ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_id))
            trade = db.scalar(
                select(ReadStockTradeProjection).where(ReadStockTradeProjection.ledger_id == ledger.id)
            )
            assert trade.fee == 0.0
    finally:
        app.dependency_overrides.clear()
