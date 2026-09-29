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

import pytest
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
from src.services import recurring_materializer
from src.services.recurring_materializer import materialize_due_stock_rules, stock_dca_occurrence_ids


@pytest.fixture(autouse=True)
def _no_live_quote_fetch(monkeypatch):
    """到期生成前會補抓上游報價(`_refresh_quotes`),測試裡一律關掉,不打
    真的網路;需要模擬「補抓到報價」的測試自己再 monkeypatch 一次。"""
    calls: list[list[tuple[str, str]]] = []
    monkeypatch.setattr(
        recurring_materializer, "_refresh_quotes", lambda db, keys, *, now: calls.append(list(keys)),
    )
    return calls


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


# ---------------------------------------------------------------------------
# 2026-09-29 修正回歸測試
# ---------------------------------------------------------------------------


def _setup(client, email, ledger_id, *, settlement_balance=100000.0):
    owner = _register(client, email)
    app_token, device = owner["access_token"], owner["device_id"]
    _seed_ledger(client, app_token, device, ledger_id)
    hdr_app = {"Authorization": f"Bearer {app_token}"}
    _setup_accounts(client, hdr_app, ledger_id, settlement_balance=settlement_balance)
    web = _login_web(client, email)
    token = web["access_token"]
    return hdr_app, {"Authorization": f"Bearer {token}"}, token


def _rule_row(TS, rule_id):
    with TS() as db:
        return db.scalar(select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id))


def test_web_patch_keeps_stock_dca_kind():
    """snapshot_builder 以前沒選 kind/market/symbol,Web 任何 PATCH 都會把
    規則沖回 kind='general'。"""
    client, TS = _make_client()
    try:
        _hdr_app, hdr, token = _setup(client, "dca-r1@example.com", "L_R1")
        res = _create_stock_dca_rule(
            client, hdr, "L_R1", token, overrides={"stock_fee_rate": 0.001, "stock_fee_min": 1},
        )
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        base = _latest_change_id(client, token, "L_R1")
        r = client.patch(
            f"/api/v1/write/ledgers/L_R1/recurring-rules/{rule_id}", headers=hdr,
            json={"base_change_id": base, "amount": 5000.0},
        )
        assert r.status_code == 200, r.text
        row = _rule_row(TS, rule_id)
        assert row.kind == "stock_dca"
        assert (row.market, row.symbol, row.security_name) == ("TW", "0050", "元大台灣50")
        assert row.amount == 5000.0
        assert row.stock_fee_rate == 0.001 and row.stock_fee_min == 1

        # 清除手續費覆寫(null = 改回沿用帳戶預設)。
        base = _latest_change_id(client, token, "L_R1")
        r = client.patch(
            f"/api/v1/write/ledgers/L_R1/recurring-rules/{rule_id}", headers=hdr,
            json={"base_change_id": base, "stock_fee_rate": None, "stock_fee_min": None},
        )
        assert r.status_code == 200, r.text
        row = _rule_row(TS, rule_id)
        assert row.kind == "stock_dca"
        assert row.stock_fee_rate is None and row.stock_fee_min is None

        full = client.get("/api/v1/read/ledgers/L_R1/recurring-rules", headers=hdr)
        assert full.status_code == 200, full.text
        listed = next(x for x in full.json() if x["id"] == rule_id)
        assert listed["kind"] == "stock_dca" and listed["symbol"] == "0050"
    finally:
        app.dependency_overrides.clear()


def test_app_push_stock_dca_rule_and_partial_push_keeps_kind():
    """App 推上來的 stock_dca 規則要原樣落 projection;舊版 App 不帶 kind 的
    partial push 不能把 kind 沖掉(merge spec 以前沒登記這幾個欄位)。"""
    client, TS = _make_client()
    try:
        hdr_app, _hdr, _token = _setup(client, "dca-r2@example.com", "L_R2")
        next_run = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        _push(client, hdr_app, "L_R2", "recurring_rule", "rule-app", {
            "syncId": "rule-app", "txType": "transfer", "amount": 3000.0,
            "fromAccountId": "acc-bank", "toAccountId": "acc-inv",
            "frequency": "monthly", "interval": 1, "nextRunAt": next_run, "enabled": True,
            "kind": "stock_dca", "market": "TW", "symbol": "0050", "securityName": "元大台灣50",
            "stockFeeRate": 0.0005, "stockFeeMin": 1.0,
        })
        row = _rule_row(TS, "rule-app")
        assert row.kind == "stock_dca" and row.symbol == "0050" and row.stock_fee_rate == 0.0005

        _push(client, hdr_app, "L_R2", "recurring_rule", "rule-app", {
            "syncId": "rule-app", "txType": "transfer", "amount": 4000.0,
            "fromAccountId": "acc-bank", "toAccountId": "acc-inv",
            "frequency": "monthly", "interval": 1, "nextRunAt": next_run, "enabled": True,
        })
        row = _rule_row(TS, "rule-app")
        assert row.amount == 4000.0
        assert row.kind == "stock_dca" and row.market == "TW" and row.symbol == "0050"
        assert row.stock_fee_rate == 0.0005 and row.stock_fee_min == 1.0

        # 顯式 null = 清除手續費覆寫。
        _push(client, hdr_app, "L_R2", "recurring_rule", "rule-app", {
            "syncId": "rule-app", "stockFeeRate": None, "stockFeeMin": None,
        })
        row = _rule_row(TS, "rule-app")
        assert row.kind == "stock_dca"
        assert row.stock_fee_rate is None and row.stock_fee_min is None
    finally:
        app.dependency_overrides.clear()


def test_app_created_stock_dca_not_materialized_as_plain_transfer():
    """transfer 自動扣繳排程要排除 stock_dca,只能走股票定期定額排程。"""
    client, TS = _make_client()
    try:
        hdr_app, _hdr, _token = _setup(client, "dca-r3@example.com", "L_R3")
        _insert_quote(TS, price=100.0)
        due = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _push(client, hdr_app, "L_R3", "recurring_rule", "rule-app3", {
            "syncId": "rule-app3", "txType": "transfer", "amount": 3000.0,
            "fromAccountId": "acc-bank", "toAccountId": "acc-inv",
            "frequency": "monthly", "interval": 1, "nextRunAt": due, "enabled": True,
            "kind": "stock_dca", "market": "TW", "symbol": "0050",
        })
        with TS() as db:
            transfer = recurring_materializer.materialize_due_transfer_rules(db)
            assert transfer["materialized"] == 0
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 1
            trade = db.scalar(select(ReadStockTradeProjection))
            assert trade is not None and trade.symbol == "0050"
            tx_id, trade_id = stock_dca_occurrence_ids("rule-app3", datetime.fromisoformat(due))
            assert trade.sync_id == trade_id and trade.tx_sync_id == tx_id
    finally:
        app.dependency_overrides.clear()


def test_run_now_job_materializes_rule_due_minutes_ago(_no_live_quote_fetch, monkeypatch):
    """使用者回報:11:50 手動執行排程,下次執行時間 11:45 的規則沒有生成。
    根因之一是還沒持有的標的快取裡沒有報價——現在生成前會先補抓。"""
    from src.services import scheduled_jobs

    client, TS = _make_client()
    try:
        _hdr_app, hdr, token = _setup(client, "dca-r4@example.com", "L_R4")
        next_run = datetime.now(timezone.utc) - timedelta(minutes=5)
        res = _create_stock_dca_rule(client, hdr, "L_R4", token, overrides={"next_run_at": next_run.isoformat()})
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]

        def fake_refresh(db, keys, *, now):
            _no_live_quote_fetch.append(list(keys))
            _insert_quote(TS, price=50.0)

        monkeypatch.setattr(recurring_materializer, "_refresh_quotes", fake_refresh)
        with TS() as db:
            scheduled_jobs.ensure_default_configs(db)
            out = scheduled_jobs.run_job(db, "stock_dca_materialization")
            assert out["status"] == "ok", out
            assert out["summary"]["materialized"] == 1, out
        assert _no_live_quote_fetch[-1] == [("TW", "0050")]
        with TS() as db:
            trade = db.scalar(select(ReadStockTradeProjection))
            assert abs(trade.shares - 60.0) < 1e-9
            assert trade.price == 50.0
            row = db.scalar(select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == rule_id))
            assert row.generated_until_at is not None
            # 再跑一次不會重複生成同一期。
            again = materialize_due_stock_rules(db)
            db.commit()
            assert again["materialized"] == 0
            assert len(db.scalars(select(ReadStockTradeProjection)).all()) == 1
    finally:
        app.dependency_overrides.clear()


def test_materializer_skips_occurrence_app_already_generated():
    """App 用同一組固定 syncId 先生成並推上來的那一期,Cloud 不重複生成。"""
    client, TS = _make_client()
    try:
        hdr_app, _hdr, _token = _setup(client, "dca-r5@example.com", "L_R5")
        _insert_quote(TS, price=100.0)
        due_dt = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
        due = due_dt.isoformat()
        _push(client, hdr_app, "L_R5", "recurring_rule", "rule-app5", {
            "syncId": "rule-app5", "txType": "transfer", "amount": 3000.0,
            "fromAccountId": "acc-bank", "toAccountId": "acc-inv",
            "frequency": "monthly", "interval": 1, "nextRunAt": due, "enabled": True,
            "kind": "stock_dca", "market": "TW", "symbol": "0050",
        })
        tx_id, trade_id = stock_dca_occurrence_ids("rule-app5", due_dt)
        _push(client, hdr_app, "L_R5", "transaction", tx_id, {
            "syncId": tx_id, "type": "transfer", "amount": 3000.0, "happenedAt": due,
            "fromAccountId": "acc-bank", "toAccountId": "acc-inv", "recurringRuleId": "rule-app5",
        })
        _push(client, hdr_app, "L_R5", "stock_trade", trade_id, {
            "syncId": trade_id, "accountId": "acc-inv", "market": "TW", "symbol": "0050",
            "tradeType": "buy", "shares": 30.0, "price": 100.0, "fee": 0.0, "tax": 0.0,
            "amount": 3000.0, "tradeDate": due, "txId": tx_id, "currency": "TWD",
        })
        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 0
            assert len(db.scalars(select(ReadStockTradeProjection)).all()) == 1
            row = db.scalar(select(ReadRecurringRuleProjection).where(ReadRecurringRuleProjection.sync_id == "rule-app5"))
            assert row.generated_until_at is not None
    finally:
        app.dependency_overrides.clear()


def test_future_pregenerated_expense_does_not_block_dca():
    """交割戶掛了每月支出規則:未來 12 個月預生成的支出不能算進「當下餘額」。"""
    client, TS = _make_client()
    try:
        hdr_app, hdr, token = _setup(client, "dca-r6@example.com", "L_R6", settlement_balance=5000.0)
        _insert_quote(TS, price=100.0)
        far = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
        _push(client, hdr_app, "L_R6", "transaction", "tx-future", {
            "syncId": "tx-future", "type": "expense", "amount": 4500.0, "happenedAt": far,
            "accountId": "acc-bank",
        })
        res = _create_stock_dca_rule(
            client, hdr, "L_R6", token, overrides={"stock_fee_rate": 0, "stock_fee_min": 0},
        )
        assert res.status_code == 200, res.text
        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            assert result["materialized"] == 1, result
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_rejects_cross_currency_settlement():
    client, TS = _make_client()
    try:
        hdr_app, hdr, token = _setup(client, "dca-r7@example.com", "L_R7")
        res = _create_stock_dca_rule(client, hdr, "L_R7", token, overrides={"market": "US", "symbol": "VOO"})
        assert res.status_code == 400, res.text
    finally:
        app.dependency_overrides.clear()


def test_close_quote_keys_include_active_dca_symbols():
    from src.services.securities import quotes

    client, TS = _make_client()
    try:
        _hdr_app, hdr, token = _setup(client, "dca-r8@example.com", "L_R8")
        res = _create_stock_dca_rule(client, hdr, "L_R8", token, overrides={"symbol": "006208"})
        assert res.status_code == 200, res.text
        with TS() as db:
            assert "006208" in quotes.held_keys(db).get("TW", set())
    finally:
        app.dependency_overrides.clear()


def test_stock_dca_occurrence_ids_are_stable():
    """跟 App `stockDcaOccurrenceIds` 對照的固定值(改格式兩邊要一起改)。"""
    occ = datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc)
    tx_id, trade_id = stock_dca_occurrence_ids("rule-abc", occ)
    assert (tx_id, trade_id) == stock_dca_occurrence_ids("rule-abc", occ.replace(microsecond=999))
    assert tx_id != trade_id
    # App test/services/investment/stock_dca_test.dart 對照同一組值。
    assert tx_id == "d16236f8-2b7d-5b7e-9efd-bdaf3378b3ad"
    assert trade_id == "733552aa-0945-51af-9a0d-7f3c7a571135"


def test_stale_periods_are_skipped_not_bought_at_todays_price():
    """起始日設在很久以前:超過 7 天的過期期數不能全部用今天的價格補買。"""
    client, TS = _make_client()
    try:
        _hdr_app, hdr, token = _setup(client, "dca-r9@example.com", "L_R9")
        _insert_quote(TS, price=100.0)
        start = datetime.now(timezone.utc) - timedelta(days=20)
        res = _create_stock_dca_rule(
            client, hdr, "L_R9", token,
            overrides={"frequency": "daily", "next_run_at": start.isoformat(), "stock_fee_rate": 0, "stock_fee_min": 0},
        )
        assert res.status_code == 200, res.text
        with TS() as db:
            result = materialize_due_stock_rules(db)
            db.commit()
            # 20 天前起算每日一期:只補最近 7 天內的(7 或 8 期,看邊界時間),其餘略過。
            assert 7 <= result["materialized"] <= 8, result
            assert result["skipped_stale"] == 21 - result["materialized"], result
            notif = db.scalars(select(Notification)).all()
            assert any(n.payload_json.get("kind") == "stale_skipped" for n in notif)
            trades = db.scalars(select(ReadStockTradeProjection)).all()
            cutoff = datetime.now(timezone.utc) - timedelta(days=7, minutes=1)
            for t in trades:
                td = t.trade_date if t.trade_date.tzinfo else t.trade_date.replace(tzinfo=timezone.utc)
                assert td >= cutoff
    finally:
        app.dependency_overrides.clear()


def test_upcoming_run_at_and_reanchor_next_run_after_first_period():
    """第一期生成後:列表的 upcoming_run_at 要是下一期;改「下次執行時間」
    要真的生效(以前 next_run_at 在第一期之後就不再被排程讀取)。"""
    client, TS = _make_client()
    try:
        _hdr_app, hdr, token = _setup(client, "dca-r10@example.com", "L_R10")
        _insert_quote(TS, price=100.0)
        first = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
        res = _create_stock_dca_rule(client, hdr, "L_R10", token, overrides={"next_run_at": first.isoformat()})
        assert res.status_code == 200, res.text
        rule_id = res.json()["entity_id"]
        with TS() as db:
            assert materialize_due_stock_rules(db)["materialized"] == 1
            db.commit()

        def listed():
            rows = client.get("/api/v1/read/ledgers/L_R10/recurring-rules", headers=hdr).json()
            return next(x for x in rows if x["id"] == rule_id)

        upcoming = datetime.fromisoformat(listed()["upcoming_run_at"])
        assert upcoming.month != first.month or upcoming.year != first.year  # 下個月那一期

        # 往前改到最後一期之前 → 400
        base = _latest_change_id(client, token, "L_R10")
        bad = (first - timedelta(days=3)).isoformat()
        r = client.patch(f"/api/v1/write/ledgers/L_R10/recurring-rules/{rule_id}", headers=hdr,
                         json={"base_change_id": base, "next_run_at": bad})
        assert r.status_code == 400, r.text

        # 往後改 → 從新時間重新起算
        new_next = (datetime.now(timezone.utc) + timedelta(days=5)).replace(microsecond=0)
        base = _latest_change_id(client, token, "L_R10")
        r = client.patch(f"/api/v1/write/ledgers/L_R10/recurring-rules/{rule_id}", headers=hdr,
                         json={"base_change_id": base, "next_run_at": new_next.isoformat()})
        assert r.status_code == 200, r.text
        row = _rule_row(TS, rule_id)
        assert row.generated_until_at is None and row.kind == "stock_dca"
        assert datetime.fromisoformat(listed()["upcoming_run_at"]) == new_next

        # 沒改時間(前端每次都會送目前值)不受影響、也不報錯
        base = _latest_change_id(client, token, "L_R10")
        r = client.patch(f"/api/v1/write/ledgers/L_R10/recurring-rules/{rule_id}", headers=hdr,
                         json={"base_change_id": base, "next_run_at": new_next.isoformat(), "amount": 2000})
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
