"""首頁「投資淨投入」`GET /read/workspace/investment-flow`:

- 買進/賣出/手續費/交易稅/股利依幣別分開加總
- 期間口徑同 analytics(含 tz_offset_minutes 切月)
- 期初持股不算現金流;買賣是轉帳,不改變收入/支出統計
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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


def _login(client, email, device_id, client_type):
    client.post("/api/v1/auth/register", json={"email": email, "password": "Pa$$word1!"})
    r = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Pa$$word1!", "device_id": device_id,
              "client_type": client_type, "device_name": "pytest", "platform": "test"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _push(client, hdr, entity_type, sync_id, payload):
    body = {
        "ledger_id": "lg1", "entity_type": entity_type, "entity_sync_id": sync_id,
        "action": "upsert", "updated_at": datetime.now(timezone.utc).isoformat(), "payload": payload,
    }
    r = client.post("/api/v1/sync/push", headers=hdr, json={"device_id": "d-app", "changes": [body]})
    assert r.status_code == 200, r.text


def _setup(client, email):
    app_tok = _login(client, email, "d-app", "app")
    web_tok = _login(client, email, "d-web", "web")
    hdr_app = {"Authorization": f"Bearer {app_tok}"}
    hdr_web = {"Authorization": f"Bearer {web_tok}", "X-Device-ID": "d-web"}
    _push(client, hdr_app, "ledger", "lg1", {"syncId": "lg1", "ledgerName": "帳本", "currency": "TWD"})
    _push(client, hdr_app, "account", "acc_bank", {"syncId": "acc_bank", "name": "交割戶", "type": "bank_card", "currency": "TWD"})
    _push(client, hdr_app, "account", "acc_inv", {"syncId": "acc_inv", "name": "證券", "type": "investment", "currency": "TWD", "includeInTotal": False})
    return hdr_web


def _trade(client, hdr, trade_type, date, **kw):
    body = {
        "base_change_id": 0, "account_id": "acc_inv", "trade_type": trade_type, "market": "TW",
        "symbol": "2330", "security_name": "台積電", "shares": 1000, "price": 600, "fee": 0, "tax": 0,
        "trade_date": date, "settlement_account_id": "acc_bank",
    }
    body.update(kw)
    r = client.post("/api/v1/write/ledgers/lg1/stock-trades", headers=hdr, json=body)
    assert r.status_code in (200, 201), r.text


def _flow(client, hdr, **params):
    r = client.get("/api/v1/read/workspace/investment-flow", headers=hdr, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_investment_flow_month_aggregation_and_timezone():
    client, _ = _make_client()
    hdr = _setup(client, "flow@x.com")
    # 9 月買進:1000×600 + 手續費 855 = 600,855
    _trade(client, hdr, "buy", "2026-09-01T02:00:00+00:00", fee=855)
    # 9 月賣出:500×700 − 手續費 500 − 稅 1,050 = 348,450
    _trade(client, hdr, "sell", "2026-09-10T02:00:00+00:00", shares=500, price=700, fee=500, tax=1050)
    # 現金股利:1000 股 × 2 = 2,000
    _trade(client, hdr, "cash_dividend", "2026-09-12T02:00:00+00:00", price=2)
    # 8 月買進(不在 9 月)
    _trade(client, hdr, "buy", "2026-08-15T02:00:00+00:00", shares=100, price=500, fee=100)
    # 期初持股不算現金流
    _trade(client, hdr, "opening", "2026-09-02T02:00:00+00:00", shares=10, price=100, settlement_account_id=None)
    # 台北時間 9/1 04:00 = UTC 8/31 20:00:UTC 切月歸 8 月、+480 切月歸 9 月
    _trade(client, hdr, "buy", "2026-08-31T20:00:00+00:00", shares=10, price=100)

    data = _flow(client, hdr, scope="month", period="2026-09", tz_offset_minutes=480)
    assert data["period"] == "2026-09"
    [row] = data["by_currency"]
    assert row["currency"] == "TWD"
    assert row["buy_amount"] == 600855 + 1000
    assert row["buy_count"] == 2
    assert row["sell_amount"] == 348450
    assert row["sell_count"] == 1
    assert row["net_invested"] == 600855 + 1000 - 348450
    assert row["fees"] == 855 + 500
    assert row["taxes"] == 1050
    assert row["dividends"] == 2000

    utc = _flow(client, hdr, scope="month", period="2026-09", tz_offset_minutes=0)["by_currency"][0]
    assert utc["buy_amount"] == 600855  # 8/31 20:00 UTC 那筆不在 9 月
    aug = _flow(client, hdr, scope="month", period="2026-08", tz_offset_minutes=0)["by_currency"][0]
    assert aug["buy_amount"] == 50000 + 100 + 1000

    all_ = _flow(client, hdr, scope="all")["by_currency"][0]
    assert all_["buy_count"] == 3
    year = _flow(client, hdr, scope="year", period="2026", tz_offset_minutes=480)["by_currency"][0]
    assert year["buy_count"] == 3


def test_investment_flow_does_not_change_income_expense_and_empty():
    client, _ = _make_client()
    hdr = _setup(client, "flow2@x.com")
    assert _flow(client, hdr, scope="all")["by_currency"] == []
    _trade(client, hdr, "buy", "2026-09-01T02:00:00+00:00", fee=855)
    r = client.get(
        "/api/v1/read/workspace/analytics",
        headers=hdr, params={"scope": "all", "metric": "expense"},
    )
    assert r.status_code == 200, r.text
    summary = r.json()["summary"]
    # 買股票是轉帳:不算支出、不算收入
    assert summary["expense_total"] == 0
    assert summary["income_total"] == 0


def test_investment_flow_rejects_bad_scope():
    client, _ = _make_client()
    hdr = _setup(client, "flow3@x.com")
    r = client.get("/api/v1/read/workspace/investment-flow", headers=hdr, params={"scope": "week"})
    assert r.status_code == 422
