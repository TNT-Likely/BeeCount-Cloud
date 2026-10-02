"""股利(股票持股 Phase 2,docs/STOCK_HOLDINGS_SD.md §7)。

兩個排程(`services/scheduled_jobs.py`):
1. `security_dividend_sync` → `sync_dividend_events`:抓「有人交易過」的標的
   除權息事件寫進 `security_dividend_events`。台股先吃證交所/櫃買官方預告表
   (整批 2 個請求,有配股資料、未來除息日),再逐檔打 Yahoo 補最近半年歷史
   (官方預告表只列近期,系統剛上線或排程停過時靠 Yahoo 補)。官方來源寫過
   的事件不會被 Yahoo 蓋掉。
2. `security_dividend_detector` → `detect_pending_dividends`:除息日當天(含)
   之後、`DETECT_LOOKBACK_DAYS` 天內的事件,依「除息日前一天」的持股替每個
   投資理財帳戶建 `pending_dividends` + 發通知;pending 狀態的估算值每次重算。

實收估算 `estimate_dividend`(App `lib/services/investment/dividend_estimate.dart`、
Web `web-features/src/lib/investment.ts::estimateDividend` 同一套規則,改一邊
要改另一邊):
- 股利總額 = 股數 × 每股現金股利(TWD/JPY/KRW 無條件捨去到整數,其它四捨五入到分)
- 預扣稅 = 總額 × dividendWithholdingRate
- 二代健保 = 總額 ≥ nhiThreshold 時 總額 × nhiSupplementRate
- 手續費 = dividendFeeFixed + 總額 × dividendFeeRate(總額為 0 時不收)
- 實收 = 總額 − 手續費 − 預扣稅 − 二代健保(不小於 0)
- 配股 = 股數 × 每股配股數(台股捨去到整股,零股部分券商通常折現,不另外算)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    PendingDividend,
    ReadStockTradeProjection,
    ReadTxProjection,
    Security,
    SecurityDividendEvent,
    UserAccountProjection,
)
from .. import notifications as notification_service
from . import holdings as holdings_service
from . import data_source, markets, store
from .providers import twse
from .providers.base import BULK_TIMEOUT, DividendData, new_client

logger = logging.getLogger(__name__)

# 除息日超過這麼多天還沒偵測到的事件就不再補建(例如使用者現在才補記一年前
# 的期初持股,不該一口氣跳出一堆陳年股利)。
DETECT_LOOKBACK_DAYS = 60
_CONCURRENCY = 4
_OFFICIAL_SOURCES = {"twse", "tpex"}
_ZERO_DECIMAL_CURRENCIES = {"TWD", "JPY", "KRW"}
_WHOLE_SHARE_MARKETS = {"TW", "TWO"}
_EPS = 1e-9

# ---------------------------------------------------------------------------
# 費用設定 + 估算
# ---------------------------------------------------------------------------

# 跟 App `InvestmentSettings.defaultsFor` / Web `investmentDefaults` 一致(只列
# 股利用得到的欄位)。
_DIVIDEND_DEFAULTS: dict[str, dict[str, float]] = {
    "TW": {"dividendFeeFixed": 10, "dividendFeeRate": 0, "dividendWithholdingRate": 0,
           "nhiSupplementRate": 0.0211, "nhiThreshold": 20000},
    "US": {"dividendFeeFixed": 0, "dividendFeeRate": 0, "dividendWithholdingRate": 0.3,
           "nhiSupplementRate": 0, "nhiThreshold": 0},
}
_DIVIDEND_DEFAULTS["TWO"] = _DIVIDEND_DEFAULTS["TW"]
_OTHER_DEFAULTS = {"dividendFeeFixed": 0, "dividendFeeRate": 0, "dividendWithholdingRate": 0,
                   "nhiSupplementRate": 0, "nhiThreshold": 0}


def parse_settings(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


def resolve_dividend_settings(market: str, settings: dict[str, Any] | None) -> dict[str, float]:
    base = dict(_DIVIDEND_DEFAULTS.get(market.upper(), _OTHER_DEFAULTS))
    for key in base:
        value = (settings or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            base[key] = float(value)
    return base


def round_money(value: float, currency: str | None) -> float:
    if (currency or "").upper() in _ZERO_DECIMAL_CURRENCIES:
        return float(math.floor(value + _EPS))
    return round(value + 0.0, 2)


@dataclass
class DividendEstimate:
    gross: float
    fee: float
    tax: float
    net: float
    stock_shares: float


def estimate_dividend(
    *, market: str, currency: str | None, shares: float, cash_per_share: float,
    stock_per_share: float, settings: dict[str, Any] | None,
) -> DividendEstimate:
    s = resolve_dividend_settings(market, settings)
    gross = round_money(max(shares, 0.0) * max(cash_per_share, 0.0), currency)
    if gross > 0:
        withholding = round_money(gross * s["dividendWithholdingRate"], currency)
        nhi = 0.0
        if s["nhiSupplementRate"] > 0 and gross >= s["nhiThreshold"]:
            nhi = round_money(gross * s["nhiSupplementRate"], currency)
        fee = round_money(s["dividendFeeFixed"] + gross * s["dividendFeeRate"], currency)
    else:
        withholding = nhi = fee = 0.0
    tax = withholding + nhi
    fee = min(fee, max(gross - tax, 0.0))
    net = round_money(max(gross - fee - tax, 0.0), currency)
    raw_stock = max(shares, 0.0) * max(stock_per_share, 0.0)
    if market.upper() in _WHOLE_SHARE_MARKETS:
        # 預告表的配股率是截斷過的小數(0.04999999 = 每千股 50 股),先四捨五入到
        # 千分位再捨去,不然 1000 股會算成 49 股。
        stock_shares = float(math.floor(round(raw_stock, 3)))
    else:
        stock_shares = round(raw_stock, 6)
    return DividendEstimate(gross=gross, fee=fee, tax=tax, net=net, stock_shares=stock_shares)


def event_ref(market: str, symbol: str, ex_date: date) -> str:
    """寫進 stock_trade.dividendEventRef 的穩定鍵(不用 DB id,跨環境/重建
    事件表都不會變)。"""
    return f"{market.upper()}:{symbol.upper()}:{ex_date.isoformat()}"


# ---------------------------------------------------------------------------
# 除權息事件同步
# ---------------------------------------------------------------------------


def traded_keys(db: Session) -> dict[tuple[str, str], str | None]:
    """所有出現在明細裡的標的 → 名稱。只看持股還 > 0 的會漏掉「除息日前持有、
    之後賣光」的情況,所以直接用全部交易過的標的(量級 = 使用者交易過的
    標的數,很小)。"""
    rows = db.execute(
        select(
            ReadStockTradeProjection.market,
            ReadStockTradeProjection.symbol,
            ReadStockTradeProjection.security_name,
        ).distinct()
    ).all()
    out: dict[tuple[str, str], str | None] = {}
    for market, symbol, name in rows:
        if not market or not symbol:
            continue
        key = (market.upper(), symbol.upper())
        if key not in out or (name and not out[key]):
            out[key] = name
    return out


def upsert_events(db: Session, events: Iterable[DividendData], *, names: dict[tuple[str, str], str | None],
                  now: datetime) -> int:
    count = 0
    for ev in events:
        key = (ev.market.upper(), ev.symbol.upper())
        m = markets.get_market(key[0])
        if m is None:
            continue
        sec_id = store.ensure_security(
            db, market=key[0], symbol=key[1], name=names.get(key) or ev.name,
            currency=ev.currency or m.currency,
        )
        row = db.scalar(
            select(SecurityDividendEvent).where(
                SecurityDividendEvent.security_id == sec_id,
                SecurityDividendEvent.ex_date == ev.ex_date,
            )
        )
        if row is None:
            db.add(SecurityDividendEvent(
                security_id=sec_id, ex_date=ev.ex_date, pay_date=ev.pay_date,
                cash_per_share=ev.cash_per_share, stock_per_share=ev.stock_per_share,
                currency=ev.currency or m.currency, source=ev.source, updated_at=now,
            ))
            count += 1
            continue
        if row.source in _OFFICIAL_SOURCES and ev.source not in _OFFICIAL_SOURCES:
            continue  # 官方資料優先
        changed = (
            abs(row.cash_per_share - ev.cash_per_share) > _EPS
            or abs(row.stock_per_share - ev.stock_per_share) > _EPS
            or (ev.pay_date is not None and row.pay_date != ev.pay_date)
            or row.source != ev.source
        )
        if changed:
            row.cash_per_share = ev.cash_per_share
            row.stock_per_share = ev.stock_per_share
            if ev.pay_date is not None:
                row.pay_date = ev.pay_date
            row.source = ev.source
            row.updated_at = now
            count += 1
    db.flush()
    return count


async def _fetch_events(keys: set[tuple[str, str]], source: data_source.DataSource | None = None) -> list[DividendData]:
    out: list[DividendData] = []
    tw_markets = {m for m, _ in keys if m in ("TW", "TWO")}
    if tw_markets:
        async with new_client(BULK_TIMEOUT) as client:
            for code, fetch in (("TW", twse.fetch_twse_dividends), ("TWO", twse.fetch_tpex_dividends)):
                if code not in tw_markets:
                    continue
                try:
                    out.extend(ev for ev in await fetch(client) if (ev.market, ev.symbol) in keys)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("securities: %s official dividend fetch failed err=%s", code, exc)

    sem = asyncio.Semaphore(_CONCURRENCY)
    async with new_client() as client:

        async def _one(key: tuple[str, str]) -> None:
            async with sem:
                try:
                    out.extend(await data_source.fetch_dividends(source or data_source.FREE, key[0], key[1], client))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("securities: yahoo dividend fetch failed %s:%s err=%s", key[0], key[1], exc)

        await asyncio.gather(*(_one(k) for k in sorted(keys) if markets.get_market(k[0])))
    # 官方來源排前面:同一事件官方先寫入,Yahoo 那筆會被跳過。
    return sorted(out, key=lambda ev: ev.source not in _OFFICIAL_SOURCES)


def sync_dividend_events(db: Session, *, now: datetime | None = None,
                         events: list[DividendData] | None = None) -> dict:
    """`security_dividend_sync` job 本體。`events` 給測試直接注入(不打網路)。"""
    now = now or datetime.now(timezone.utc)
    names = traded_keys(db)
    if not names:
        return {"symbols": 0, "events": 0}
    if events is None:
        events = asyncio.run(_fetch_events(set(names), data_source.load_source(db)))
    changed = upsert_events(db, events, names=names, now=now)
    db.commit()
    return {"symbols": len(names), "events": changed}


# ---------------------------------------------------------------------------
# 待確認股利偵測
# ---------------------------------------------------------------------------


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def trade_local_date(raw: datetime | str | None, market: markets.Market) -> date | None:
    """交易日期換成該市場當地日期再跟除息日比(App/Web 存的是使用者當地
    時間轉 UTC,直接取 UTC 日期會差一天)。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return _aware(raw).astimezone(market.zone).date()


def _trade_row(t: ReadStockTradeProjection) -> holdings_service.TradeRow:
    return holdings_service.TradeRow(
        sync_id=t.sync_id, account_id=t.account_sync_id, market=t.market, symbol=t.symbol,
        trade_type=t.trade_type, shares=float(t.shares or 0), price=t.price,
        fee=float(t.fee or 0), tax=float(t.tax or 0), amount=float(t.amount or 0),
        trade_date=t.trade_date, security_name=t.security_name, currency=t.currency,
    )


@dataclass
class _Entitlement:
    user_id: str
    account_id: str
    ledger_id: str
    shares: float
    security_name: str | None
    currency: str | None
    ref_trade_ids: set[str]


def _entitlements(trades: list[ReadStockTradeProjection], *, market: markets.Market, ex_date: date,
                  ref: str) -> list[_Entitlement]:
    by_account: dict[tuple[str, str], list[ReadStockTradeProjection]] = {}
    for t in trades:
        if t.account_sync_id:
            by_account.setdefault((t.user_id, t.account_sync_id), []).append(t)
    out: list[_Entitlement] = []
    for (user_id, account_id), rows in by_account.items():
        before = [
            _trade_row(t) for t in rows
            if (d := trade_local_date(t.trade_date, market)) is not None and d < ex_date
        ]
        shares = holdings_service.held_shares(
            before, account_id=account_id, market=market.code, symbol=rows[0].symbol,
        )
        latest = max(rows, key=lambda t: _aware(t.trade_date) if t.trade_date else datetime.min.replace(tzinfo=timezone.utc))
        name = next((t.security_name for t in sorted(rows, key=lambda t: t.sync_id, reverse=True) if t.security_name), None)
        out.append(_Entitlement(
            user_id=user_id, account_id=account_id, ledger_id=latest.ledger_id, shares=shares,
            security_name=name, currency=latest.currency,
            ref_trade_ids={t.sync_id for t in rows if t.dividend_event_ref == ref},
        ))
    return out


def _settings_for(db: Session, user_id: str, account_id: str) -> dict[str, Any]:
    raw = db.scalar(
        select(UserAccountProjection.investment_settings_json).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    return parse_settings(raw)


def _fmt_amount(value: float, currency: str | None) -> str:
    if (currency or "").upper() in _ZERO_DECIMAL_CURRENCIES:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _notify(db: Session, pending: PendingDividend, ex_date: date) -> None:
    label = " ".join(p for p in (pending.symbol, pending.security_name) if p)
    parts = [f"除息日 {ex_date.isoformat()}，持有 {pending.shares:g} 股"]
    if pending.est_net > 0:
        parts.append(f"預估實收 {_fmt_amount(pending.est_net, pending.currency)} {pending.currency or ''}".rstrip())
    if pending.est_stock_shares > 0:
        parts.append(f"配股 {pending.est_stock_shares:g} 股")
    notification_service.create_notification(
        db,
        user_id=pending.user_id,
        category="dividend",
        title=f"股利待確認：{label}",
        body="，".join(parts) + "。",
        payload={
            "pendingDividendId": pending.id,
            "accountId": pending.account_sync_id,
            "market": pending.market,
            "symbol": pending.symbol,
            "ledgerId": notification_service.resolve_ledger_external_id(db, pending.ledger_id),
        },
    )


def detect_pending_dividends(db: Session, *, now: datetime | None = None) -> dict:
    """`security_dividend_detector` job 本體。"""
    now = now or datetime.now(timezone.utc)
    earliest = (now - timedelta(days=DETECT_LOOKBACK_DAYS + 1)).date()
    rows = db.execute(
        select(SecurityDividendEvent, Security)
        .join(Security, Security.id == SecurityDividendEvent.security_id)
        .where(SecurityDividendEvent.ex_date >= earliest)
    ).all()
    created = updated = removed = reopened = 0
    for event, sec in rows:
        market = markets.get_market(sec.market)
        if market is None:
            continue
        today = markets.local_now(market, now).date()
        if event.ex_date > today or event.ex_date < today - timedelta(days=DETECT_LOOKBACK_DAYS):
            continue
        if event.cash_per_share <= 0 and event.stock_per_share <= 0:
            continue  # 預告表還沒公告金額
        trades = db.scalars(
            select(ReadStockTradeProjection).where(
                ReadStockTradeProjection.market == sec.market,
                ReadStockTradeProjection.symbol == sec.symbol,
            )
        ).all()
        if not trades:
            continue
        ref = event_ref(sec.market, sec.symbol, event.ex_date)
        existing = {
            p.account_sync_id: p
            for p in db.scalars(select(PendingDividend).where(PendingDividend.event_id == event.id)).all()
        }
        for ent in _entitlements(list(trades), market=market, ex_date=event.ex_date, ref=ref):
            pending = existing.get(ent.account_id)
            if pending is not None and pending.user_id != ent.user_id:
                continue
            if pending is not None and pending.status == "confirmed":
                # 確認時建的明細都被刪掉了 → 退回待確認(不重發通知)。
                if ent.ref_trade_ids:
                    continue
                pending.status = "pending"
                pending.resolved_at = None
                pending.created_trade_ids = None
                reopened += 1
            if pending is not None and pending.status == "dismissed":
                continue
            if ent.shares <= _EPS:
                if pending is not None and pending.status == "pending":
                    db.delete(pending)
                    removed += 1
                continue
            est = estimate_dividend(
                market=sec.market, currency=event.currency or sec.currency, shares=ent.shares,
                cash_per_share=event.cash_per_share, stock_per_share=event.stock_per_share,
                settings=_settings_for(db, ent.user_id, ent.account_id),
            )
            fields = {
                "ledger_id": ent.ledger_id,
                "security_name": ent.security_name or sec.name or None,
                "currency": (event.currency or sec.currency or ent.currency),
                "shares": ent.shares,
                "est_gross": est.gross,
                "est_fee": est.fee,
                "est_tax": est.tax,
                "est_net": est.net,
                "est_stock_shares": est.stock_shares,
            }
            if pending is None:
                pending = PendingDividend(
                    user_id=ent.user_id, account_sync_id=ent.account_id, event_id=event.id,
                    market=sec.market, symbol=sec.symbol, status="pending", created_at=now,
                    updated_at=now, **fields,
                )
                db.add(pending)
                db.flush()
                _notify(db, pending, event.ex_date)
                created += 1
            else:
                dirty = False
                for key, value in fields.items():
                    old = getattr(pending, key)
                    if (isinstance(value, float) and abs((old or 0.0) - value) > _EPS) or (
                        not isinstance(value, float) and old != value
                    ):
                        setattr(pending, key, value)
                        dirty = True
                if dirty:
                    pending.updated_at = now
                    updated += 1
    db.commit()
    return {"created": created, "updated": updated, "removed": removed, "reopened": reopened}


# ---------------------------------------------------------------------------
# API 輸出
# ---------------------------------------------------------------------------


def default_receiving_account(db: Session, *, user_id: str, account_id: str,
                              settings: dict[str, Any] | None = None) -> str | None:
    """股利入帳帳戶預設值:費用設定的交割帳戶 → 這個投資帳戶最近一筆買賣用的
    交割帳戶(同 App `StockTradeEditorPage` 的預帶順序)。"""
    configured = (settings or {}).get("settlementAccountId")
    if configured:
        return str(configured)
    rows = db.execute(
        select(ReadStockTradeProjection.trade_type, ReadTxProjection.from_account_sync_id,
               ReadTxProjection.to_account_sync_id)
        .join(ReadTxProjection, (ReadTxProjection.ledger_id == ReadStockTradeProjection.ledger_id)
              & (ReadTxProjection.sync_id == ReadStockTradeProjection.tx_sync_id))
        .where(
            ReadStockTradeProjection.user_id == user_id,
            ReadStockTradeProjection.account_sync_id == account_id,
            ReadStockTradeProjection.trade_type.in_(("buy", "sell")),
        )
        .order_by(ReadStockTradeProjection.trade_date.desc())
        .limit(1)
    ).first()
    if rows is None:
        return None
    trade_type, from_id, to_id = rows
    return from_id if trade_type == "buy" else to_id


def serialize_pending(
    pending: PendingDividend, event: SecurityDividendEvent, *, ledger_external_id: str | None,
    account: UserAccountProjection | None, db: Session | None = None,
) -> dict[str, Any]:
    settings = parse_settings(account.investment_settings_json if account else None)
    receiving = settings.get("settlementAccountId")
    if not receiving and db is not None:
        receiving = default_receiving_account(db, user_id=pending.user_id, account_id=pending.account_sync_id)
    return {
        "id": pending.id,
        "ledger_id": ledger_external_id,
        "account_id": pending.account_sync_id,
        "account_name": account.name if account else None,
        "account_currency": account.currency if account else None,
        "market": pending.market,
        "symbol": pending.symbol,
        "security_name": pending.security_name,
        "currency": pending.currency,
        "ex_date": event.ex_date.isoformat(),
        "pay_date": event.pay_date.isoformat() if event.pay_date else None,
        "cash_per_share": event.cash_per_share,
        "stock_per_share": event.stock_per_share,
        "shares": pending.shares,
        "est_gross": pending.est_gross,
        "est_fee": pending.est_fee,
        "est_tax": pending.est_tax,
        "est_net": pending.est_net,
        "est_stock_shares": pending.est_stock_shares,
        "status": pending.status,
        "reinvest_default": bool(settings.get("reinvestDividends")),
        "settlement_account_id": receiving,
        "event_ref": event_ref(pending.market, pending.symbol, event.ex_date),
        "created_at": _aware(pending.created_at).isoformat() if pending.created_at else None,
    }
