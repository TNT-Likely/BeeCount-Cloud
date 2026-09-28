"""由 stock_trade 明細即時彙總持股(移動平均成本法,台灣券商慣例)。

純函式、不碰 DB,方便跟 App 端 lib/services/investment/holdings_calculator.dart
用同一份測試向量(tests/fixtures/stock_holdings_vectors.json)互相驗證——
兩邊算出不同數字時,App 跟 Web 會顯示不同的平均成本/損益。改算法時兩邊
一起改、一起更新向量。

規則:
- 排序:trade_date 升冪 → 同一天依類型(opening, buy, reinvest,
  stock_dividend, cash_dividend, sell)→ sync_id,保證兩端順序一致。
- opening/buy:股數 += s,成本 += amount(amount ≤ 0 時退回 s×price+fee)。
- reinvest:股數 += s,成本 += amount,同時計入累計股利。
- stock_dividend(配股):股數 += s,成本不變(攤低平均成本)。
- cash_dividend:只計入累計股利。
- sell:依賣出當下平均成本扣除成本,已實現損益 += amount − 扣除成本;
  賣超(s > 持有股數)時只扣掉持有的部分,股數歸零。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

EPS = 1e-9

_TYPE_ORDER = {
    "opening": 0,
    "buy": 1,
    "reinvest": 2,
    "stock_dividend": 3,
    "cash_dividend": 4,
    "sell": 5,
}


@dataclass
class TradeRow:
    sync_id: str
    account_id: str | None
    market: str
    symbol: str
    trade_type: str
    shares: float
    price: float | None
    fee: float
    tax: float
    amount: float
    trade_date: datetime | str | None
    security_name: str | None = None
    currency: str | None = None


@dataclass
class Holding:
    account_id: str | None
    market: str
    symbol: str
    security_name: str | None = None
    currency: str | None = None
    shares: float = 0.0
    total_cost: float = 0.0
    realized_pnl: float = 0.0
    dividends: float = 0.0
    trade_count: int = 0
    first_trade_date: str | None = None
    last_trade_date: str | None = None

    @property
    def avg_cost(self) -> float:
        return self.total_cost / self.shares if self.shares > EPS else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "accountId": self.account_id,
            "market": self.market,
            "symbol": self.symbol,
            "securityName": self.security_name,
            "currency": self.currency,
            "shares": _r(self.shares),
            "totalCost": _r(self.total_cost),
            "avgCost": _r(self.avg_cost),
            "realizedPnl": _r(self.realized_pnl),
            "dividends": _r(self.dividends),
            "tradeCount": self.trade_count,
            "firstTradeDate": self.first_trade_date,
            "lastTradeDate": self.last_trade_date,
        }


def _r(v: float) -> float:
    out = round(v, 6)
    return 0.0 if out == 0 else out


def _date_key(raw: datetime | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, datetime):
        return raw.isoformat()[:10]
    return str(raw)[:10]


def trade_row_from_payload(payload: dict[str, Any]) -> TradeRow:
    """snapshot/wire camelCase dict → TradeRow。"""

    def _f(key: str, default: float = 0.0) -> float:
        v = payload.get(key)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    price = payload.get("price")
    return TradeRow(
        sync_id=str(payload.get("syncId") or ""),
        account_id=payload.get("accountId"),
        market=str(payload.get("market") or "").upper(),
        symbol=str(payload.get("symbol") or "").upper(),
        trade_type=str(payload.get("tradeType") or "buy"),
        shares=_f("shares"),
        price=float(price) if price is not None else None,
        fee=_f("fee"),
        tax=_f("tax"),
        amount=_f("amount"),
        trade_date=payload.get("tradeDate"),
        security_name=payload.get("securityName"),
        currency=payload.get("currency"),
    )


def sort_trades(trades: Iterable[TradeRow]) -> list[TradeRow]:
    return sorted(
        trades,
        key=lambda t: (_date_key(t.trade_date), _TYPE_ORDER.get(t.trade_type, 9), t.sync_id),
    )


def compute_holdings(trades: Iterable[TradeRow], *, include_closed: bool = False) -> list[Holding]:
    """回傳每個 (account, market, symbol) 的持股。`include_closed=False` 時
    濾掉股數為 0 的(已全部賣出),但已實現損益仍保留在 include_closed=True
    的結果裡。"""
    book: dict[tuple[str | None, str, str], Holding] = {}
    for t in sort_trades(trades):
        key = (t.account_id, t.market, t.symbol)
        h = book.get(key)
        if h is None:
            h = Holding(account_id=t.account_id, market=t.market, symbol=t.symbol)
            book[key] = h
        if t.security_name:
            h.security_name = t.security_name
        if t.currency:
            h.currency = t.currency.upper()
        h.trade_count += 1
        date_key = _date_key(t.trade_date) or None
        if date_key:
            if h.first_trade_date is None:
                h.first_trade_date = date_key
            h.last_trade_date = date_key

        s = max(t.shares, 0.0)
        if t.trade_type in ("opening", "buy"):
            cost = t.amount if t.amount > 0 else s * (t.price or 0.0) + t.fee
            h.shares += s
            h.total_cost += cost
        elif t.trade_type == "reinvest":
            h.shares += s
            h.total_cost += t.amount
            h.dividends += t.amount
        elif t.trade_type == "stock_dividend":
            h.shares += s
        elif t.trade_type == "cash_dividend":
            h.dividends += t.amount
        elif t.trade_type == "sell":
            sold = min(s, h.shares)
            cost_out = h.avg_cost * sold
            h.realized_pnl += t.amount - cost_out
            h.shares -= sold
            h.total_cost -= cost_out
            if h.shares <= EPS:
                h.shares = 0.0
                h.total_cost = 0.0

    out = list(book.values())
    if not include_closed:
        out = [h for h in out if h.shares > EPS]
    return sorted(out, key=lambda h: (h.account_id or "", h.market, h.symbol))


def held_shares(trades: Iterable[TradeRow], *, account_id: str | None, market: str, symbol: str) -> float:
    for h in compute_holdings(trades, include_closed=True):
        if h.account_id == account_id and h.market == market.upper() and h.symbol == symbol.upper():
            return h.shares
    return 0.0
