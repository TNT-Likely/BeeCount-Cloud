"""股票持股 Phase 1(docs/STOCK_HOLDINGS_SD.md):

- 持股計算跑 App/Cloud 共用的測試向量(tests/fixtures/stock_holdings_vectors.json)
- provider 解析(證交所/櫃買/Yahoo 錄好的回應,不打真實網路)
- 市場時段判斷(含美股夏令時間)
- mobile push `stock_trade` / account `investmentSettings` 的 partial-update
  merge 契約
- web 寫入:buy/sell 連帶建立轉帳交易、賣超擋下、編輯重算、刪明細連帶刪
  交易、刪交易連帶刪明細、跨幣別必須帶 settlement_amount
- `/workspace/holdings` 市值與主幣別折算、收盤排程只抓持有標的

============================================================================
手动检查清单(pytest 测不到的运行时行为):

1. 真的連到證交所/Yahoo:`GET /api/v1/read/securities/search?q=台積` 應回
   TW:2330;`?q=apple` 應回 US:AAPL。
2. 排程:`/admin/scheduled-jobs` 看得到 `security_quote_close`,台股收盤後
   (台北 15:00 後)手動執行一次,`last_run_message` 的 markets 應 > 0。
============================================================================
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    ReadStockTradeProjection,
    ReadTxProjection,
    Security,
    SecurityQuote,
    User,
    UserAccountProjection,
    UserProfile,
)
from src.services.securities import holdings, markets, quotes
from src.services.securities.providers import base as provider_base
from src.services.securities.providers import twse, yahoo

FIXTURES = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #


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
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
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


def _push(client, hdr, ledger_id, entity_type, sync_id, payload, *, device_id="d-app", action="upsert"):
    body = {
        "ledger_id": ledger_id,
        "entity_type": entity_type,
        "entity_sync_id": sync_id,
        "action": action,
        "updated_at": _iso(),
        "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": device_id, "changes": [body]})
    assert r.status_code == 200, r.text
    return r.json()


def _setup(client, email, *, ledger="lg1", inv_currency="TWD", bank_currency="TWD"):
    app_tok = _login(client, email, device_id="d-app", client_type="app")
    web_tok = _login(client, email, device_id="d-web", client_type="web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, ledger, "ledger", ledger, {"syncId": ledger, "ledgerName": "帳本", "currency": "TWD"})
    _push(client, hdr_app, ledger, "account", "acc_bank",
          {"syncId": "acc_bank", "name": "交割戶", "type": "bank_card", "currency": bank_currency})
    _push(client, hdr_app, ledger, "account", "acc_inv",
          {"syncId": "acc_inv", "name": "證券", "type": "investment", "currency": inv_currency,
           "includeInTotal": False})
    return hdr_app, hdr_web


def _trade_rows(TS, ledger_external_id="lg1"):
    with TS() as db:
        ledger = db.scalar(select(Ledger).where(Ledger.external_id == ledger_external_id))
        rows = db.scalars(
            select(ReadStockTradeProjection).where(ReadStockTradeProjection.ledger_id == ledger.id)
        ).all()
        for r in rows:
            db.expunge(r)
        return rows


def _tx_row(TS, sync_id):
    with TS() as db:
        row = db.scalar(select(ReadTxProjection).where(ReadTxProjection.sync_id == sync_id))
        if row is not None:
            db.expunge(row)
        return row


def _buy(client, hdr_web, **overrides):
    body = {
        "base_change_id": 0,
        "account_id": "acc_inv",
        "trade_type": "buy",
        "market": "TW",
        "symbol": "2330",
        "security_name": "台積電",
        "shares": 1000,
        "price": 600,
        "fee": 855,
        "tax": 0,
        "trade_date": "2026-09-01T02:00:00+00:00",
        "settlement_account_id": "acc_bank",
    }
    body.update(overrides)
    return client.post("/api/v1/write/ledgers/lg1/stock-trades", headers=hdr_web, json=body)


# --------------------------------------------------------------------------- #
# 持股計算 — 共用測試向量                                                      #
# --------------------------------------------------------------------------- #


def _vector_cases():
    data = json.loads((FIXTURES / "stock_holdings_vectors.json").read_text())
    return [pytest.param(c, id=c["name"]) for c in data["cases"]]


@pytest.mark.parametrize("case", _vector_cases())
def test_holdings_shared_vectors(case):
    rows = [holdings.trade_row_from_payload(t) for t in case["trades"]]
    got = [h.to_dict() for h in holdings.compute_holdings(rows, include_closed=True)]
    assert len(got) == len(case["expected"])
    for g, e in zip(got, case["expected"]):
        for key, value in e.items():
            if isinstance(value, (int, float)):
                assert g[key] == pytest.approx(value, abs=1e-6), key
            else:
                assert g[key] == value, key


def test_realized_events_shared_vectors():
    for case in json.loads((FIXTURES / "stock_holdings_vectors.json").read_text())["cases"]:
        if "realizedEvents" not in case:
            continue
        rows = [holdings.trade_row_from_payload(t) for t in case["trades"]]
        events: list = []
        holdings.compute_holdings(rows, include_closed=True, events=events)
        got = [e.to_dict() for e in events]
        assert len(got) == len(case["realizedEvents"])
        for g, e in zip(got, case["realizedEvents"], strict=True):
            for key, value in e.items():
                if isinstance(value, (int, float)):
                    assert g[key] == pytest.approx(value, abs=1e-6), key
                else:
                    assert g[key] == value, key


def test_holdings_excludes_closed_positions_by_default():
    rows = [
        holdings.TradeRow("a", "acc", "US", "X", "buy", 1, 10, 0, 0, 10, "2026-01-01"),
        holdings.TradeRow("b", "acc", "US", "X", "sell", 1, 12, 0, 0, 12, "2026-01-02"),
    ]
    assert holdings.compute_holdings(rows) == []
    assert holdings.compute_holdings(rows, include_closed=True)[0].realized_pnl == pytest.approx(2)


# --------------------------------------------------------------------------- #
# providers                                                                    #
# --------------------------------------------------------------------------- #


def test_parse_twse_filters_to_stocks_and_etfs():
    snap = twse.parse_twse(json.loads((FIXTURES / "twse_stock_day_all.json").read_text()))
    symbols = {s.symbol: s for s in snap.securities}
    # fixture 另外帶了一筆 0100xx 權證類代號,必須被濾掉
    assert set(symbols) == {"2330", "0050", "00878"}
    assert symbols["2330"].name == "台積電"
    assert symbols["0050"].kind == "etf"
    assert all(not s.symbol.startswith("0100") for s in snap.securities)
    q = next(q for q in snap.quotes if q.symbol == "2330")
    assert q.market == "TW" and q.currency == "TWD"
    assert q.price == 2475.0
    assert q.prev_close == pytest.approx(2500.0)
    assert snap.trade_date.isoformat() == "2026-09-24"
    # 收盤時間 13:30 台北 = 05:30 UTC
    assert q.quote_time == datetime(2026, 9, 24, 5, 30, tzinfo=timezone.utc)


def test_parse_tpex_marks_bond_etf():
    snap = twse.parse_tpex(json.loads((FIXTURES / "tpex_daily_close.json").read_text()))
    symbols = {s.symbol: s for s in snap.securities}
    assert symbols["6488"].market == "TWO"
    assert symbols["00679B"].kind == "bond_etf"
    q = next(q for q in snap.quotes if q.symbol == "6488")
    assert q.price == 948.0 and q.prev_close == pytest.approx(950.0)


def test_parse_roc_date():
    assert twse.parse_roc_date("1150924").isoformat() == "2026-09-24"
    assert twse.parse_roc_date("bad") is None


def test_parse_yahoo_chart():
    q = yahoo.parse_chart(json.loads((FIXTURES / "yahoo_chart_aapl.json").read_text()), market="US", symbol="AAPL")
    assert q.price == pytest.approx(341.07)
    assert q.prev_close == pytest.approx(336.13)
    assert q.currency == "USD"
    assert q.name == "Apple Inc."
    assert q.quote_time is not None


def test_yahoo_chart_uses_one_day_range_for_previous_close():
    """chartPreviousClose 是圖表區間開始前的收盤價;range 不是 1d 的話今日漲跌
    會算成跟好幾天前比(2026-09-28 實機踩到:2330 顯示 +0.61%,實際 -1%)。"""
    assert yahoo.CHART_PARAMS["range"] == "1d"


def test_parse_yahoo_chart_converts_pence():
    payload = {"chart": {"result": [{"meta": {"currency": "GBp", "regularMarketPrice": 250.0,
                                              "chartPreviousClose": 200.0, "regularMarketTime": 1790000000}}]}}
    q = yahoo.parse_chart(payload, market="LSE", symbol="VOD")
    assert q.price == pytest.approx(2.5) and q.prev_close == pytest.approx(2.0) and q.currency == "GBP"


def test_parse_yahoo_search_keeps_supported_equities_only():
    results = yahoo.parse_search(json.loads((FIXTURES / "yahoo_search_apple.json").read_text()))
    keys = [(r.market, r.symbol) for r in results]
    assert ("US", "AAPL") in keys
    assert all(not s.endswith("=F") for _, s in keys)  # 期貨排除
    assert all(m in markets.MARKETS for m, _ in keys)


def test_yahoo_symbol_roundtrip():
    assert markets.yahoo_symbol("TW", "2330") == "2330.TW"
    assert markets.yahoo_symbol("US", "aapl") == "AAPL"
    assert markets.from_yahoo_symbol("6488.TWO") == ("TWO", "6488")
    assert markets.from_yahoo_symbol("AAPL.TO") is None
    assert markets.from_yahoo_symbol("AAPL", "NMS") == ("US", "AAPL")


# --------------------------------------------------------------------------- #
# 市場時段                                                                     #
# --------------------------------------------------------------------------- #


def test_us_market_hours_follow_dst():
    us = markets.MARKETS["US"]
    # 夏令(EDT, UTC-4):09:30 開盤 = 13:30 UTC
    assert markets.is_market_open(us, datetime(2026, 7, 1, 13, 45, tzinfo=timezone.utc))
    assert not markets.is_market_open(us, datetime(2026, 7, 1, 13, 15, tzinfo=timezone.utc))
    # 冬令(EST, UTC-5):09:30 開盤 = 14:30 UTC
    assert not markets.is_market_open(us, datetime(2026, 12, 1, 14, 15, tzinfo=timezone.utc))
    assert markets.is_market_open(us, datetime(2026, 12, 1, 14, 45, tzinfo=timezone.utc))


def test_close_fetch_threshold_tw_and_weekend():
    tw = markets.MARKETS["TW"]
    # 2026-09-24 週四,台北 14:59 還沒到門檻(13:30 + 90 分)
    assert markets.close_fetch_threshold(tw, datetime(2026, 9, 24, 6, 59, tzinfo=timezone.utc)) is None
    th = markets.close_fetch_threshold(tw, datetime(2026, 9, 24, 7, 1, tzinfo=timezone.utc))
    assert th == datetime(2026, 9, 24, 7, 0, tzinfo=timezone.utc)
    # 2026-09-26 週六
    assert markets.close_fetch_threshold(tw, datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)) is None


# --------------------------------------------------------------------------- #
# mobile push merge 契約                                                       #
# --------------------------------------------------------------------------- #


def _full_trade_payload(**overrides):
    payload = {
        "syncId": "stk_1",
        "accountId": "acc_inv",
        "market": "TW",
        "symbol": "2330",
        "securityName": "台積電",
        "tradeType": "buy",
        "shares": 1000,
        "price": 600,
        "fee": 855,
        "tax": 0,
        "amount": 600855,
        "currency": "TWD",
        "tradeDate": "2026-09-01T02:00:00+00:00",
        "txId": "tx_1",
        "note": "第一筆",
    }
    payload.update(overrides)
    return payload


def test_mobile_push_stock_trade_persists_all_fields():
    client, TS = _make_client()
    try:
        hdr_app, _ = _setup(client, "stk-push1@t.com")
        _push(client, hdr_app, "lg1", "stock_trade", "stk_1", _full_trade_payload())
        [row] = _trade_rows(TS)
        assert (row.account_sync_id, row.market, row.symbol, row.trade_type) == ("acc_inv", "TW", "2330", "buy")
        assert row.shares == 1000 and row.price == 600 and row.fee == 855 and row.amount == 600855
        assert row.tx_sync_id == "tx_1" and row.note == "第一筆" and row.security_name == "台積電"
        assert row.currency == "TWD"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_stock_trade_partial_update_keeps_existing_fields():
    """merge 契約(CLAUDE.md 硬門檻):後續 push 只帶 note,其它欄位不能被
    沖成預設值。"""
    client, TS = _make_client()
    try:
        hdr_app, _ = _setup(client, "stk-push2@t.com")
        _push(client, hdr_app, "lg1", "stock_trade", "stk_1", _full_trade_payload())
        _push(client, hdr_app, "lg1", "stock_trade", "stk_1", {"syncId": "stk_1", "note": "改備註"})
        [row] = _trade_rows(TS)
        assert row.note == "改備註"
        assert row.shares == 1000 and row.price == 600 and row.fee == 855
        assert row.tx_sync_id == "tx_1" and row.account_sync_id == "acc_inv"
        assert row.trade_date is not None and row.trade_date.date().isoformat() == "2026-09-01"
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_stock_trade_delete():
    client, TS = _make_client()
    try:
        hdr_app, _ = _setup(client, "stk-push3@t.com")
        _push(client, hdr_app, "lg1", "stock_trade", "stk_1", _full_trade_payload())
        _push(client, hdr_app, "lg1", "stock_trade", "stk_1", {}, action="delete")
        assert _trade_rows(TS) == []
    finally:
        app.dependency_overrides.clear()


def test_account_investment_settings_partial_update_keeps_existing():
    client, TS = _make_client()
    try:
        hdr_app, _ = _setup(client, "stk-acc1@t.com")
        settings = {"market": "TW", "feeRate": 0.001425, "feeDiscount": 0.6, "feeMin": 20,
                    "sellTaxRate": 0.003, "settlementAccountId": "acc_bank"}
        _push(client, hdr_app, "lg1", "account", "acc_inv",
              {"syncId": "acc_inv", "name": "證券", "type": "investment", "currency": "TWD",
               "investmentSettings": settings})
        _push(client, hdr_app, "lg1", "account", "acc_inv", {"syncId": "acc_inv", "name": "證券改名"})
        with TS() as db:
            row = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc_inv"))
            assert row.name == "證券改名"
            assert json.loads(row.investment_settings_json) == settings
    finally:
        app.dependency_overrides.clear()


def test_snapshot_and_account_read_include_investment_fields():
    from src.snapshot_builder import build

    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "stk-snap@t.com")
        _push(client, hdr_app, "lg1", "account", "acc_inv",
              {"syncId": "acc_inv", "investmentSettings": {"feeRate": 0.001}})
        _push(client, hdr_app, "lg1", "stock_trade", "stk_1", _full_trade_payload())
        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "lg1"))
            snap = build(db, ledger)
        acc = next(a for a in snap["accounts"] if a["syncId"] == "acc_inv")
        assert acc["investmentSettings"] == {"feeRate": 0.001}
        [t] = snap["stockTrades"]
        assert t["txId"] == "tx_1" and t["shares"] == 1000 and t["tradeType"] == "buy"

        r = client.get("/api/v1/read/ledgers/lg1/accounts", headers=hdr_web)
        assert r.status_code == 200, r.text
        acc_out = next(a for a in r.json() if a["id"] == "acc_inv")
        assert acc_out["investment_settings"] == {"feeRate": 0.001}
    finally:
        app.dependency_overrides.clear()


def test_web_account_rename_keeps_investment_settings():
    """snapshot_builder 漏選 investment_settings_json 的話,web 改帳戶名時
    diff-emit 會把費用設定沖掉——這條測試守住這個坑。"""
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "stk-acc2@t.com")
        _push(client, hdr_app, "lg1", "account", "acc_inv",
              {"syncId": "acc_inv", "investmentSettings": {"feeRate": 0.001}})
        r = client.patch("/api/v1/write/ledgers/lg1/accounts/acc_inv", headers=hdr_web,
                         json={"base_change_id": 0, "name": "證券改名"})
        assert r.status_code == 200, r.text
        with TS() as db:
            row = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc_inv"))
            assert row.name == "證券改名"
            assert json.loads(row.investment_settings_json) == {"feeRate": 0.001}

        r = client.patch("/api/v1/write/ledgers/lg1/accounts/acc_inv", headers=hdr_web,
                         json={"base_change_id": 0, "investment_settings": {"feeRate": 0.002, "junk": 1}})
        assert r.status_code == 200, r.text
        with TS() as db:
            row = db.scalar(select(UserAccountProjection).where(UserAccountProjection.sync_id == "acc_inv"))
            assert json.loads(row.investment_settings_json) == {"feeRate": 0.002}
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# web 寫入                                                                     #
# --------------------------------------------------------------------------- #


def test_web_buy_creates_transfer_with_fee_and_trade():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w1@t.com")
        r = _buy(client, hdr_web)
        assert r.status_code == 200, r.text
        [trade] = _trade_rows(TS)
        assert trade.sync_id == r.json()["entity_id"]
        assert trade.amount == pytest.approx(600855)
        assert trade.currency == "TWD"
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx is not None
        assert tx.tx_type == "transfer"
        assert tx.from_account_sync_id == "acc_bank" and tx.to_account_sync_id == "acc_inv"
        assert tx.amount == pytest.approx(600000)
        assert tx.fee_amount == pytest.approx(855)
        assert tx.to_amount is None
        assert "2330" in (tx.note or "")
    finally:
        app.dependency_overrides.clear()


def test_web_sell_uses_discount_for_fee_and_tax_and_blocks_oversell():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w2@t.com")
        assert _buy(client, hdr_web).status_code == 200
        r = _buy(client, hdr_web, trade_type="sell", shares=1500, price=700, fee=100, tax=300)
        assert r.status_code == 400, r.text
        assert "only 1000" in r.text
        r = _buy(client, hdr_web, trade_type="sell", shares=400, price=700, fee=399, tax=840,
                 trade_date="2026-09-10T02:00:00+00:00")
        assert r.status_code == 200, r.text
        sell = next(t for t in _trade_rows(TS) if t.trade_type == "sell")
        assert sell.amount == pytest.approx(280000 - 399 - 840)
        tx = _tx_row(TS, sell.tx_sync_id)
        assert tx.from_account_sync_id == "acc_inv" and tx.to_account_sync_id == "acc_bank"
        assert tx.amount == pytest.approx(280000)
        assert tx.discount_amount == pytest.approx(399 + 840)
    finally:
        app.dependency_overrides.clear()


def test_web_cross_currency_buy_requires_settlement_amount():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w3@t.com", inv_currency="USD", bank_currency="TWD")
        base = dict(market="US", symbol="AAPL", security_name="Apple", shares=10, price=200, fee=5,
                    currency="USD")
        r = _buy(client, hdr_web, **base)
        assert r.status_code == 400, r.text
        r = _buy(client, hdr_web, settlement_amount=64500, **base)
        assert r.status_code == 200, r.text
        [trade] = _trade_rows(TS)
        assert trade.amount == pytest.approx(2005)
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.amount == pytest.approx(64500)
        assert tx.to_amount == pytest.approx(2005)
        assert tx.fee_amount is None
    finally:
        app.dependency_overrides.clear()


def test_web_update_trade_recomputes_linked_tx():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w4@t.com")
        trade_id = _buy(client, hdr_web).json()["entity_id"]
        r = client.patch(f"/api/v1/write/ledgers/lg1/stock-trades/{trade_id}", headers=hdr_web,
                         json={"base_change_id": 0, "shares": 2000, "fee": 1710, "note": "加碼"})
        assert r.status_code == 200, r.text
        [trade] = _trade_rows(TS)
        assert trade.shares == 2000 and trade.amount == pytest.approx(1201710) and trade.note == "加碼"
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.amount == pytest.approx(1200000)
        assert tx.fee_amount == pytest.approx(1710)
        assert tx.note == "加碼"
    finally:
        app.dependency_overrides.clear()


def test_web_delete_trade_deletes_linked_tx():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w5@t.com")
        trade_id = _buy(client, hdr_web).json()["entity_id"]
        tx_id = _trade_rows(TS)[0].tx_sync_id
        r = client.request("DELETE", f"/api/v1/write/ledgers/lg1/stock-trades/{trade_id}",
                           headers=hdr_web, json={"base_change_id": 0})
        assert r.status_code == 200, r.text
        assert _trade_rows(TS) == []
        assert _tx_row(TS, tx_id) is None
    finally:
        app.dependency_overrides.clear()


def test_web_delete_tx_deletes_linked_trade():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w6@t.com")
        _buy(client, hdr_web)
        tx_id = _trade_rows(TS)[0].tx_sync_id
        r = client.request("DELETE", f"/api/v1/write/ledgers/lg1/transactions/{tx_id}",
                           headers=hdr_web, json={"base_change_id": 0})
        assert r.status_code == 200, r.text
        assert _tx_row(TS, tx_id) is None
        assert _trade_rows(TS) == []
    finally:
        app.dependency_overrides.clear()


def test_web_trade_rejects_non_investment_account_and_missing_settlement():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w7@t.com")
        assert _buy(client, hdr_web, account_id="acc_bank").status_code == 400
        assert _buy(client, hdr_web, settlement_account_id=None).status_code == 400
        assert _buy(client, hdr_web, settlement_account_id="acc_inv").status_code == 400
        r = _buy(client, hdr_web, trade_type="opening", settlement_account_id=None)
        assert r.status_code == 200, r.text
        [trade] = _trade_rows(TS)
        assert trade.tx_sync_id is None and trade.amount == pytest.approx(600855)
    finally:
        app.dependency_overrides.clear()


def test_web_trade_changes_are_pullable_by_app():
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "stk-w8@t.com")
        _buy(client, hdr_web)
        r = client.get("/api/v1/sync/pull", headers=hdr_app, params={"since": 0, "device_id": "d-app"})
        assert r.status_code == 200, r.text
        entity_types = [c["entity_type"] for c in r.json()["changes"]]
        assert "stock_trade" in entity_types
        assert entity_types.index("transaction") < len(entity_types) - 1 - entity_types[::-1].index("stock_trade")
    finally:
        app.dependency_overrides.clear()


def test_list_stock_trades_endpoint():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-w9@t.com")
        _buy(client, hdr_web)
        r = client.get("/api/v1/read/ledgers/lg1/stock-trades", headers=hdr_web, params={"account_id": "acc_inv"})
        assert r.status_code == 200, r.text
        [t] = r.json()
        assert t["symbol"] == "2330" and t["trade_type"] == "buy" and t["tx_id"]
    finally:
        app.dependency_overrides.clear()


def test_web_split_changes_shares_without_cash_flow_and_feeds_realized_report():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-split@t.com")
        assert _buy(client, hdr_web, shares=100, price=100, fee=0,
                    trade_date="2026-01-01T02:00:00+00:00").status_code == 200
        r = _buy(client, hdr_web, trade_type="split", shares=4, price=None, fee=0, settlement_account_id=None,
                 trade_date="2026-02-01T02:00:00+00:00")
        assert r.status_code == 200, r.text
        split = next(t for t in _trade_rows(TS) if t.trade_type == "split")
        assert split.shares == 4 and split.tx_sync_id is None and split.amount == 0
        # 分割後可賣 400 股,賣超 401 擋下
        assert _buy(client, hdr_web, trade_type="sell", shares=401, price=30, fee=0,
                    trade_date="2026-03-01T02:00:00+00:00").status_code == 400
        assert _buy(client, hdr_web, trade_type="sell", shares=100, price=30, fee=0,
                    trade_date="2026-03-01T02:00:00+00:00").status_code == 200
        rep = client.get("/api/v1/read/workspace/realized-pnl", headers=hdr_web)
        assert rep.status_code == 200, rep.text
        body = rep.json()
        assert body["years"] == [2026]
        assert body["realized_pnl_by_currency"] == {"TWD": pytest.approx(500)}
        [sym] = body["symbols"]
        assert sym["symbol"] == "2330" and sym["sell_count"] == 1
        assert sym["events"][0]["cost_basis"] == pytest.approx(2500)
        # 年度篩選:2025 沒有賣出
        rep25 = client.get("/api/v1/read/workspace/realized-pnl", headers=hdr_web, params={"year": 2025}).json()
        assert rep25["symbols"] == [] and rep25["realized_pnl_by_currency"] == {}
        assert rep25["years"] == [2026]
        assert client.get("/api/v1/read/workspace/realized-pnl", headers=hdr_web,
                          params={"account_id": "nope"}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_mcp_stock_tools_return_holdings_and_realized_pnl(monkeypatch):
    from src.mcp.tools import read_tools

    client, TS = _make_client()
    try:
        monkeypatch.setattr(read_tools, "SessionLocal", TS)
        _, hdr_web = _setup(client, "stk-mcp@t.com")
        assert _buy(client, hdr_web, shares=100, price=100, fee=0,
                    trade_date="2026-01-01T02:00:00+00:00").status_code == 200
        assert _buy(client, hdr_web, trade_type="sell", shares=40, price=150, fee=0,
                    trade_date="2026-03-01T02:00:00+00:00").status_code == 200
        with TS() as db:
            user = db.scalar(select(User).where(User.email == "stk-mcp@t.com"))
            sec_id = quotes.store.ensure_security(db, market="TW", symbol="2330", name="台積電", currency="TWD")
            db.add(SecurityQuote(security_id=sec_id, price=200.0, session="close", source="test"))
            db.commit()
            db.refresh(user)
            db.expunge(user)
        [h] = read_tools.list_stock_holdings(user)
        assert h["symbol"] == "2330" and h["shares"] == 60 and h["market_value"] == 12000
        assert h["unrealized_pnl"] == pytest.approx(12000 - 6000)
        rep = read_tools.get_stock_realized_pnl(user, year=2026, symbol="TW:2330")
        assert rep["realized_pnl_by_currency"] == {"TWD": pytest.approx(2000)}
        assert read_tools.list_stock_holdings(user, account_name="不存在") == []
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 報價 + 持股彙總                                                              #
# --------------------------------------------------------------------------- #


def _fake_quote(price, *, market="TW", symbol="2330", currency="TWD", prev=None):
    async def _fetch(m, s, client=None):
        assert (m, s) == (market, symbol)
        return provider_base.QuoteData(
            market=market, symbol=symbol, price=price, prev_close=prev,
            quote_time=datetime(2026, 9, 24, 5, 30, tzinfo=timezone.utc),
            currency=currency, name="台積電", source="yahoo",
        )

    return _fetch


def test_holdings_endpoint_market_value_and_base_conversion(monkeypatch):
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-h1@t.com")
        _buy(client, hdr_web)
        monkeypatch.setattr(yahoo, "fetch_quote", _fake_quote(700.0, prev=690.0))
        r = client.get("/api/v1/read/workspace/holdings", headers=hdr_web)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["base_currency"] == "TWD"
        [acc] = body["accounts"]
        assert acc["account_id"] == "acc_inv" and acc["include_in_total"] is False
        [h] = acc["holdings"]
        assert h["shares"] == 1000
        assert h["market_value"] == pytest.approx(700000)
        # 預設未實現損益扣預估賣出費用:手續費 floor(700000×0.1425%)=997、
        # 普通股證交稅 0.3% = 2100(trade_fees.estimate_sell)。
        assert h["est_sell_fee"] == 997 and h["est_sell_tax"] == 2100
        assert h["net_value"] == pytest.approx(700000 - 997 - 2100)
        assert h["pnl_after_sell_costs"] is True
        assert h["unrealized_pnl"] == pytest.approx(700000 - 997 - 2100 - 600855)
        assert body["total_net_value"] == pytest.approx(700000 - 997 - 2100)
        assert body["total_unrealized_pnl"] == pytest.approx(700000 - 997 - 2100 - 600855)
        assert h["quote"]["change"] == pytest.approx(10)
        assert body["total_market_value"] == pytest.approx(700000)
        assert body["missing_rates"] == []

        r = client.get("/api/v1/read/securities/quotes", headers=hdr_web,
                       params={"symbols": "TW:2330", "refresh": "false"})
        assert r.status_code == 200, r.text
        assert r.json()[0]["price"] == 700.0
    finally:
        app.dependency_overrides.clear()


def test_holdings_endpoint_missing_rate_is_excluded_not_added_as_one(monkeypatch):
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-h2@t.com", inv_currency="USD", bank_currency="USD")
        with TS() as db:
            user_id = db.scalar(select(User.id).where(User.email == "stk-h2@t.com"))
            db.add(UserProfile(user_id=user_id, primary_currency="TWD"))
            db.commit()
        _buy(client, hdr_web, market="US", symbol="AAPL", shares=10, price=200, fee=0, currency="USD")
        monkeypatch.setattr(yahoo, "fetch_quote", _fake_quote(250.0, market="US", symbol="AAPL", currency="USD"))

        async def _no_rates(db, base):
            raise RuntimeError("offline")

        from src.services.exchange_rate import fetcher
        monkeypatch.setattr(fetcher, "get_rates", _no_rates)
        r = client.get("/api/v1/read/workspace/holdings", headers=hdr_web)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["base_currency"] == "TWD"
        assert body["missing_rates"] == ["USD"]
        assert body["total_market_value"] == 0
        assert body["accounts"][0]["market_value_by_currency"] == {"USD": 2500.0}
    finally:
        app.dependency_overrides.clear()


def test_quotes_refresh_failure_serves_stale(monkeypatch):
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-h3@t.com")
        with TS() as db:
            db.add(Security(market="TW", symbol="2330", name="台積電", currency="TWD"))
            db.flush()
            sid = db.scalar(select(Security.id).where(Security.symbol == "2330"))
            db.add(SecurityQuote(security_id=sid, price=500.0, session="close", source="twse",
                                 fetched_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
            db.commit()

        async def _boom(m, s, client=None):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(yahoo, "fetch_quote", _boom)
        r = client.get("/api/v1/read/securities/quotes", headers=hdr_web, params={"symbols": "TW:2330"})
        assert r.status_code == 200, r.text
        [q] = r.json()
        assert q["price"] == 500.0 and q["stale"] is True
    finally:
        app.dependency_overrides.clear()


def test_quotes_rejects_bad_symbol_keys():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-h4@t.com")
        assert client.get("/api/v1/read/securities/quotes", headers=hdr_web,
                          params={"symbols": "2330"}).status_code == 422
        assert client.get("/api/v1/read/securities/quotes", headers=hdr_web,
                          params={"symbols": "XX:2330"}).status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_search_uses_local_tw_master_and_yahoo(monkeypatch):
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-s1@t.com")
        tw_rows = json.loads((FIXTURES / "twse_stock_day_all.json").read_text())
        tpex_rows = json.loads((FIXTURES / "tpex_daily_close.json").read_text())

        async def _twse(client=None):
            return twse.parse_twse(tw_rows)

        async def _tpex(client=None):
            return twse.parse_tpex(tpex_rows)

        async def _ysearch(q, client=None):
            return yahoo.parse_search(json.loads((FIXTURES / "yahoo_search_apple.json").read_text()))

        monkeypatch.setattr(twse, "fetch_twse", _twse)
        monkeypatch.setattr(twse, "fetch_tpex", _tpex)
        monkeypatch.setattr(yahoo, "search", _ysearch)

        r = client.get("/api/v1/read/securities/search", headers=hdr_web, params={"q": "台積", "market": "TW"})
        assert r.status_code == 200, r.text
        assert [(x["market"], x["symbol"]) for x in r.json()] == [("TW", "2330")]

        r = client.get("/api/v1/read/securities/search", headers=hdr_web, params={"q": "apple"})
        assert r.status_code == 200, r.text
        assert ("US", "AAPL") in [(x["market"], x["symbol"]) for x in r.json()]
        # 台股清單同步時順便寫入收盤報價
        with TS() as db:
            assert db.scalar(select(SecurityQuote.price).join(Security).where(Security.symbol == "2330")) == 2475.0
    finally:
        app.dependency_overrides.clear()


def test_search_refreshes_master_when_only_sparse_yahoo_rows_exist(monkeypatch):
    """2026-09-28 實機踩到:查報價時先用 Yahoo 建了 TW:0050(英文名稱),
    `updated_at` 很新,舊的新鮮度判斷就以為台股清單同步過了,搜尋永遠拿到
    英文名稱。現在要有足量(官方清單規模)的台股資料才算同步過。"""
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-s2@t.com")
        with TS() as db:
            db.add(Security(market="TW", symbol="0050", name="Yuanta/P-shares Taiwan Top 50 ETF", currency="TWD"))
            db.commit()
        calls = {"twse": 0}
        tw_rows = json.loads((FIXTURES / "twse_stock_day_all.json").read_text())

        async def _twse(client=None):
            calls["twse"] += 1
            return twse.parse_twse(tw_rows)

        async def _tpex(client=None):
            return twse.parse_tpex([])

        monkeypatch.setattr(twse, "fetch_twse", _twse)
        monkeypatch.setattr(twse, "fetch_tpex", _tpex)
        r = client.get("/api/v1/read/securities/search", headers=hdr_web, params={"q": "0050", "market": "TW"})
        assert r.status_code == 200, r.text
        assert calls["twse"] == 1
        assert r.json()[0]["name"] == "元大台灣50"
    finally:
        app.dependency_overrides.clear()


def test_refresh_close_quotes_only_after_close_and_only_held(monkeypatch):
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-j1@t.com")
        _buy(client, hdr_web)
        calls = {"twse": 0}
        tw_rows = json.loads((FIXTURES / "twse_stock_day_all.json").read_text())

        async def _twse(client=None):
            calls["twse"] += 1
            return twse.parse_twse(tw_rows)

        async def _yahoo_down(m, s, client=None):
            raise RuntimeError("yahoo down")

        monkeypatch.setattr(twse, "fetch_twse", _twse)
        monkeypatch.setattr(yahoo, "fetch_quote", _yahoo_down)  # Yahoo 失敗 → 官方備援
        before_close = datetime(2026, 9, 24, 5, 0, tzinfo=timezone.utc)  # 台北 13:00
        after_close = datetime(2026, 9, 24, 7, 30, tzinfo=timezone.utc)  # 台北 15:30
        with TS() as db:
            assert quotes.refresh_close_quotes(db, now=before_close) == {"markets": 0, "quotes": 0}
            assert calls["twse"] == 0
            result = quotes.refresh_close_quotes(db, now=after_close)
            assert result["markets"] == 1 and calls["twse"] == 1
            price = db.scalar(select(SecurityQuote.price).join(Security).where(Security.symbol == "2330"))
            assert price == 2475.0
            # 同一天再跑:資料日期就是今天 → 已完成,不再打上游
            assert quotes.refresh_close_quotes(db, now=after_close)["markets"] == 0
            assert calls["twse"] == 1
    finally:
        app.dependency_overrides.clear()


def test_refresh_close_quotes_prefers_yahoo_and_ignores_stale_official(monkeypatch):
    """2026-10-02:證交所晚更新(還是前一交易日)時,收盤價要以 Yahoo 為準,
    且官方舊資料不得蓋掉較新的快取。"""
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "stk-j2@t.com")
        _buy(client, hdr_web)
        calls = {"twse": 0}
        tw_rows = json.loads((FIXTURES / "twse_stock_day_all.json").read_text())

        async def _twse(client=None):
            calls["twse"] += 1
            return twse.parse_twse(tw_rows)

        monkeypatch.setattr(twse, "fetch_twse", _twse)
        monkeypatch.setattr(yahoo, "fetch_quote", _fake_quote(2500.0, prev=2475.0))
        after_close = datetime(2026, 9, 24, 7, 30, tzinfo=timezone.utc)
        with TS() as db:
            quotes.refresh_close_quotes(db, now=after_close)
            row = db.execute(select(SecurityQuote).join(Security).where(Security.symbol == "2330")).scalar_one()
            assert row.price == 2500.0 and row.source == "yahoo" and calls["twse"] == 0

        # Yahoo 之後失敗,官方資料日期較舊 → 不覆蓋
        async def _yahoo_down(m, s, client=None):
            raise RuntimeError("yahoo down")

        monkeypatch.setattr(yahoo, "fetch_quote", _yahoo_down)
        with TS() as db:
            old = db.execute(select(SecurityQuote).join(Security).where(Security.symbol == "2330")).scalar_one()
            old.session = "intraday"  # 讓它被視為尚未完成
            db.commit()
            for r in tw_rows:
                r["Date"] = "1150923"  # 比 Yahoo 的 9/24 舊一天
            quotes.refresh_close_quotes(db, now=after_close)
            row = db.execute(select(SecurityQuote).join(Security).where(Security.symbol == "2330")).scalar_one()
            assert calls["twse"] == 1 and row.price == 2500.0 and row.source == "yahoo"
    finally:
        app.dependency_overrides.clear()


def test_scheduled_job_registered():
    from src.services import scheduled_jobs

    assert "security_quote_close" in scheduled_jobs.JOB_REGISTRY
    assert "security_quote_close" in scheduled_jobs._DEFAULT_JOB_CONFIGS
