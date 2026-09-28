"""Yahoo Finance 非官方端點(chart / search)。無需 API key,但隨時可能改版
——失敗一律拋例外,由呼叫方決定是否回傳舊快取。"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from .. import markets
from .base import DividendData, QuoteData, SecurityInfo, new_client, parse_float

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"

SOURCE = "yahoo"
CHART_PARAMS = {"range": "1d", "interval": "1d"}
# 股利歷史:抓最近半年,足夠涵蓋待確認股利的回溯範圍
# (`dividends.DETECT_LOOKBACK_DAYS`)。
DIVIDEND_PARAMS = {"range": "6mo", "interval": "1d", "events": "div"}


def parse_chart(payload: dict, *, market: str, symbol: str) -> QuoteData:
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise RuntimeError(f"yahoo chart: no result for {symbol}: {error}")
    meta = result.get("meta") or {}
    price = parse_float(meta.get("regularMarketPrice"))
    if price is None:
        raise RuntimeError(f"yahoo chart: missing price for {symbol}")
    prev_close = parse_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
    currency = meta.get("currency")
    # 倫敦等市場用便士(GBp)報價,換成英鎊。
    if currency == "GBp":
        price /= 100.0
        prev_close = prev_close / 100.0 if prev_close is not None else None
        currency = "GBP"
    ts = meta.get("regularMarketTime")
    quote_time = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    return QuoteData(
        market=market,
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        quote_time=quote_time,
        currency=currency.upper() if isinstance(currency, str) else None,
        name=meta.get("longName") or meta.get("shortName"),
        source=SOURCE,
    )


async def fetch_quote(market: str, symbol: str, client: httpx.AsyncClient | None = None) -> QuoteData:
    ysym = markets.yahoo_symbol(market, symbol)
    own = client is None
    client = client or new_client()
    try:
        # range 必須是 1d:chartPreviousClose 是「圖表區間開始前」的收盤價,
        # range=5d 會拿到 5 個交易日前的價格,今日漲跌幅整個算錯。
        resp = await client.get(_CHART_URL.format(symbol=ysym), params=CHART_PARAMS)
        resp.raise_for_status()
        return parse_chart(resp.json(), market=market.upper(), symbol=symbol.upper())
    finally:
        if own:
            await client.aclose()


_KIND_BY_QUOTE_TYPE = {"EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "other"}


def parse_search(payload: dict) -> list[SecurityInfo]:
    out: list[SecurityInfo] = []
    for q in payload.get("quotes") or []:
        kind = _KIND_BY_QUOTE_TYPE.get(str(q.get("quoteType") or "").upper())
        if kind is None:
            continue
        mapped = markets.from_yahoo_symbol(str(q.get("symbol") or ""), q.get("exchange"))
        if mapped is None:
            continue
        market, symbol = mapped
        m = markets.get_market(market)
        if m is None:
            continue
        out.append(
            SecurityInfo(
                market=market,
                symbol=symbol,
                name=str(q.get("longname") or q.get("shortname") or symbol),
                currency=m.currency,
                kind=kind,
            )
        )
    return out


async def search(query: str, client: httpx.AsyncClient | None = None) -> list[SecurityInfo]:
    own = client is None
    client = client or new_client()
    try:
        resp = await client.get(
            _SEARCH_URL, params={"q": query, "quotesCount": 10, "newsCount": 0}
        )
        resp.raise_for_status()
        return parse_search(resp.json())
    finally:
        if own:
            await client.aclose()


def parse_dividends(payload: dict, *, market: str, symbol: str) -> list[DividendData]:
    """chart `events.dividends`:key/`date` 是除息日開盤時間(epoch 秒),換成
    交易所當地日期。Yahoo 沒有配股(stock dividend)也沒有發放日。金額有浮點
    雜訊(例 5.000011),四捨五入到 4 位。"""
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        error = (payload.get("chart") or {}).get("error")
        raise RuntimeError(f"yahoo chart: no result for {symbol}: {error}")
    meta = result.get("meta") or {}
    currency = meta.get("currency")
    divisor = 1.0
    if currency == "GBp":
        currency, divisor = "GBP", 100.0
    m = markets.get_market(market)
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or (m.tz if m else "UTC"))
    out: list[DividendData] = []
    for item in ((result.get("events") or {}).get("dividends") or {}).values():
        amount = parse_float(item.get("amount"))
        ts = item.get("date")
        if amount is None or amount <= 0 or not ts:
            continue
        out.append(
            DividendData(
                market=market,
                symbol=symbol,
                ex_date=datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(tz).date(),
                cash_per_share=round(amount / divisor, 4),
                stock_per_share=0.0,
                currency=currency.upper() if isinstance(currency, str) else None,
                source=SOURCE,
                name=meta.get("longName") or meta.get("shortName"),
            )
        )
    return sorted(out, key=lambda d: d.ex_date)


async def fetch_dividends(market: str, symbol: str, client: httpx.AsyncClient) -> list[DividendData]:
    ysym = markets.yahoo_symbol(market, symbol)
    resp = await client.get(_CHART_URL.format(symbol=ysym), params=DIVIDEND_PARAMS)
    resp.raise_for_status()
    return parse_dividends(resp.json(), market=market.upper(), symbol=symbol.upper())
