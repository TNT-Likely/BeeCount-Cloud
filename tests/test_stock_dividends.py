"""股票持股 Phase 2 — 股利(docs/STOCK_HOLDINGS_SD.md §7):

- provider 解析:證交所/櫃買除權除息預告表、Yahoo chart dividends(錄好的回應)
- 實收估算(二代健保門檻、匯費、預扣稅、配股捨去)
- 事件同步:官方來源優先於 Yahoo
- 偵測:依「除息日前一天」的持股(交易日換成市場當地日期)建待確認股利 +
  通知;重跑冪等;持股變動會更新估算;明細刪掉會退回待確認
- 確認:現金股利(income 入交割帳戶,分類「股利」)/ 再投入(income 入投資
  帳戶)/ 配股;忽略、復原;跨幣別要 settlement_amount + 補折算欄位
- 手動記股利(stock-trades 端點 cash_dividend)

============================================================================
手动检查清单(pytest 测不到的运行时行为):

1. 真的連到證交所/櫃買/Yahoo:`/admin/scheduled-jobs` 手動跑
   `security_dividend_sync`,`last_run_message` 的 events 應 > 0(有交易過的
   標的近半年有配息時)。
2. 除息日當天跑 `security_dividend_detector`,App/Web 通知中心應出現
   「股利待確認」。
============================================================================
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from src.main import app
from src.models import (
    Notification,
    PendingDividend,
    ReadStockTradeProjection,
    ReadTxProjection,
    Security,
    SecurityDividendEvent,
    UserCategoryProjection,
)
from src.services.securities import dividends
from src.services.securities.providers import twse, yahoo
from src.services.securities.providers.base import DividendData

from tests.test_stock_holdings import FIXTURES, _buy, _login, _make_client, _push, _setup, _trade_rows, _tx_row

EX_DATE = date(2026, 9, 16)
# 台北 2026-09-16 10:00
ON_EX_DATE = datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc)


def _event(**overrides) -> DividendData:
    data = dict(
        market="TW", symbol="2330", ex_date=EX_DATE, cash_per_share=7.0, stock_per_share=0.0,
        currency="TWD", source="twse", name="台積電",
    )
    data.update(overrides)
    return DividendData(**data)


def _sync_and_detect(TS, events, *, now=ON_EX_DATE):
    with TS() as db:
        dividends.sync_dividend_events(db, now=now, events=events)
        return dividends.detect_pending_dividends(db, now=now)


def _pending_rows(TS):
    with TS() as db:
        rows = db.scalars(select(PendingDividend).order_by(PendingDividend.id)).all()
        for r in rows:
            db.expunge(r)
        return rows


def _confirm(client, hdr, pending_id, **body):
    return client.post(
        f"/api/v1/write/securities/pending-dividends/{pending_id}/confirm",
        headers=hdr, json={"base_change_id": 0, **body},
    )


# --------------------------------------------------------------------------- #
# provider 解析                                                                #
# --------------------------------------------------------------------------- #


def test_parse_twse_dividend_forecast():
    rows = json.loads((FIXTURES / "twse_twt48u.json").read_text())
    events = {e.symbol: e for e in twse.parse_twse_dividends(rows)}
    assert "03001P" not in events  # 權證排除
    assert events["2542"].ex_date == date(2026, 9, 23)
    assert events["2542"].cash_per_share == pytest.approx(4.0)
    assert events["1235"].stock_per_share == pytest.approx(0.04999999)
    assert events["00400A"].cash_per_share == 0.0  # 還沒公告金額
    assert all(e.market == "TW" and e.currency == "TWD" and e.source == "twse" for e in events.values())


def test_parse_tpex_dividend_forecast():
    rows = json.loads((FIXTURES / "tpex_exright_prepost.json").read_text())
    events = {e.symbol: e for e in twse.parse_tpex_dividends(rows)}
    assert events["3675"].market == "TWO"
    assert events["3675"].cash_per_share == pytest.approx(4.0)
    assert events["3675"].ex_date == date(2026, 9, 15)


def test_parse_yahoo_dividends_uses_exchange_local_date_and_rounds():
    aapl = yahoo.parse_dividends(
        json.loads((FIXTURES / "yahoo_chart_aapl_dividends.json").read_text()), market="US", symbol="AAPL",
    )
    assert aapl[-1].ex_date == date(2026, 8, 10)  # 13:30Z = 紐約 09:30
    assert aapl[-1].cash_per_share == pytest.approx(0.27)
    assert aapl[-1].currency == "USD" and aapl[-1].source == "yahoo"
    tsmc = yahoo.parse_dividends(
        json.loads((FIXTURES / "yahoo_chart_2330_dividends.json").read_text()), market="TW", symbol="2330",
    )
    # 01:00Z = 台北 09:00 → 當地日期 9/16(直接取 UTC 日期也剛好是 16,但紐約會是 15)
    assert tsmc[-1].ex_date == date(2026, 9, 16)
    assert tsmc[-1].cash_per_share == 7.0  # 7.000001 → 4 位小數
    assert tsmc[0].cash_per_share == 5.0


def test_yahoo_dividend_request_includes_events():
    assert yahoo.DIVIDEND_PARAMS["events"] == "div"


# --------------------------------------------------------------------------- #
# 實收估算                                                                     #
# --------------------------------------------------------------------------- #


def test_estimate_tw_under_nhi_threshold_only_remittance_fee():
    est = dividends.estimate_dividend(
        market="TW", currency="TWD", shares=1000, cash_per_share=7, stock_per_share=0, settings={},
    )
    assert (est.gross, est.fee, est.tax, est.net) == (7000, 10, 0, 6990)


def test_estimate_tw_over_nhi_threshold_floors_supplement():
    est = dividends.estimate_dividend(
        market="TW", currency="TWD", shares=5000, cash_per_share=5, stock_per_share=0, settings={},
    )
    assert est.gross == 25000
    assert est.tax == 527  # 25000 × 2.11% = 527.5 → 捨去
    assert est.net == 25000 - 10 - 527


def test_estimate_user_settings_override_defaults():
    est = dividends.estimate_dividend(
        market="TW", currency="TWD", shares=5000, cash_per_share=5, stock_per_share=0,
        settings={"dividendFeeFixed": 0, "nhiSupplementRate": 0},
    )
    assert (est.fee, est.tax, est.net) == (0, 0, 25000)


def test_estimate_us_withholding_and_fee_rate():
    est = dividends.estimate_dividend(
        market="US", currency="USD", shares=10, cash_per_share=0.27, stock_per_share=0,
        settings={"dividendFeeRate": 0.1},
    )
    assert est.gross == pytest.approx(2.7)
    assert est.tax == pytest.approx(0.81)
    assert est.fee == pytest.approx(0.27)
    assert est.net == pytest.approx(1.62)


def test_estimate_stock_dividend_rounds_truncated_ratio():
    est = dividends.estimate_dividend(
        market="TW", currency="TWD", shares=1000, cash_per_share=0, stock_per_share=0.04999999, settings={},
    )
    assert est.stock_shares == 50
    assert (est.gross, est.fee, est.net) == (0, 0, 0)
    us = dividends.estimate_dividend(
        market="US", currency="USD", shares=3, cash_per_share=0, stock_per_share=0.1, settings={},
    )
    assert us.stock_shares == pytest.approx(0.3)


# --------------------------------------------------------------------------- #
# 事件同步                                                                     #
# --------------------------------------------------------------------------- #


def test_sync_events_only_for_traded_symbols_and_official_wins():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-s1@t.com")
        _buy(client, hdr_web)
        with TS() as db:
            assert dividends.sync_dividend_events(db, now=ON_EX_DATE, events=[])["symbols"] == 1
            dividends.upsert_events(db, [_event(cash_per_share=0.0)], names={("TW", "2330"): "台積電"}, now=ON_EX_DATE)
            dividends.upsert_events(db, [_event(cash_per_share=7.0)], names={}, now=ON_EX_DATE)  # 官方補金額
            dividends.upsert_events(db, [_event(cash_per_share=7.000011, source="yahoo")], names={}, now=ON_EX_DATE)
            db.commit()
            [row] = db.scalars(select(SecurityDividendEvent)).all()
            assert row.cash_per_share == 7.0 and row.source == "twse"
            # Yahoo 先寫、官方後到 → 官方覆蓋
            dividends.upsert_events(db, [_event(ex_date=date(2026, 3, 17), cash_per_share=6.000036, source="yahoo")],
                                    names={}, now=ON_EX_DATE)
            dividends.upsert_events(db, [_event(ex_date=date(2026, 3, 17), cash_per_share=6.0)], names={}, now=ON_EX_DATE)
            db.commit()
            march = db.scalar(select(SecurityDividendEvent).where(SecurityDividendEvent.ex_date == date(2026, 3, 17)))
            assert march.source == "twse" and march.cash_per_share == 6.0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 偵測                                                                         #
# --------------------------------------------------------------------------- #


def test_detect_creates_pending_with_record_date_shares_and_notifies():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-d1@t.com")
        _buy(client, hdr_web)  # 9/1 1000 股
        # 除息日當天(台北 9/16 00:00)買的不算——UTC 是 9/15 16:00,直接比 UTC 日期會誤算
        _buy(client, hdr_web, shares=500, trade_date="2026-09-15T16:00:00+00:00")
        result = _sync_and_detect(TS, [_event()])
        assert result["created"] == 1
        [pending] = _pending_rows(TS)
        assert pending.shares == 1000
        assert pending.status == "pending"
        assert (pending.est_gross, pending.est_fee, pending.est_net) == (7000, 10, 6990)
        with TS() as db:
            [note] = db.scalars(select(Notification).where(Notification.category == "dividend")).all()
            assert "2330" in note.title
            assert note.payload_json["pendingDividendId"] == pending.id
            assert note.payload_json["ledgerId"] == "lg1"
        # 重跑:不重建、不重發通知
        again = _sync_and_detect(TS, [_event()])
        assert again["created"] == 0
        with TS() as db:
            assert len(db.scalars(select(Notification).where(Notification.category == "dividend")).all()) == 1
    finally:
        app.dependency_overrides.clear()


def test_detect_skips_future_unannounced_and_too_old_events():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-d2@t.com")
        _buy(client, hdr_web, trade_date="2025-01-01T02:00:00+00:00")
        result = _sync_and_detect(TS, [
            _event(ex_date=date(2026, 9, 30)),  # 未來
            _event(ex_date=date(2026, 9, 10), cash_per_share=0.0),  # 還沒公告金額
            _event(ex_date=date(2026, 6, 1)),  # 超過回溯範圍
        ])
        assert result["created"] == 0
        assert _pending_rows(TS) == []
    finally:
        app.dependency_overrides.clear()


def test_detect_updates_estimate_when_backfilled_trade_changes_shares():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-d3@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        _buy(client, hdr_web, shares=2000, trade_date="2026-09-10T02:00:00+00:00")
        result = _sync_and_detect(TS, [_event()])
        assert result["updated"] == 1
        [pending] = _pending_rows(TS)
        assert pending.shares == 3000
        assert pending.est_gross == 21000
        assert pending.est_tax == 443  # 21000 × 2.11% = 443.1
    finally:
        app.dependency_overrides.clear()


def test_detect_removes_pending_when_position_disappears():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-d4@t.com")
        trade_id = _buy(client, hdr_web).json()["entity_id"]
        _sync_and_detect(TS, [_event()])
        assert len(_pending_rows(TS)) == 1
        r = client.request(
            "DELETE", f"/api/v1/write/ledgers/lg1/stock-trades/{trade_id}", headers=hdr_web,
            json={"base_change_id": 0},
        )
        assert r.status_code == 200, r.text
        # 最後一筆明細也刪了 → 這檔已經沒有任何明細,事件層級直接跳過;
        # 另外補一筆除息後的買進,確認「持股 0」會移除 pending。
        _buy(client, hdr_web, trade_date="2026-09-20T02:00:00+00:00")
        result = _sync_and_detect(TS, [_event()], now=datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc))
        assert result["removed"] == 1
        assert _pending_rows(TS) == []
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 確認 / 忽略                                                                  #
# --------------------------------------------------------------------------- #


def test_list_pending_dividends_endpoint():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-l1@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        r = client.get("/api/v1/read/securities/pending-dividends", headers=hdr_web)
        assert r.status_code == 200, r.text
        [item] = r.json()
        assert item["symbol"] == "2330" and item["account_id"] == "acc_inv"
        assert item["account_name"] == "證券" and item["ledger_id"] == "lg1"
        assert item["ex_date"] == "2026-09-16" and item["cash_per_share"] == 7.0
        assert item["est_net"] == 6990 and item["status"] == "pending"
        assert item["event_ref"] == "TW:2330:2026-09-16"
        # 別的使用者看不到
        other = {"Authorization": f"Bearer {_login(client, 'div-l2@t.com', device_id='d-other', client_type='web')}",
                 "X-Device-ID": "d-other"}
        assert client.get("/api/v1/read/securities/pending-dividends", headers=other).json() == []
        assert _confirm(client, other, item["id"], settlement_account_id="acc_bank").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_confirm_cash_dividend_creates_income_tx_and_trade():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c1@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        [pending] = _pending_rows(TS)
        r = _confirm(client, hdr_web, pending.id, settlement_account_id="acc_bank", fee=15)
        assert r.status_code == 200, r.text
        trades = [t for t in _trade_rows(TS) if t.trade_type == "cash_dividend"]
        [trade] = trades
        assert trade.shares == 1000 and trade.price == 7.0
        assert trade.fee == 15 and trade.tax == 0 and trade.amount == 6985
        assert trade.dividend_event_ref == "TW:2330:2026-09-16"
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.tx_type == "income" and tx.account_sync_id == "acc_bank"
        assert tx.amount == 6985
        assert tx.category_name == "股利"
        with TS() as db:
            cat = db.scalar(select(UserCategoryProjection).where(UserCategoryProjection.sync_id == tx.category_sync_id))
            assert cat.name == "股利" and cat.kind == "income"
        [pending] = _pending_rows(TS)
        assert pending.status == "confirmed"
        assert json.loads(pending.created_trade_ids) == [trade.sync_id]
        # 重複確認 → 409
        assert _confirm(client, hdr_web, pending.id, settlement_account_id="acc_bank").status_code == 409
        # 持股詳情把股利算進累計股利
        holdings = client.get("/api/v1/read/workspace/holdings?refresh=false", headers=hdr_web).json()
        [h] = holdings["accounts"][0]["holdings"]
        assert h["dividends"] == 6985 and h["shares"] == 1000
    finally:
        app.dependency_overrides.clear()


def test_confirm_uses_settlement_account_from_settings():
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "div-c2@t.com")
        _push(client, hdr_app, "lg1", "account", "acc_inv",
              {"syncId": "acc_inv", "investmentSettings": {"settlementAccountId": "acc_bank"}})
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        [pending] = _pending_rows(TS)
        r = _confirm(client, hdr_web, pending.id)
        assert r.status_code == 200, r.text
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "cash_dividend"]
        assert _tx_row(TS, trade.tx_sync_id).account_sync_id == "acc_bank"
    finally:
        app.dependency_overrides.clear()


def test_confirm_defaults_to_last_trade_settlement_account():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c3@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        [item] = client.get("/api/v1/read/securities/pending-dividends", headers=hdr_web).json()
        assert item["settlement_account_id"] == "acc_bank"  # 最近一筆買進的交割帳戶
        r = _confirm(client, hdr_web, item["id"])
        assert r.status_code == 200, r.text
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "cash_dividend"]
        assert _tx_row(TS, trade.tx_sync_id).account_sync_id == "acc_bank"
    finally:
        app.dependency_overrides.clear()


def test_confirm_without_any_receiving_account_is_rejected():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c9@t.com")
        # 只有期初持股(沒有買賣 → 推不出交割帳戶)
        _buy(client, hdr_web, trade_type="opening", fee=0, settlement_account_id=None)
        _sync_and_detect(TS, [_event()])
        [pending] = _pending_rows(TS)
        assert _confirm(client, hdr_web, pending.id).status_code == 400
        assert _pending_rows(TS)[0].status == "pending"
    finally:
        app.dependency_overrides.clear()


def test_confirm_reinvest_income_goes_into_investment_account():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c4@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        [pending] = _pending_rows(TS)
        assert _confirm(client, hdr_web, pending.id, mode="reinvest").status_code == 400  # 缺股數/價格
        r = _confirm(client, hdr_web, pending.id, mode="reinvest", reinvest_shares=11, reinvest_price=630,
                     reinvest_fee=1)
        assert r.status_code == 200, r.text
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "reinvest"]
        assert trade.shares == 11 and trade.amount == 6931
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.tx_type == "income" and tx.account_sync_id == "acc_inv" and tx.amount == 6931
        holdings = client.get("/api/v1/read/workspace/holdings?refresh=false", headers=hdr_web).json()
        [h] = holdings["accounts"][0]["holdings"]
        assert h["shares"] == 1011
        assert h["total_cost"] == pytest.approx(600855 + 6931)
    finally:
        app.dependency_overrides.clear()


def test_confirm_stock_dividend_records_shares_without_tx():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c5@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event(cash_per_share=0.5, stock_per_share=0.04999999)])
        [pending] = _pending_rows(TS)
        assert pending.est_stock_shares == 50
        r = _confirm(client, hdr_web, pending.id, settlement_account_id="acc_bank")
        assert r.status_code == 200, r.text
        by_type = {t.trade_type: t for t in _trade_rows(TS)}
        assert by_type["stock_dividend"].shares == 50 and by_type["stock_dividend"].tx_sync_id is None
        assert by_type["cash_dividend"].amount == 490  # 500 − 匯費 10
        assert len(json.loads(_pending_rows(TS)[0].created_trade_ids)) == 2
    finally:
        app.dependency_overrides.clear()


def test_dismiss_and_restore():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c6@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        [pending] = _pending_rows(TS)
        base = f"/api/v1/write/securities/pending-dividends/{pending.id}"
        assert client.post(f"{base}/dismiss", headers=hdr_web).json()["status"] == "dismissed"
        assert client.post(f"{base}/dismiss", headers=hdr_web).status_code == 409
        # 忽略後排程不會改回來
        _sync_and_detect(TS, [_event()])
        assert _pending_rows(TS)[0].status == "dismissed"
        assert client.get("/api/v1/read/securities/pending-dividends", headers=hdr_web).json() == []
        assert client.post(f"{base}/restore", headers=hdr_web).json()["status"] == "pending"
    finally:
        app.dependency_overrides.clear()


def test_deleting_confirmed_dividend_trade_reopens_pending():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-c7@t.com")
        _buy(client, hdr_web)
        _sync_and_detect(TS, [_event()])
        [pending] = _pending_rows(TS)
        _confirm(client, hdr_web, pending.id, settlement_account_id="acc_bank")
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "cash_dividend"]
        r = client.request(
            "DELETE", f"/api/v1/write/ledgers/lg1/stock-trades/{trade.sync_id}", headers=hdr_web,
            json={"base_change_id": 0},
        )
        assert r.status_code == 200, r.text
        assert _tx_row(TS, trade.tx_sync_id) is None
        result = _sync_and_detect(TS, [_event()])
        assert result["reopened"] == 1
        assert _pending_rows(TS)[0].status == "pending"
        with TS() as db:  # 不重發通知
            assert len(db.scalars(select(Notification).where(Notification.category == "dividend")).all()) == 1
    finally:
        app.dependency_overrides.clear()


def test_confirm_cross_currency_requires_settlement_amount_and_sets_fx_fields(monkeypatch):
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "div-c8@t.com", inv_currency="USD")
        _push(client, hdr_app, "lg1", "account", "acc_usd",
              {"syncId": "acc_usd", "name": "外幣戶", "type": "bank_card", "currency": "USD"})
        # 手動匯率:1 USD = 32 TWD(override 優先,不打外部匯率 API)
        _push(client, hdr_app, "lg1", "exchange_rate_override", "USD",
              {"syncId": "USD", "baseCurrency": "TWD", "quoteCurrency": "USD", "rate": 32})
        r = _buy(client, hdr_web, market="US", symbol="AAPL", security_name="Apple", shares=10, price=200,
                 fee=5, currency="USD", settlement_account_id="acc_usd")
        assert r.status_code == 200, r.text
        _sync_and_detect(TS, [_event(market="US", symbol="AAPL", cash_per_share=0.27, currency="USD",
                                     source="yahoo", ex_date=date(2026, 9, 15))],
                         now=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc))
        [pending] = _pending_rows(TS)
        assert pending.est_net == pytest.approx(1.89)  # 2.7 − 30% 預扣
        # 美元股利入台幣交割戶 → 要給台幣實收
        assert _confirm(client, hdr_web, pending.id, settlement_account_id="acc_bank").status_code == 400
        r = _confirm(client, hdr_web, pending.id, settlement_account_id="acc_usd")
        assert r.status_code == 200, r.text
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "cash_dividend"]
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.amount == pytest.approx(1.89)
        assert tx.currency_code == "USD"
        assert tx.native_amount == pytest.approx(1.89 * 32)
    finally:
        app.dependency_overrides.clear()


def test_fractional_reinvest_income_is_rounded_to_cents():
    client, TS = _make_client()
    try:
        hdr_app, hdr_web = _setup(client, "div-c10@t.com", inv_currency="USD", bank_currency="USD")
        # 手動匯率,避免測試打到外部匯率 API
        _push(client, hdr_app, "lg1", "exchange_rate_override", "USD",
              {"syncId": "USD", "baseCurrency": "TWD", "quoteCurrency": "USD", "rate": 32})
        _buy(client, hdr_web, market="US", symbol="AAPL", shares=10, price=200, fee=5, currency="USD")
        r = _buy(client, hdr_web, market="US", symbol="AAPL", trade_type="reinvest", shares=0.0055,
                 price=341.07, fee=0, currency="USD", settlement_account_id=None)
        assert r.status_code == 200, r.text
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "reinvest"]
        assert trade.amount == pytest.approx(1.88)  # 成交價金依幣別取整(美元到分,trade_fees.stock_gross)
        assert _tx_row(TS, trade.tx_sync_id).amount == 1.88  # 收入金額到分
    finally:
        app.dependency_overrides.clear()


def test_manual_cash_dividend_via_stock_trade_endpoint():
    client, TS = _make_client()
    try:
        _, hdr_web = _setup(client, "div-m1@t.com")
        _buy(client, hdr_web)
        r = _buy(client, hdr_web, trade_type="cash_dividend", shares=1000, price=3.5, fee=10, tax=0,
                 trade_date="2026-07-20T04:00:00+00:00")
        assert r.status_code == 200, r.text
        [trade] = [t for t in _trade_rows(TS) if t.trade_type == "cash_dividend"]
        assert trade.amount == 3490
        tx = _tx_row(TS, trade.tx_sync_id)
        assert tx.tx_type == "income" and tx.amount == 3490 and tx.category_name == "股利"
        # 編輯金額 → 交易跟著改
        r = client.patch(f"/api/v1/write/ledgers/lg1/stock-trades/{trade.sync_id}", headers=hdr_web,
                         json={"base_change_id": 0, "price": 4})
        assert r.status_code == 200, r.text
        assert _tx_row(TS, trade.tx_sync_id).amount == 3990
    finally:
        app.dependency_overrides.clear()


def test_dividend_jobs_registered():
    from src.services import scheduled_jobs

    for key in ("security_dividend_sync", "security_dividend_detector"):
        assert key in scheduled_jobs.JOB_REGISTRY
        assert key in scheduled_jobs._DEFAULT_JOB_CONFIGS
