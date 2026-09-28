"""報價讀取 + 補抓 + 收盤排程。

兩條更新路徑(使用者確認的決策,docs/STOCK_HOLDINGS_SD.md §報價更新):
1. **收盤排程** `refresh_close_quotes`(`security_quote_close` job,每 5 分鐘
   檢查一次):各市場收盤後(`markets.close_fetch_threshold`)抓一次收盤價,
   只抓「有人持有」的標的。台股一次抓證交所/櫃買全市場(2 個請求),其它
   市場逐檔打 Yahoo。
2. **盤中補抓** `get_quotes`:App/Web 打開持股畫面時呼叫;盤中且快取超過
   15 分鐘、或完全沒有快取、或快取超過 12 小時,就向 Yahoo 補抓延遲報價。

上游失敗一律回傳舊快取並標記 `stale`,不讓使用者畫面整個空掉。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from ...models import ReadStockTradeProjection, Security, SecurityQuote
from . import markets, store
from .providers import twse, yahoo
from .providers.base import BULK_TIMEOUT, QuoteData, new_client

logger = logging.getLogger(__name__)

INTRADAY_TTL = timedelta(minutes=15)
STALE_TTL = timedelta(hours=12)
# 台股收盤資料偶爾晚發布:收盤門檻之後若抓到的還不是今天的資料,這段時間內
# 每次排程都再試一次,超過就放棄(假日也會走到這裡,抓到的是前一交易日收盤)。
CLOSE_RETRY_WINDOW = timedelta(hours=3)
_CONCURRENCY = 4

# 同一檔同時只放一個上游請求(僅 API 路徑用;排程在自己的 event loop 跑,
# 不共用這組 lock,避免跨 loop 使用 asyncio.Lock)。
_locks: dict[str, asyncio.Lock] = {}


@dataclass
class QuoteView:
    market: str
    symbol: str
    name: str | None
    currency: str | None
    price: float | None
    prev_close: float | None
    quote_time: datetime | None
    session: str | None
    source: str | None
    fetched_at: datetime | None
    stale: bool

    def to_dict(self) -> dict:
        change = None
        change_percent = None
        if self.price is not None and self.prev_close:
            change = round(self.price - self.prev_close, 6)
            change_percent = round(change / self.prev_close * 100, 4)
        return {
            "market": self.market,
            "symbol": self.symbol,
            "name": self.name,
            "currency": self.currency,
            "price": self.price,
            "prev_close": self.prev_close,
            "change": change,
            "change_percent": change_percent,
            "quote_time": _iso(self.quote_time),
            "session": self.session,
            "source": self.source,
            "fetched_at": _iso(self.fetched_at),
            "stale": self.stale,
        }


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def _load(db: Session, keys: list[tuple[str, str]]) -> dict[tuple[str, str], QuoteView]:
    out: dict[tuple[str, str], QuoteView] = {}
    ids = store.security_ids(db, keys)
    if ids:
        rows = db.execute(
            select(Security, SecurityQuote)
            .outerjoin(SecurityQuote, SecurityQuote.security_id == Security.id)
            .where(Security.id.in_(list(ids.values())))
        ).all()
        for sec, quote in rows:
            out[(sec.market, sec.symbol)] = QuoteView(
                market=sec.market,
                symbol=sec.symbol,
                name=sec.name or None,
                currency=sec.currency,
                price=quote.price if quote else None,
                prev_close=quote.prev_close if quote else None,
                quote_time=_aware(quote.quote_time) if quote else None,
                session=quote.session if quote else None,
                source=quote.source if quote else None,
                fetched_at=_aware(quote.fetched_at) if quote else None,
                stale=False,
            )
    for key in keys:
        if key not in out:
            m = markets.get_market(key[0])
            out[key] = QuoteView(
                market=key[0], symbol=key[1], name=None,
                currency=m.currency if m else None, price=None, prev_close=None,
                quote_time=None, session=None, source=None, fetched_at=None, stale=False,
            )
    return out


def needs_refresh(view: QuoteView, now: datetime) -> bool:
    m = markets.get_market(view.market)
    if m is None:
        return False
    if view.price is None or view.fetched_at is None:
        return True
    age = now - view.fetched_at
    if markets.is_market_open(m, now) and age > INTRADAY_TTL:
        return True
    return age > STALE_TTL


def _write_quotes(bind, quotes: list[QuoteData], *, session_label: str, now: datetime) -> None:
    ScopedSession = sessionmaker(bind=bind, autocommit=False, autoflush=False)
    with ScopedSession() as s:
        for q in quotes:
            m = markets.get_market(q.market)
            store.ensure_security(
                s, market=q.market, symbol=q.symbol, name=q.name,
                currency=(q.currency or (m.currency if m else "USD")),
            )
        store.upsert_quotes(s, quotes, session=session_label, now=now)
        s.commit()


async def get_quotes(
    db: Session, keys: list[tuple[str, str]], *, refresh: bool = True, now: datetime | None = None
) -> list[QuoteView]:
    """回傳 keys 順序對應的報價。`refresh=False` 只讀快取。"""
    now = now or datetime.now(timezone.utc)
    keys = [(m.upper(), s.upper()) for m, s in keys]
    views = _load(db, keys)
    to_fetch = [k for k in dict.fromkeys(keys) if refresh and needs_refresh(views[k], now)]
    if not to_fetch:
        return [views[k] for k in keys]

    bind = db.get_bind()
    # 打上游前放掉呼叫方 session 占用的連線(同 exchange_rate/fetcher.get_rates
    # 的理由:別讓慢上游把連線池吃滿)。這裡只讀過純資料,rollback 不影響。
    db.rollback()

    sem = asyncio.Semaphore(_CONCURRENCY)
    fetched: list[QuoteData] = []
    failed: set[tuple[str, str]] = set()

    async with new_client() as client:

        async def _one(key: tuple[str, str]) -> None:
            lock = _locks.setdefault(f"{key[0]}:{key[1]}", asyncio.Lock())
            async with lock, sem:
                try:
                    fetched.append(await yahoo.fetch_quote(key[0], key[1], client=client))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("securities: quote fetch failed %s:%s err=%s", key[0], key[1], exc)
                    failed.add(key)

        await asyncio.gather(*(_one(k) for k in to_fetch))

    if fetched:
        _write_quotes(
            bind, fetched,
            session_label="intraday", now=now,
        )
    fresh = _load(db, keys) if fetched else views
    for key in failed:
        fresh[key].stale = True
    return [fresh[k] for k in keys]


# ---------------------------------------------------------------------------
# 收盤排程
# ---------------------------------------------------------------------------


def held_keys(db: Session) -> dict[str, set[str]]:
    """目前有人持有(淨股數 > 0)的標的,依市場分組。只用粗略的「加總買入類
    − 賣出」判斷要不要抓報價,不需要精確的移動平均成本。"""
    signed = case(
        (ReadStockTradeProjection.trade_type == "sell", -ReadStockTradeProjection.shares),
        (
            ReadStockTradeProjection.trade_type.in_(("buy", "opening", "reinvest", "stock_dividend")),
            ReadStockTradeProjection.shares,
        ),
        else_=0.0,
    )
    rows = db.execute(
        select(
            ReadStockTradeProjection.market,
            ReadStockTradeProjection.symbol,
            func.sum(signed),
        ).group_by(ReadStockTradeProjection.market, ReadStockTradeProjection.symbol)
    ).all()
    out: dict[str, set[str]] = {}
    for market, symbol, net in rows:
        if net is not None and float(net) > 1e-9 and market and symbol:
            out.setdefault(market.upper(), set()).add(symbol.upper())
    return out


def _close_done(view: QuoteView, market: markets.Market, threshold: datetime, now: datetime) -> bool:
    if view.session != "close" or view.fetched_at is None or view.fetched_at < threshold:
        return False
    if view.quote_time is not None and markets.local_now(market, view.quote_time).date() == markets.local_now(market, now).date():
        return True
    return now >= threshold + CLOSE_RETRY_WINDOW


async def _fetch_close(db: Session, market: markets.Market, symbols: set[str], now: datetime) -> int:
    count = 0
    if market.code in ("TW", "TWO"):
        try:
            async with new_client(BULK_TIMEOUT) as client:
                snapshot = await (twse.fetch_twse(client) if market.code == "TW" else twse.fetch_tpex(client))
            store.upsert_securities(db, snapshot.securities, now=now)
            db.flush()
            count += store.upsert_quotes(db, snapshot.quotes, session="close", now=now)
            got = {q.symbol for q in snapshot.quotes}
            symbols = symbols - got
            if not symbols:
                return count
        except Exception as exc:  # noqa: BLE001
            logger.warning("securities: %s official close fetch failed, fallback yahoo err=%s", market.code, exc)

    fetched: list[QuoteData] = []
    async with new_client() as client:
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _one(symbol: str) -> None:
            async with sem:
                try:
                    fetched.append(await yahoo.fetch_quote(market.code, symbol, client=client))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("securities: close fetch failed %s:%s err=%s", market.code, symbol, exc)

        await asyncio.gather(*(_one(s) for s in sorted(symbols)))
    for q in fetched:
        store.ensure_security(
            db, market=q.market, symbol=q.symbol, name=q.name, currency=q.currency or market.currency,
        )
    count += store.upsert_quotes(db, fetched, session="close", now=now)
    return count


def refresh_close_quotes(db: Session, *, now: datetime | None = None) -> dict:
    """`security_quote_close` job 本體。同步介面(排程在 worker thread 裡
    呼叫),內部用 asyncio.run 跑上游請求。"""
    now = now or datetime.now(timezone.utc)
    held = held_keys(db)
    touched_markets = 0
    quotes = 0
    for code, symbols in held.items():
        market = markets.get_market(code)
        if market is None:
            continue
        threshold = markets.close_fetch_threshold(market, now)
        if threshold is None:
            continue
        views = _load(db, [(code, s) for s in symbols])
        pending = {s for s in symbols if not _close_done(views[(code, s)], market, threshold, now)}
        if not pending:
            continue
        touched_markets += 1
        quotes += asyncio.run(_fetch_close(db, market, pending, now))
        db.commit()
    return {"markets": touched_markets, "quotes": quotes}
