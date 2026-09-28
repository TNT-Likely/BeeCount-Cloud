"""台灣證券交易所(上市)/ 證券櫃檯買賣中心(上櫃)官方 OpenAPI。

一次呼叫回傳全市場當日收盤行情,同時拿來當台股證券清單(代號/名稱)。
日期是民國年 `YYYMMDD`(例 1150924 = 2026-09-24)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import httpx

from .base import BULK_TIMEOUT, DividendData, QuoteData, SecurityInfo, new_client, parse_float

TWSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
# 除權除息預告表:列出近期(含剛過去幾天)的除權息日;金額常常晚於日期公告,
# 還沒公告時欄位是空字串。
TWSE_DIVIDEND_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
TPEX_DIVIDEND_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"

_TAIPEI = ZoneInfo("Asia/Taipei")
# 只收一般股票(4 碼數字)與 ETF(00 開頭),排除權證/牛熊證/債券等。
_STOCK_CODE = re.compile(r"^\d{4}$")
_ETF_CODE = re.compile(r"^00\d{2,4}[A-Z]?$")


@dataclass
class MarketSnapshot:
    securities: list[SecurityInfo]
    quotes: list[QuoteData]
    trade_date: date | None


def parse_roc_date(raw: object) -> date | None:
    text = str(raw or "").strip()
    if not re.fullmatch(r"\d{7}", text):
        return None
    try:
        return date(int(text[:3]) + 1911, int(text[3:5]), int(text[5:7]))
    except ValueError:
        return None


def _kind(code: str, name: str) -> str | None:
    if _ETF_CODE.match(code):
        return "bond_etf" if code.endswith("B") else "etf"
    if _STOCK_CODE.match(code):
        return "stock"
    return None


def _close_time(d: date | None) -> datetime | None:
    if d is None:
        return None
    return datetime.combine(d, time(13, 30), tzinfo=_TAIPEI).astimezone(timezone.utc)


def _parse(rows: list[dict], *, market: str, code_key: str, name_key: str, close_key: str, change_key: str, source: str) -> MarketSnapshot:
    securities: list[SecurityInfo] = []
    quotes: list[QuoteData] = []
    trade_date: date | None = None
    for row in rows:
        code = str(row.get(code_key) or "").strip().upper()
        name = str(row.get(name_key) or "").strip()
        kind = _kind(code, name)
        if kind is None:
            continue
        securities.append(SecurityInfo(market=market, symbol=code, name=name, currency="TWD", kind=kind))
        row_date = parse_roc_date(row.get("Date"))
        trade_date = trade_date or row_date
        close = parse_float(row.get(close_key))
        if close is None or close <= 0:
            continue  # 當日無成交
        change = parse_float(row.get(change_key))
        quotes.append(
            QuoteData(
                market=market,
                symbol=code,
                price=close,
                prev_close=(close - change) if change is not None else None,
                quote_time=_close_time(row_date),
                currency="TWD",
                name=name,
                source=source,
            )
        )
    return MarketSnapshot(securities=securities, quotes=quotes, trade_date=trade_date)


def parse_twse(rows: list[dict]) -> MarketSnapshot:
    return _parse(rows, market="TW", code_key="Code", name_key="Name", close_key="ClosingPrice", change_key="Change", source="twse")


def parse_tpex(rows: list[dict]) -> MarketSnapshot:
    return _parse(rows, market="TWO", code_key="SecuritiesCompanyCode", name_key="CompanyName", close_key="Close", change_key="Change", source="tpex")


async def fetch_twse(client: httpx.AsyncClient | None = None) -> MarketSnapshot:
    own = client is None
    client = client or new_client(BULK_TIMEOUT)
    try:
        resp = await client.get(TWSE_URL)
        resp.raise_for_status()
        return parse_twse(resp.json())
    finally:
        if own:
            await client.aclose()


async def fetch_tpex(client: httpx.AsyncClient | None = None) -> MarketSnapshot:
    own = client is None
    client = client or new_client(BULK_TIMEOUT)
    try:
        resp = await client.get(TPEX_URL)
        resp.raise_for_status()
        return parse_tpex(resp.json())
    finally:
        if own:
            await client.aclose()


# ---------------------------------------------------------------------------
# 除權除息預告表
# ---------------------------------------------------------------------------


def _parse_dividends(
    rows: list[dict], *, market: str, date_key: str, code_key: str, name_key: str, source: str,
) -> list[DividendData]:
    out: list[DividendData] = []
    for row in rows:
        code = str(row.get(code_key) or "").strip().upper()
        if _kind(code, "") is None:
            continue
        ex_date = parse_roc_date(row.get(date_key))
        if ex_date is None:
            continue
        cash = parse_float(row.get("CashDividend")) or 0.0
        stock = parse_float(row.get("StockDividendRatio")) or 0.0
        out.append(
            DividendData(
                market=market,
                symbol=code,
                ex_date=ex_date,
                cash_per_share=max(cash, 0.0),
                stock_per_share=max(stock, 0.0),
                currency="TWD",
                source=source,
                name=str(row.get(name_key) or "").strip() or None,
            )
        )
    return out


def parse_twse_dividends(rows: list[dict]) -> list[DividendData]:
    return _parse_dividends(rows, market="TW", date_key="Date", code_key="Code", name_key="Name", source="twse")


def parse_tpex_dividends(rows: list[dict]) -> list[DividendData]:
    return _parse_dividends(
        rows, market="TWO", date_key="ExRrightsExDividendDate", code_key="SecuritiesCompanyCode",
        name_key="CompanyName", source="tpex",
    )


async def fetch_twse_dividends(client: httpx.AsyncClient) -> list[DividendData]:
    resp = await client.get(TWSE_DIVIDEND_URL)
    resp.raise_for_status()
    return parse_twse_dividends(resp.json())


async def fetch_tpex_dividends(client: httpx.AsyncClient) -> list[DividendData]:
    resp = await client.get(TPEX_DIVIDEND_URL)
    resp.raise_for_status()
    return parse_tpex_dividends(resp.json())
