"""股票持股讀端點(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。

- `GET /securities/search`:證券搜尋(台股官方清單 + Yahoo)。
- `GET /securities/quotes`:報價(盤中快取超過 15 分鐘會補抓)。
- `GET /workspace/holdings`:目前使用者所有投資理財帳戶的持股、市值、損益,
  並折算成主幣別——Web「投資市值(預估)」卡與持股分頁用。App 端自己用
  本地 stock_trade 算持股,只跟 server 拿報價。
- `GET /workspace/realized-pnl`:已實現損益報表(Phase 3,每筆賣出一列 + 各幣別彙總)。
- `GET /ledgers/{id}/stock-trades`:交易明細列表。
- `GET /securities/pending-dividends`:待確認股利(Phase 2)。
- `GET /securities/dividend-events`:某檔的除權息事件(持股詳情顯示用)。
"""
from __future__ import annotations

import re

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database import get_db
from ...deps import get_current_user
from ...models import (
    Ledger,
    PendingDividend,
    ReadStockTradeProjection,
    Security,
    SecurityDividendEvent,
    SecurityQuote,
    User,
    UserAccountProjection,
    UserExchangeRateProjection,
    UserProfile,
)
from ...services.exchange_rate import fetcher as exchange_rate_fetcher
from ...services.securities import dividends as dividend_service
from ...services.securities import holdings as holdings_service
from ...services.securities import markets, trade_fees
from ...services.securities import quotes as quote_service
from ...services.securities import search as search_service
from ._shared import (
    _READ_SCOPE_DEP,
    _analytics_range,
    _is_admin,
    _require_ledger,
    _visible_workspace_ledgers,
    router,
)

_SYMBOL_KEY = re.compile(r"^[A-Za-z]{2,4}:[A-Za-z0-9.\-]{1,24}$")
_MAX_QUOTE_KEYS = 100


class SecuritySearchItemOut(BaseModel):
    market: str
    symbol: str
    name: str
    currency: str
    kind: str


class SecurityQuoteOut(BaseModel):
    market: str
    symbol: str
    name: str | None = None
    currency: str | None = None
    price: float | None = None
    prev_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    quote_time: str | None = None
    session: str | None = None
    source: str | None = None
    fetched_at: str | None = None
    stale: bool = False


class HoldingOut(BaseModel):
    account_id: str | None
    market: str
    symbol: str
    security_name: str | None = None
    currency: str | None = None
    shares: float
    total_cost: float
    avg_cost: float
    realized_pnl: float
    dividends: float
    trade_count: int
    first_trade_date: str | None = None
    last_trade_date: str | None = None
    quote: SecurityQuoteOut | None = None
    market_value: float | None = None
    # 現在全部賣掉的預估手續費/交易稅與淨額(trade_fees.estimate_sell)。
    est_sell_fee: float | None = None
    est_sell_tax: float | None = None
    net_value: float | None = None
    # true = unrealized_pnl 是用 net_value 算的(帳戶設定 pnlAfterSellCosts,預設開)。
    pnl_after_sell_costs: bool = True
    unrealized_pnl: float | None = None
    unrealized_pnl_percent: float | None = None


class AccountHoldingsOut(BaseModel):
    account_id: str
    account_name: str
    currency: str | None = None
    include_in_total: bool = True
    holdings: list[HoldingOut]
    # 以下都以「證券幣別」計;一個帳戶裡混多種幣別時 by_currency 分開列。
    market_value_by_currency: dict[str, float]
    net_value_by_currency: dict[str, float] = {}
    # 算未實現損益用的價值(依設定取 net_value 或 market_value)。
    valuation_by_currency: dict[str, float] = {}
    cost_by_currency: dict[str, float]
    realized_pnl_by_currency: dict[str, float]


class HoldingsSummaryOut(BaseModel):
    base_currency: str | None
    accounts: list[AccountHoldingsOut]
    # 折算成主幣別的總計;缺匯率的幣別整條剔除(列在 missing_rates),不按 1.0 裸加。
    total_market_value: float
    total_net_value: float = 0.0
    total_cost: float
    total_unrealized_pnl: float
    # 有任何帳戶的未實現損益扣了預估賣出費用(畫面要標示)。
    pnl_after_sell_costs: bool = False
    missing_rates: list[str]
    stale: bool


class StockTradeOut(BaseModel):
    id: str
    account_id: str | None
    market: str
    symbol: str
    security_name: str | None = None
    trade_type: str
    shares: float
    price: float | None = None
    fee: float
    tax: float
    amount: float
    currency: str | None = None
    trade_date: str | None = None
    tx_id: str | None = None
    dividend_event_ref: str | None = None
    note: str | None = None


def _parse_keys(raw: str) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not _SYMBOL_KEY.match(part):
            raise HTTPException(status_code=422, detail=f"invalid symbol key: {part}")
        market, symbol = part.split(":", 1)
        if markets.get_market(market) is None:
            raise HTTPException(status_code=422, detail=f"unsupported market: {market}")
        keys.append((market.upper(), symbol.upper()))
    if len(keys) > _MAX_QUOTE_KEYS:
        raise HTTPException(status_code=422, detail=f"too many symbols (max {_MAX_QUOTE_KEYS})")
    return keys


@router.get("/securities/search", response_model=list[SecuritySearchItemOut])
async def search_securities(
    q: str = Query(min_length=1, max_length=64),
    market: str | None = Query(default=None, pattern=r"^[A-Za-z]{2,4}$"),
    limit: int = Query(default=20, ge=1, le=50),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SecuritySearchItemOut]:
    if market and markets.get_market(market) is None:
        raise HTTPException(status_code=422, detail=f"unsupported market: {market}")
    rows = await search_service.search_securities(db, q, market=market, limit=limit)
    return [SecuritySearchItemOut(**r) for r in rows]


@router.get("/securities/quotes", response_model=list[SecurityQuoteOut])
async def get_security_quotes(
    symbols: str = Query(min_length=1, max_length=4000, description="MARKET:SYMBOL,逗號分隔,例 TW:2330,US:AAPL"),
    refresh: bool = Query(default=True),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SecurityQuoteOut]:
    keys = _parse_keys(symbols)
    views = await quote_service.get_quotes(db, keys, refresh=refresh)
    return [SecurityQuoteOut(**v.to_dict()) for v in views]


async def _rates_to_base(db: Session, *, user_id: str, base: str) -> dict[str, float]:
    """各幣別 → base 的匯率(1 X = rate base),同 workspace 淨資產卡口徑:
    自動匯率(1 base = x quote)取倒數,使用者手動 override 覆蓋。"""
    rates: dict[str, float] = {base: 1.0}
    try:
        row, _stale = await exchange_rate_fetcher.get_rates(db, base)
        for q, x in (row.payload_json or {}).items():
            try:
                xf = float(x)
            except (TypeError, ValueError):
                continue
            if xf > 0:
                rates[q.upper()] = 1.0 / xf
    except RuntimeError:
        pass
    for quote_ccy, rate in db.execute(
        select(UserExchangeRateProjection.quote_currency, UserExchangeRateProjection.rate).where(
            UserExchangeRateProjection.user_id == user_id,
            UserExchangeRateProjection.base_currency == base,
        )
    ).all():
        try:
            r = float(rate)
        except (TypeError, ValueError):
            continue
        if r > 0:
            rates[quote_ccy.upper()] = r
    return rates


@router.get("/workspace/holdings", response_model=HoldingsSummaryOut)
async def workspace_holdings(
    account_id: str | None = Query(default=None),
    refresh: bool = Query(default=True),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HoldingsSummaryOut:
    user_id = current_user.id
    acct_stmt = select(UserAccountProjection).where(
        UserAccountProjection.user_id == user_id,
        UserAccountProjection.account_type == "investment",
    )
    if account_id:
        acct_stmt = acct_stmt.where(UserAccountProjection.sync_id == account_id)
    accounts = {a.sync_id: a for a in db.scalars(acct_stmt).all()}

    trade_stmt = select(ReadStockTradeProjection).where(ReadStockTradeProjection.user_id == user_id)
    if account_id:
        trade_stmt = trade_stmt.where(ReadStockTradeProjection.account_sync_id == account_id)
    rows = [
        holdings_service.TradeRow(
            sync_id=t.sync_id,
            account_id=t.account_sync_id,
            market=t.market,
            symbol=t.symbol,
            trade_type=t.trade_type,
            shares=float(t.shares or 0),
            price=t.price,
            fee=float(t.fee or 0),
            tax=float(t.tax or 0),
            amount=float(t.amount or 0),
            trade_date=t.trade_date,
            security_name=t.security_name,
            currency=t.currency,
        )
        for t in db.scalars(trade_stmt).all()
    ]
    all_holdings = [
        h for h in holdings_service.compute_holdings(rows, include_closed=True) if h.account_id in accounts
    ]
    base = (
        db.scalar(select(UserProfile.primary_currency).where(UserProfile.user_id == user_id)) or ""
    ).upper()
    account_meta = {
        sid: (a.name or "", (a.currency or "").upper() or None, a.include_in_total is not False)
        for sid, a in accounts.items()
    }
    account_settings = {
        sid: dividend_service.parse_settings(a.investment_settings_json) for sid, a in accounts.items()
    }

    open_keys = list(dict.fromkeys((h.market, h.symbol) for h in all_holdings if h.shares > 0))
    views = await quote_service.get_quotes(db, open_keys, refresh=refresh) if open_keys else []
    quote_by_key = {(v.market, v.symbol): v for v in views}
    any_stale = any(v.stale for v in views)

    currencies_needed: set[str] = set()
    any_after_costs = False
    by_account: dict[str, AccountHoldingsOut] = {}
    for sid, (name, ccy, include) in account_meta.items():
        by_account[sid] = AccountHoldingsOut(
            account_id=sid, account_name=name, currency=ccy, include_in_total=include,
            holdings=[], market_value_by_currency={}, cost_by_currency={}, realized_pnl_by_currency={},
        )

    for h in all_holdings:
        view = quote_by_key.get((h.market, h.symbol))
        ccy = (h.currency or (view.currency if view else None) or account_meta[h.account_id][1] or "").upper()
        market_value = None
        estimate = None
        valuation = None
        unrealized = None
        unrealized_pct = None
        settings = account_settings.get(h.account_id)
        after_costs = trade_fees.pnl_after_sell_costs(settings)
        if h.shares > 0 and view is not None and view.price is not None:
            # 毛市值依幣別取整(台幣無條件捨去),同成交價金與 App HoldingView.marketValue。
            market_value = trade_fees.stock_gross(h.shares, view.price, ccy or None)
            estimate = trade_fees.estimate_sell(
                shares=h.shares, price=view.price, market=h.market, symbol=h.symbol,
                currency=ccy or None, settings=settings,
            )
            valuation = round(estimate.net, 6) if after_costs else market_value
            unrealized = round(valuation - h.total_cost, 6)
            unrealized_pct = round(unrealized / h.total_cost * 100, 4) if h.total_cost > 0 else None
        d = h.to_dict()
        out = HoldingOut(
            account_id=h.account_id,
            market=h.market,
            symbol=h.symbol,
            security_name=h.security_name or (view.name if view else None),
            currency=ccy or None,
            shares=d["shares"],
            total_cost=d["totalCost"],
            avg_cost=d["avgCost"],
            realized_pnl=d["realizedPnl"],
            dividends=d["dividends"],
            trade_count=h.trade_count,
            first_trade_date=h.first_trade_date,
            last_trade_date=h.last_trade_date,
            quote=SecurityQuoteOut(**view.to_dict()) if view is not None and h.shares > 0 else None,
            market_value=market_value,
            est_sell_fee=estimate.fee if estimate else None,
            est_sell_tax=estimate.tax if estimate else None,
            net_value=round(estimate.net, 6) if estimate else None,
            pnl_after_sell_costs=after_costs,
            unrealized_pnl=unrealized,
            unrealized_pnl_percent=unrealized_pct,
        )
        acc = by_account[h.account_id]
        acc.holdings.append(out)
        if ccy:
            currencies_needed.add(ccy)
            if h.shares > 0:
                acc.cost_by_currency[ccy] = round(acc.cost_by_currency.get(ccy, 0.0) + h.total_cost, 6)
                if market_value is not None:
                    acc.market_value_by_currency[ccy] = round(
                        acc.market_value_by_currency.get(ccy, 0.0) + market_value, 6
                    )
                    acc.net_value_by_currency[ccy] = round(
                        acc.net_value_by_currency.get(ccy, 0.0) + (estimate.net if estimate else market_value), 6
                    )
                    acc.valuation_by_currency[ccy] = round(
                        acc.valuation_by_currency.get(ccy, 0.0) + (valuation if valuation is not None else market_value),
                        6,
                    )
                    if after_costs:
                        any_after_costs = True
            if h.realized_pnl:
                acc.realized_pnl_by_currency[ccy] = round(
                    acc.realized_pnl_by_currency.get(ccy, 0.0) + h.realized_pnl, 6
                )

    if not base and len(currencies_needed) == 1:
        base = next(iter(currencies_needed))
    rates = await _rates_to_base(db, user_id=user_id, base=base) if base else {}
    missing: set[str] = set()
    total_mv = 0.0
    total_net = 0.0
    total_valuation = 0.0
    total_cost = 0.0
    for acc in by_account.values():
        for ccy, mv in acc.market_value_by_currency.items():
            rate = rates.get(ccy)
            if rate is None:
                missing.add(ccy)
                continue
            total_mv += mv * rate
            total_net += acc.net_value_by_currency.get(ccy, mv) * rate
            total_valuation += acc.valuation_by_currency.get(ccy, mv) * rate
            total_cost += acc.cost_by_currency.get(ccy, 0.0) * rate

    ordered = sorted(by_account.values(), key=lambda a: a.account_name.lower())
    return HoldingsSummaryOut(
        base_currency=base or None,
        accounts=[a for a in ordered if a.holdings or not account_id or a.account_id == account_id],
        total_market_value=round(total_mv, 4),
        total_net_value=round(total_net, 4),
        total_cost=round(total_cost, 4),
        total_unrealized_pnl=round(total_valuation - total_cost, 4),
        pnl_after_sell_costs=any_after_costs,
        missing_rates=sorted(missing),
        stale=any_stale,
    )


class RealizedEventOut(BaseModel):
    trade_id: str
    account_id: str | None
    market: str
    symbol: str
    security_name: str | None = None
    currency: str | None = None
    date: str | None = None
    shares: float
    proceeds: float
    cost_basis: float
    pnl: float


class RealizedSymbolOut(BaseModel):
    market: str
    symbol: str
    security_name: str | None = None
    currency: str | None = None
    pnl: float
    proceeds: float
    cost_basis: float
    sell_count: int
    events: list[RealizedEventOut]


class RealizedPnlOut(BaseModel):
    year: int | None = None
    # 有賣出紀錄的年份(新到舊),給前端年度篩選用。
    years: list[int]
    # 以下都依證券幣別分開,不跨幣別加總(同持股頁口徑)。
    realized_pnl_by_currency: dict[str, float]
    dividends_by_currency: dict[str, float]
    symbols: list[RealizedSymbolOut]


def _load_user_trade_rows(db: Session, user_id: str, account_id: str | None = None) -> list[holdings_service.TradeRow]:
    stmt = select(ReadStockTradeProjection).where(ReadStockTradeProjection.user_id == user_id)
    if account_id:
        stmt = stmt.where(ReadStockTradeProjection.account_sync_id == account_id)
    return [
        holdings_service.TradeRow(
            sync_id=t.sync_id, account_id=t.account_sync_id, market=t.market, symbol=t.symbol,
            trade_type=t.trade_type, shares=float(t.shares or 0), price=t.price,
            fee=float(t.fee or 0), tax=float(t.tax or 0), amount=float(t.amount or 0),
            trade_date=t.trade_date, security_name=t.security_name, currency=t.currency,
        )
        for t in db.scalars(stmt).all()
    ]


def build_realized_pnl(
    rows: list[holdings_service.TradeRow],
    *,
    account_ids: set[str] | None = None,
    account_currency: dict[str, str | None] | None = None,
    year: int | None = None,
    symbol: str | None = None,
) -> RealizedPnlOut:
    """已實現損益彙總。損益一律用「全部歷史」算出每筆賣出的成本,再依
    年度/標的/帳戶過濾(不能先過濾再算,移動平均成本會跑掉)。"""
    events: list[holdings_service.RealizedEvent] = []
    holdings_service.compute_holdings(rows, include_closed=True, events=events)
    account_currency = account_currency or {}
    sym_filter = symbol.upper() if symbol else None
    if sym_filter and ":" in sym_filter:
        sym_filter = sym_filter.split(":", 1)[1]

    def _ccy(market: str, ccy: str | None, account_id: str | None) -> str:
        return (ccy or account_currency.get(account_id or "") or "").upper()

    def _keep(account_id: str | None, sym: str, date: str | None) -> bool:
        if account_ids is not None and account_id not in account_ids:
            return False
        if sym_filter and sym != sym_filter:
            return False
        if year is not None and not (date and date[:4] == str(year)):
            return False
        return True

    years = sorted({int(e.date[:4]) for e in events if e.date and e.date[:4].isdigit()
                    and (account_ids is None or e.account_id in account_ids)}, reverse=True)
    totals: dict[str, float] = {}
    groups: dict[tuple[str, str], RealizedSymbolOut] = {}
    for e in events:
        if not _keep(e.account_id, e.symbol, e.date):
            continue
        ccy = _ccy(e.market, e.currency, e.account_id)
        out = RealizedEventOut(
            trade_id=e.trade_sync_id, account_id=e.account_id, market=e.market, symbol=e.symbol,
            security_name=e.security_name, currency=ccy or None, date=e.date,
            shares=round(e.shares, 6), proceeds=round(e.proceeds, 6),
            cost_basis=round(e.cost_basis, 6), pnl=round(e.pnl, 6),
        )
        totals[ccy] = totals.get(ccy, 0.0) + e.pnl
        g = groups.get((e.market, e.symbol))
        if g is None:
            g = RealizedSymbolOut(
                market=e.market, symbol=e.symbol, security_name=e.security_name, currency=ccy or None,
                pnl=0.0, proceeds=0.0, cost_basis=0.0, sell_count=0, events=[],
            )
            groups[(e.market, e.symbol)] = g
        g.pnl = round(g.pnl + e.pnl, 6)
        g.proceeds = round(g.proceeds + e.proceeds, 6)
        g.cost_basis = round(g.cost_basis + e.cost_basis, 6)
        g.sell_count += 1
        g.events.append(out)
    dividends: dict[str, float] = {}
    for t in rows:
        if t.trade_type not in ("cash_dividend", "reinvest"):
            continue
        date = holdings_service._date_key(t.trade_date) or None
        if not _keep(t.account_id, t.symbol, date):
            continue
        ccy = _ccy(t.market, t.currency, t.account_id)
        dividends[ccy] = dividends.get(ccy, 0.0) + t.amount
    for g in groups.values():
        g.events.sort(key=lambda x: (x.date or "", x.trade_id), reverse=True)
    return RealizedPnlOut(
        year=year,
        years=years,
        realized_pnl_by_currency={k: round(v, 6) for k, v in totals.items()},
        dividends_by_currency={k: round(v, 6) for k, v in dividends.items()},
        symbols=sorted(groups.values(), key=lambda g: -abs(g.pnl)),
    )


@router.get("/workspace/realized-pnl", response_model=RealizedPnlOut)
def workspace_realized_pnl(
    account_id: str | None = Query(default=None),
    year: int | None = Query(default=None, ge=1990, le=2200),
    symbol: str | None = Query(default=None, max_length=40),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RealizedPnlOut:
    user_id = current_user.id
    accounts = {
        a.sync_id: a
        for a in db.scalars(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.account_type == "investment",
            )
        ).all()
    }
    if account_id and account_id not in accounts:
        raise HTTPException(status_code=404, detail="Account not found")
    rows = _load_user_trade_rows(db, user_id)
    return build_realized_pnl(
        rows,
        account_ids={account_id} if account_id else set(accounts),
        account_currency={sid: (a.currency or "").upper() or None for sid, a in accounts.items()},
        year=year,
        symbol=symbol,
    )


class InvestmentFlowCurrencyOut(BaseModel):
    currency: str
    # 買進現金流出(含手續費)/ 賣出淨收入(已扣手續費+交易稅),證券幣別。
    buy_amount: float
    sell_amount: float
    # buy_amount − sell_amount:這段期間「從現金轉進投資」的淨額(可為負)。
    # 這是資金轉移,不是支出,也不計入收入/結餘。
    net_invested: float
    # 期間內手續費、交易稅合計(買+賣)。已含在 buy/sell_amount 內,另列只是讓
    # 使用者看到成本;它們不會進收入/支出(轉帳的 feeAmount/discountAmount 本來
    # 就只影響帳戶餘額,不進收支統計)。
    fees: float
    taxes: float
    # 現金股利 + 股利再投入(已是收入交易,算在首頁收入內)。
    dividends: float
    buy_count: int
    sell_count: int


class InvestmentFlowOut(BaseModel):
    scope: str
    period: str | None = None
    by_currency: list[InvestmentFlowCurrencyOut]


@router.get("/workspace/investment-flow", response_model=InvestmentFlowOut)
def workspace_investment_flow(
    scope: str = Query(default="month", pattern="^(month|year|all)$"),
    period: str | None = Query(default=None),
    ledger_id: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvestmentFlowOut:
    """首頁「投資淨投入」:買進/賣出/手續費稅/股利,各幣別分開。

    期間口徑跟 `/workspace/analytics` 完全相同(同一個 `_analytics_range`,單帳本
    用該帳本的月份起始日,多帳本維持自然月),所以跟首頁其它卡片的「本月」一致。
    買進/賣出是轉帳到投資帳戶,不是支出/收入;期初持股、股票股利、分割不是現金
    流動,不列入。"""
    is_admin = _is_admin(current_user)
    ledgers = _visible_workspace_ledgers(
        db, current_user=current_user, is_admin=is_admin, ledger_id=ledger_id,
    )
    month_start_day = (ledgers[0].month_start_day or 1) if len(ledgers) == 1 else 1
    start_at, end_at, normalized = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,  # type: ignore[arg-type]
        month_start_day=month_start_day,
    )
    stmt = select(ReadStockTradeProjection).where(
        ReadStockTradeProjection.user_id == current_user.id,
        ReadStockTradeProjection.trade_type.in_(("buy", "sell", "cash_dividend", "reinvest")),
    )
    if ledger_id:
        stmt = stmt.where(ReadStockTradeProjection.ledger_id.in_([lg.id for lg in ledgers] or [""]))
    if start_at is not None and end_at is not None:
        stmt = stmt.where(
            ReadStockTradeProjection.trade_date >= start_at,
            ReadStockTradeProjection.trade_date < end_at,
        )
    acct_currency = {
        a.sync_id: (a.currency or "").upper()
        for a in db.scalars(
            select(UserAccountProjection).where(UserAccountProjection.user_id == current_user.id)
        ).all()
    }
    agg: dict[str, dict[str, float]] = {}
    for t in db.scalars(stmt).all():
        cur = (t.currency or acct_currency.get(t.account_sync_id or "") or "").upper()
        a = agg.setdefault(
            cur,
            {"buy": 0.0, "sell": 0.0, "fees": 0.0, "taxes": 0.0, "div": 0.0, "bc": 0, "sc": 0},
        )
        amount = float(t.amount or 0)
        if t.trade_type == "buy":
            a["buy"] += amount
            a["bc"] += 1
        elif t.trade_type == "sell":
            a["sell"] += amount
            a["sc"] += 1
        else:
            a["div"] += amount
            continue
        a["fees"] += float(t.fee or 0)
        a["taxes"] += float(t.tax or 0)
    out = [
        InvestmentFlowCurrencyOut(
            currency=cur,
            buy_amount=round(v["buy"], 2),
            sell_amount=round(v["sell"], 2),
            net_invested=round(v["buy"] - v["sell"], 2),
            fees=round(v["fees"], 2),
            taxes=round(v["taxes"], 2),
            dividends=round(v["div"], 2),
            buy_count=int(v["bc"]),
            sell_count=int(v["sc"]),
        )
        for cur, v in sorted(agg.items())
    ]
    return InvestmentFlowOut(scope=scope, period=normalized, by_currency=out)


@router.get("/ledgers/{ledger_external_id}/stock-trades", response_model=list[StockTradeOut])
def list_stock_trades(
    ledger_external_id: str,
    account_id: str | None = Query(default=None),
    market: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StockTradeOut]:
    ledger, _ = _require_ledger(
        db, user_id=current_user.id, ledger_external_id=ledger_external_id,
        is_admin=_is_admin(current_user),
    )
    stmt = select(ReadStockTradeProjection).where(ReadStockTradeProjection.ledger_id == ledger.id)
    if account_id:
        stmt = stmt.where(ReadStockTradeProjection.account_sync_id == account_id)
    if market:
        stmt = stmt.where(ReadStockTradeProjection.market == market.upper())
    if symbol:
        stmt = stmt.where(ReadStockTradeProjection.symbol == symbol.upper())
    rows = db.scalars(
        stmt.order_by(ReadStockTradeProjection.trade_date.desc(), ReadStockTradeProjection.sync_id.desc())
    ).all()
    return [
        StockTradeOut(
            id=r.sync_id,
            account_id=r.account_sync_id,
            market=r.market,
            symbol=r.symbol,
            security_name=r.security_name,
            trade_type=r.trade_type,
            shares=float(r.shares or 0),
            price=r.price,
            fee=float(r.fee or 0),
            tax=float(r.tax or 0),
            amount=float(r.amount or 0),
            currency=r.currency,
            trade_date=quote_service._iso(r.trade_date),
            tx_id=r.tx_sync_id,
            dividend_event_ref=r.dividend_event_ref,
            note=r.note,
        )
        for r in rows
    ]


class PendingDividendOut(BaseModel):
    id: int
    ledger_id: str | None = None
    account_id: str
    account_name: str | None = None
    account_currency: str | None = None
    market: str
    symbol: str
    security_name: str | None = None
    currency: str | None = None
    ex_date: str
    pay_date: str | None = None
    cash_per_share: float
    stock_per_share: float
    shares: float
    est_gross: float
    est_fee: float
    est_tax: float
    est_net: float
    est_stock_shares: float
    status: str
    reinvest_default: bool = False
    settlement_account_id: str | None = None
    event_ref: str
    created_at: str | None = None
    # 再投入價格預填用(快取報價,不打上游)。
    quote_price: float | None = None


@router.get("/securities/pending-dividends", response_model=list[PendingDividendOut])
def list_pending_dividends(
    status: str = Query(default="pending", pattern=r"^(pending|confirmed|dismissed|all)$"),
    limit: int = Query(default=100, ge=1, le=500),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PendingDividendOut]:
    stmt = (
        select(PendingDividend, SecurityDividendEvent, Ledger.external_id, SecurityQuote.price)
        .join(SecurityDividendEvent, SecurityDividendEvent.id == PendingDividend.event_id)
        .join(Ledger, Ledger.id == PendingDividend.ledger_id)
        .outerjoin(SecurityQuote, SecurityQuote.security_id == SecurityDividendEvent.security_id)
        .where(PendingDividend.user_id == current_user.id)
    )
    if status != "all":
        stmt = stmt.where(PendingDividend.status == status)
    rows = db.execute(
        stmt.order_by(SecurityDividendEvent.ex_date.desc(), PendingDividend.id.desc()).limit(limit)
    ).all()
    account_ids = {p.account_sync_id for p, *_ in rows}
    accounts = {
        a.sync_id: a
        for a in db.scalars(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == current_user.id,
                UserAccountProjection.sync_id.in_(account_ids or {""}),
            )
        ).all()
    }
    out: list[PendingDividendOut] = []
    for pending, event, ledger_external_id, quote_price in rows:
        data = dividend_service.serialize_pending(
            pending, event, ledger_external_id=ledger_external_id,
            account=accounts.get(pending.account_sync_id), db=db,
        )
        out.append(PendingDividendOut(**data, quote_price=quote_price))
    return out


class DividendEventOut(BaseModel):
    market: str
    symbol: str
    ex_date: str
    pay_date: str | None = None
    cash_per_share: float
    stock_per_share: float
    currency: str | None = None
    source: str


@router.get("/securities/dividend-events", response_model=list[DividendEventOut])
def list_dividend_events(
    symbol: str = Query(min_length=3, max_length=40, description="MARKET:SYMBOL"),
    limit: int = Query(default=12, ge=1, le=50),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DividendEventOut]:
    keys = _parse_keys(symbol)
    if len(keys) != 1:
        raise HTTPException(status_code=422, detail="exactly one symbol is required")
    market, sym = keys[0]
    rows = db.execute(
        select(SecurityDividendEvent, Security)
        .join(Security, Security.id == SecurityDividendEvent.security_id)
        .where(Security.market == market, Security.symbol == sym)
        .order_by(SecurityDividendEvent.ex_date.desc())
        .limit(limit)
    ).all()
    return [
        DividendEventOut(
            market=sec.market, symbol=sec.symbol, ex_date=ev.ex_date.isoformat(),
            pay_date=ev.pay_date.isoformat() if ev.pay_date else None,
            cash_per_share=ev.cash_per_share, stock_per_share=ev.stock_per_share,
            currency=ev.currency or sec.currency, source=ev.source,
        )
        for ev, sec in rows
    ]
