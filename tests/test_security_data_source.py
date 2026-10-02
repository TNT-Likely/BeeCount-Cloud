"""股票資料來源切換(Phase 3,docs/STOCK_HOLDINGS_SD.md §12):

- Twelve Data 回應解析(quote / dividends / 錯誤 body)
- 付費來源失敗退回 Yahoo;免費來源完全不碰付費端點
- 後台 API:需 admin、key 不回傳明文、provider=twelvedata 必須有 key、
  `/test` 用存好的 key 打付費端點
- 切到付費來源後 `quotes.get_quotes` 真的走付費報價
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import SecurityDataSourceConfig, User
from src.services import secret_crypto
from src.services.securities import data_source, quotes
from src.services.securities.providers import base as provider_base
from src.services.securities.providers import twelvedata, yahoo

_TS: sessionmaker | None = None


def _make_client() -> TestClient:
    global _TS
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    _TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = _TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app)


def _admin_token(client: TestClient, email: str, *, admin: bool = True) -> str:
    res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "123456", "client_type": "app", "device_name": "p", "platform": "app"},
    )
    assert res.status_code == 200, res.text
    if admin:
        with _TS() as db:
            u = db.query(User).filter(User.id == res.json()["user"]["id"]).first()
            u.is_admin = True
            db.commit()
    res = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "123456", "client_type": "web", "device_name": "w", "platform": "web"},
    )
    return res.json()["access_token"]


# --------------------------------------------------------------------------- #
# 解析                                                                         #
# --------------------------------------------------------------------------- #


def test_parse_twelvedata_quote_and_error():
    q = twelvedata.parse_quote(
        {"name": "Apple Inc", "currency": "USD", "close": "190.5", "previous_close": "189.0", "timestamp": 1735000000},
        market="US", symbol="aapl",
    )
    assert (q.market, q.symbol, q.price, q.prev_close, q.currency, q.source) == (
        "US", "AAPL", 190.5, 189.0, "USD", "twelvedata",
    )
    assert q.quote_time == datetime.fromtimestamp(1735000000, tz=timezone.utc)
    with pytest.raises(RuntimeError, match="429"):
        twelvedata.parse_quote({"status": "error", "code": 429, "message": "limit"}, market="US", symbol="AAPL")


def test_parse_twelvedata_dividends_skips_bad_rows():
    events = twelvedata.parse_dividends(
        {"meta": {"currency": "USD"}, "dividends": [
            {"ex_date": "2026-05-10", "amount": 0.2500001},
            {"ex_date": "bad", "amount": 1},
            {"ex_date": "2026-02-10", "amount": 0},
        ]},
        market="US", symbol="AAPL",
    )
    assert [(e.ex_date.isoformat(), e.cash_per_share, e.source) for e in events] == [("2026-05-10", 0.25, "twelvedata")]


def test_redact_masks_api_key_in_httpx_error_text():
    msg = "Client error '401' for url 'https://api.twelvedata.com/quote?symbol=AAPL&apikey=SECRETKEY&x=1'"
    out = twelvedata.redact(msg)
    assert "SECRETKEY" not in out and "apikey=***" in out and "x=1" in out


def test_twelvedata_params_use_mic_for_non_us():
    assert twelvedata._params("TW", "2330", "k")["mic_code"] == "XTAI"
    assert "mic_code" not in twelvedata._params("US", "AAPL", "k")


# --------------------------------------------------------------------------- #
# 來源切換 / 備援                                                              #
# --------------------------------------------------------------------------- #


def _qd(source, price):
    return provider_base.QuoteData(
        market="US", symbol="AAPL", price=price, prev_close=None, quote_time=None,
        currency="USD", name="Apple", source=source,
    )


def test_paid_source_used_then_falls_back_to_yahoo(monkeypatch):
    calls: list[str] = []

    async def paid_ok(market, symbol, key, client=None):
        calls.append("paid")
        assert key == "K"
        return _qd("twelvedata", 1.0)

    async def paid_fail(market, symbol, key, client=None):
        calls.append("paid")
        raise RuntimeError("quota")

    async def yahoo_ok(market, symbol, client=None):
        calls.append("yahoo")
        return _qd("yahoo", 2.0)

    monkeypatch.setattr(yahoo, "fetch_quote", yahoo_ok)
    paid = data_source.DataSource(data_source.PROVIDER_TWELVEDATA, "K")

    monkeypatch.setattr(twelvedata, "fetch_quote", paid_ok)
    assert asyncio.run(data_source.fetch_quote(paid, "US", "AAPL", None)).source == "twelvedata"
    assert calls == ["paid"]

    calls.clear()
    monkeypatch.setattr(twelvedata, "fetch_quote", paid_fail)
    assert asyncio.run(data_source.fetch_quote(paid, "US", "AAPL", None)).source == "yahoo"
    assert calls == ["paid", "yahoo"]

    calls.clear()
    assert asyncio.run(data_source.fetch_quote(data_source.FREE, "US", "AAPL", None)).source == "yahoo"
    assert calls == ["yahoo"]


def test_paid_dividends_empty_falls_back_to_yahoo(monkeypatch):
    async def paid_empty(market, symbol, key, client=None):
        return []

    async def yahoo_div(market, symbol, client=None):
        return ["y"]

    monkeypatch.setattr(twelvedata, "fetch_dividends", paid_empty)
    monkeypatch.setattr(yahoo, "fetch_dividends", yahoo_div)
    paid = data_source.DataSource(data_source.PROVIDER_TWELVEDATA, "K")
    assert asyncio.run(data_source.fetch_dividends(paid, "US", "AAPL", None)) == ["y"]


# --------------------------------------------------------------------------- #
# 後台 API                                                                     #
# --------------------------------------------------------------------------- #


def test_admin_api_requires_admin():
    client = _make_client()
    try:
        token = _admin_token(client, "na@t.com", admin=False)
        r = client.get("/api/v1/admin/security-data-source", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_api_key_is_encrypted_and_never_returned(monkeypatch):
    client = _make_client()
    try:
        hdr = {"Authorization": f"Bearer {_admin_token(client, 'ad@t.com')}"}
        url = "/api/v1/admin/security-data-source"
        body = client.get(url, headers=hdr).json()
        assert body["provider"] == "free" and body["api_key_set"] is False

        # twelvedata 沒 key → 400
        assert client.put(url, headers=hdr, json={"provider": "twelvedata"}).status_code == 400
        assert client.put(url, headers=hdr, json={"provider": "nope"}).status_code == 400

        r = client.put(url, headers=hdr, json={"provider": "twelvedata", "api_key": " SECRET "})
        assert r.status_code == 200, r.text
        assert r.json()["provider"] == "twelvedata" and r.json()["api_key_set"] is True
        assert "SECRET" not in r.text
        with _TS() as db:
            cfg = db.get(SecurityDataSourceConfig, 1)
            assert cfg.api_key_encrypted and "SECRET" not in cfg.api_key_encrypted
            assert secret_crypto.decrypt(cfg.api_key_encrypted) == "SECRET"
            assert data_source.load_source(db) == data_source.DataSource("twelvedata", "SECRET")

        # 不帶 api_key 的更新不會清掉 key
        r = client.put(url, headers=hdr, json={"provider": "twelvedata"})
        assert r.json()["api_key_set"] is True

        async def fake_quote(market, symbol, key, client=None):
            assert (market, symbol, key) == ("US", "AAPL", "SECRET")
            return _qd("twelvedata", 123.0)

        monkeypatch.setattr(twelvedata, "fetch_quote", fake_quote)
        t = client.post(url + "/test", headers=hdr).json()
        assert t == {"ok": True, "message": "連線成功", "price": 123.0}

        async def boom(market, symbol, key, client=None):
            raise RuntimeError("twelvedata quote: 401 bad key")

        monkeypatch.setattr(twelvedata, "fetch_quote", boom)
        t = client.post(url + "/test", headers=hdr).json()
        assert t["ok"] is False and "401" in t["message"]
        assert client.get(url, headers=hdr).json()["last_test_error"]

        # 切回免費 + 清 key
        r = client.put(url, headers=hdr, json={"provider": "free", "clear_api_key": True})
        assert r.json() == {**r.json(), "provider": "free", "api_key_set": False}
        assert client.post(url + "/test", headers=hdr).json()["ok"] is False
    finally:
        app.dependency_overrides.clear()


def test_get_quotes_uses_paid_source_when_configured(monkeypatch):
    _make_client()
    try:
        async def fake_paid(market, symbol, key, client=None):
            assert key == "K"
            return _qd("twelvedata", 77.0)

        async def fail_yahoo(*a, **k):
            raise AssertionError("yahoo must not be called")

        monkeypatch.setattr(twelvedata, "fetch_quote", fake_paid)
        monkeypatch.setattr(yahoo, "fetch_quote", fail_yahoo)
        with _TS() as db:
            cfg = data_source.get_or_create_config(db)
            cfg.provider = "twelvedata"
            cfg.api_key_encrypted = secret_crypto.encrypt("K")
            db.commit()
            [view] = asyncio.run(quotes.get_quotes(db, [("US", "AAPL")], refresh=True))
            assert view.price == 77.0 and view.source == "twelvedata"
    finally:
        app.dependency_overrides.clear()
