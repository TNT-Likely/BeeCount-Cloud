from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import httpx

# Yahoo 對沒有 User-Agent 的請求會回 429/403。
USER_AGENT = "Mozilla/5.0 (compatible; BeeCountCloud/1.0; +https://github.com/TNT-Likely/BeeCount)"
TIMEOUT = httpx.Timeout(10.0)


@dataclass
class QuoteData:
    market: str
    symbol: str
    price: float
    prev_close: float | None
    quote_time: datetime | None
    currency: str | None
    name: str | None
    source: str


@dataclass
class SecurityInfo:
    market: str
    symbol: str
    name: str
    currency: str
    kind: str = "stock"


@dataclass
class DividendData:
    """一筆除權息事件。`stock_per_share` = 每股配幾股(台股無償配股率)。"""

    market: str
    symbol: str
    ex_date: date
    cash_per_share: float
    stock_per_share: float
    currency: str | None
    source: str
    name: str | None = None
    pay_date: date | None = None


# 證交所/櫃買全市場收盤檔一次幾百 KB,實測常常超過 10 秒。
BULK_TIMEOUT = httpx.Timeout(30.0)


def new_client(timeout: httpx.Timeout = TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )


def parse_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        text = str(raw).replace(",", "").strip()
        if not text or text in {"--", "-", "---"}:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None
