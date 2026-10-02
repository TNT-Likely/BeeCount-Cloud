"""Twelve Data(付費資料來源,Phase 3,docs/STOCK_HOLDINGS_SD.md §12)。

後台選了 `twelvedata` 並填入 API key 後,報價與除息資料優先走這裡,失敗時
由 `data_source` 退回 Yahoo。回應格式見 https://twelvedata.com/docs
(`/quote`、`/dividends`):錯誤時 HTTP 仍是 200,body 為
`{"status": "error", "code": 4xx, "message": ...}`,一律轉成例外。"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import httpx

from .. import markets
from .base import DividendData, QuoteData, new_client, parse_float

BASE_URL = "https://api.twelvedata.com"
SOURCE = "twelvedata"

# BeeCount 市場代碼 → Twelve Data MIC;美股不帶(預設)。
_MIC = {
    "TW": "XTAI",
    "TWO": "ROCO",
    "HK": "XHKG",
    "JP": "XTKS",
    "SS": "XSHG",
    "SZ": "XSHE",
    "KS": "XKRX",
    "KQ": "XKOS",
    "LSE": "XLON",
}


_APIKEY_RE = re.compile(r"(apikey=)[^&\s'\")]+", re.IGNORECASE)


def redact(text: str) -> str:
    """httpx 的錯誤訊息帶完整 URL(含 apikey=...),寫進 log/DB/回應前一律遮罩。"""
    return _APIKEY_RE.sub(r"\1***", text)


def _params(market: str, symbol: str, api_key: str) -> dict[str, str]:
    params = {"symbol": symbol.upper(), "apikey": api_key}
    mic = _MIC.get(market.upper())
    if mic:
        params["mic_code"] = mic
    return params


def _raise_on_error(payload: dict, what: str) -> None:
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise RuntimeError(f"twelvedata {what}: {payload.get('code')} {payload.get('message')}")


def parse_quote(payload: dict, *, market: str, symbol: str) -> QuoteData:
    _raise_on_error(payload, "quote")
    price = parse_float(payload.get("close"))
    if price is None:
        raise RuntimeError(f"twelvedata quote: missing price for {symbol}")
    ts = payload.get("timestamp")
    quote_time = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    currency = payload.get("currency")
    m = markets.get_market(market)
    return QuoteData(
        market=market.upper(),
        symbol=symbol.upper(),
        price=price,
        prev_close=parse_float(payload.get("previous_close")),
        quote_time=quote_time,
        currency=(currency.upper() if isinstance(currency, str) and currency else (m.currency if m else None)),
        name=payload.get("name"),
        source=SOURCE,
    )


async def fetch_quote(market: str, symbol: str, api_key: str, client: httpx.AsyncClient | None = None) -> QuoteData:
    own = client is None
    client = client or new_client()
    try:
        resp = await client.get(f"{BASE_URL}/quote", params=_params(market, symbol, api_key))
        resp.raise_for_status()
        return parse_quote(resp.json(), market=market, symbol=symbol)
    finally:
        if own:
            await client.aclose()


def parse_dividends(payload: dict, *, market: str, symbol: str) -> list[DividendData]:
    _raise_on_error(payload, "dividends")
    meta = payload.get("meta") or {}
    currency = meta.get("currency")
    m = markets.get_market(market)
    out: list[DividendData] = []
    for item in payload.get("dividends") or []:
        amount = parse_float(item.get("amount"))
        raw = item.get("ex_date")
        if amount is None or amount <= 0 or not raw:
            continue
        try:
            ex = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        out.append(
            DividendData(
                market=market.upper(),
                symbol=symbol.upper(),
                ex_date=ex,
                cash_per_share=round(amount, 4),
                stock_per_share=0.0,
                currency=(currency.upper() if isinstance(currency, str) and currency else (m.currency if m else None)),
                source=SOURCE,
                name=meta.get("name"),
            )
        )
    return out


async def fetch_dividends(
    market: str, symbol: str, api_key: str, client: httpx.AsyncClient | None = None
) -> list[DividendData]:
    own = client is None
    client = client or new_client()
    try:
        params = _params(market, symbol, api_key)
        params["range"] = "1y"
        resp = await client.get(f"{BASE_URL}/dividends", params=params)
        resp.raise_for_status()
        return parse_dividends(resp.json(), market=market, symbol=symbol)
    finally:
        if own:
            await client.aclose()
