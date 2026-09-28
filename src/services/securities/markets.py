"""市場代碼定義。App 端 lib/services/investment/markets.dart 維護同一份
清單(只用到代碼/幣別/名稱),改這裡要一起改。

`market` 是 stock_trade/securities 的業務鍵之一,一旦寫進使用者資料就不能
改名。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Market:
    code: str
    currency: str
    tz: str
    open_time: time
    close_time: time
    # 收盤後多久才去抓收盤價(交易所資料發布有延遲)。
    close_fetch_delay: timedelta
    # Yahoo Finance 代號後綴,例如 2330 → 2330.TW;美股沒有後綴。
    yahoo_suffix: str

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)


MARKETS: dict[str, Market] = {
    m.code: m
    for m in (
        Market("TW", "TWD", "Asia/Taipei", time(9, 0), time(13, 30), timedelta(minutes=90), ".TW"),
        Market("TWO", "TWD", "Asia/Taipei", time(9, 0), time(13, 30), timedelta(minutes=90), ".TWO"),
        Market("US", "USD", "America/New_York", time(9, 30), time(16, 0), timedelta(minutes=30), ""),
        Market("HK", "HKD", "Asia/Hong_Kong", time(9, 30), time(16, 0), timedelta(minutes=30), ".HK"),
        Market("JP", "JPY", "Asia/Tokyo", time(9, 0), time(15, 30), timedelta(minutes=30), ".T"),
        Market("SS", "CNY", "Asia/Shanghai", time(9, 30), time(15, 0), timedelta(minutes=30), ".SS"),
        Market("SZ", "CNY", "Asia/Shanghai", time(9, 30), time(15, 0), timedelta(minutes=30), ".SZ"),
        Market("KS", "KRW", "Asia/Seoul", time(9, 0), time(15, 30), timedelta(minutes=30), ".KS"),
        Market("KQ", "KRW", "Asia/Seoul", time(9, 0), time(15, 30), timedelta(minutes=30), ".KQ"),
        Market("LSE", "GBP", "Europe/London", time(8, 0), time(16, 30), timedelta(minutes=30), ".L"),
    )
}

# Yahoo 搜尋結果的 exchange 代碼 → 美股。其它市場靠代號後綴判斷。
_YAHOO_US_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS", "NAS", "NYS", "PNK", "OQX", "OQB"}


def get_market(code: str | None) -> Market | None:
    if not code:
        return None
    return MARKETS.get(code.upper())


def yahoo_symbol(market: str, symbol: str) -> str:
    m = get_market(market)
    suffix = m.yahoo_suffix if m else ""
    return f"{symbol.upper()}{suffix}"


def from_yahoo_symbol(yahoo_sym: str, exchange: str | None = None) -> tuple[str, str] | None:
    """Yahoo 代號 → (market, symbol)。不支援的市場回 None。"""
    s = yahoo_sym.strip().upper()
    if "." in s:
        base, _, suffix = s.rpartition(".")
        for m in MARKETS.values():
            if m.yahoo_suffix and m.yahoo_suffix[1:] == suffix:
                return m.code, base
        return None
    if exchange is None or exchange.upper() in _YAHOO_US_EXCHANGES:
        return "US", s
    return None


def local_now(market: Market, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(market.zone)


def is_trading_day(market: Market, day: date) -> bool:
    """只排除週末。國定假日不做日曆:假日抓到的仍是前一交易日的收盤價,
    結果是對的,只是多打一次上游。"""
    return day.weekday() < 5


def is_market_open(market: Market, now: datetime | None = None) -> bool:
    local = local_now(market, now)
    if not is_trading_day(market, local.date()):
        return False
    return market.open_time <= local.time() <= market.close_time


def close_fetch_threshold(market: Market, now: datetime | None = None) -> datetime | None:
    """今天(市場當地日期)的「可以抓收盤價」時間點(UTC)。非交易日或還沒
    到時間回 None。"""
    local = local_now(market, now)
    if not is_trading_day(market, local.date()):
        return None
    threshold_local = datetime.combine(local.date(), market.close_time, tzinfo=market.zone) + market.close_fetch_delay
    if local < threshold_local:
        return None
    return threshold_local.astimezone(timezone.utc)
