"""證券搜尋。

- 台股(TW/TWO):本地 `securities` 表(證交所/櫃買全市場清單)比對代號前綴
  或名稱片段——中文名稱「台積」也找得到。清單不存在或超過 7 天沒更新時,
  搜尋當下順便同步一次(順帶寫入收盤報價)。
- 其它市場:Yahoo search,結果 upsert 進 `securities`。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ...models import Security
from . import store
from .providers import twse, yahoo
from .providers.base import BULK_TIMEOUT, SecurityInfo, new_client

logger = logging.getLogger(__name__)

MASTER_MAX_AGE = timedelta(days=7)
_TW_MARKETS = ("TW", "TWO")
_master_lock = asyncio.Lock()


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# 上市/上櫃官方清單各有上千檔;任一市場少於這個數量代表那個市場從沒完整
# 同步過——只是查報價/Yahoo 搜尋時零星建立的幾筆(名稱還是英文),或上次
# 同步時其中一個來源失敗了。兩個市場要分開判斷,不能合計。
_MASTER_MIN_ROWS = 300


def _tw_master_fresh(db: Session, now: datetime) -> bool:
    rows = db.execute(
        select(Security.market, func.count(Security.id), func.max(Security.updated_at))
        .where(Security.market.in_(_TW_MARKETS))
        .group_by(Security.market)
    ).all()
    stats = {market: (count, _aware(latest)) for market, count, latest in rows}
    for market in _TW_MARKETS:
        count, latest = stats.get(market, (0, None))
        if count < _MASTER_MIN_ROWS or latest is None or now - latest >= MASTER_MAX_AGE:
            return False
    return True


async def refresh_tw_master(bind, *, now: datetime | None = None) -> int:
    """同步台股清單 + 收盤報價。回傳寫入的證券數。失敗拋例外。"""
    now = now or datetime.now(timezone.utc)
    async with new_client(BULK_TIMEOUT) as client:
        results = await asyncio.gather(
            twse.fetch_twse(client), twse.fetch_tpex(client), return_exceptions=True
        )
    ScopedSession = sessionmaker(bind=bind, autocommit=False, autoflush=False)
    written = 0
    errors: list[str] = []
    with ScopedSession() as s:
        for res in results:
            if isinstance(res, BaseException):
                errors.append(str(res))
                continue
            store.upsert_securities(s, res.securities, now=now)
            s.flush()
            store.upsert_quotes(s, res.quotes, session="close", now=now)
            written += len(res.securities)
        s.commit()
    if errors:
        if written == 0:
            raise RuntimeError("tw master refresh failed: " + "; ".join(errors))
        logger.warning("securities: tw master partially refreshed errors=%s", errors)
    return written


def _to_dict(row: Security) -> dict:
    return {
        "market": row.market,
        "symbol": row.symbol,
        "name": row.name,
        "currency": row.currency,
        "kind": row.kind,
    }


def _local_search(db: Session, q: str, markets_filter: tuple[str, ...] | None, limit: int) -> list[Security]:
    upper = q.upper()
    stmt = select(Security).where(
        Security.is_active.is_(True),
        or_(Security.symbol.like(f"{upper}%"), Security.name.like(f"%{q}%")),
    )
    if markets_filter:
        stmt = stmt.where(Security.market.in_(markets_filter))
    rows = db.scalars(stmt.limit(200)).all()

    def rank(r: Security) -> tuple:
        if r.symbol == upper:
            tier = 0
        elif r.symbol.startswith(upper):
            tier = 1
        else:
            tier = 2
        return (tier, len(r.symbol), r.symbol)

    return sorted(rows, key=rank)[:limit]


async def search_securities(
    db: Session, q: str, *, market: str | None = None, limit: int = 20, now: datetime | None = None
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    q = q.strip()
    if not q:
        return []
    market = market.upper() if market else None
    want_tw = market is None or market in _TW_MARKETS
    # Yahoo search 對中文等非 ASCII 查詢回 400,這類查詢只可能命中台股中文名稱。
    want_other = (market is None or market not in _TW_MARKETS) and q.isascii()

    if want_tw and not _tw_master_fresh(db, now):
        bind = db.get_bind()
        db.rollback()
        async with _master_lock:
            if not _tw_master_fresh(db, now):
                try:
                    await refresh_tw_master(bind, now=now)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("securities: tw master refresh failed err=%s", exc)

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    local_filter = (market,) if market else None
    for row in _local_search(db, q, local_filter, limit):
        key = (row.market, row.symbol)
        if key not in seen:
            seen.add(key)
            results.append(_to_dict(row))

    if want_other and len(results) < limit:
        bind = db.get_bind()
        db.rollback()
        infos: list[SecurityInfo] = []
        try:
            infos = await yahoo.search(q)
        except Exception as exc:  # noqa: BLE001
            logger.warning("securities: yahoo search failed q=%r err=%s", q, exc)
        if market:
            infos = [i for i in infos if i.market == market]
        # 台股以官方清單為準,Yahoo 的台股結果(名稱是英文)只在官方清單查不到時用。
        infos = [i for i in infos if (i.market, i.symbol) not in seen]
        if infos:
            ScopedSession = sessionmaker(bind=bind, autocommit=False, autoflush=False)
            with ScopedSession() as s:
                existing = store.security_ids(s, [(i.market, i.symbol) for i in infos])
                store.upsert_securities(
                    s, [i for i in infos if (i.market, i.symbol) not in existing], now=now
                )
                s.commit()
            for info in infos:
                key = (info.market, info.symbol)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "market": info.market,
                        "symbol": info.symbol,
                        "name": info.name,
                        "currency": info.currency,
                        "kind": info.kind,
                    }
                )
    return results[:limit]
