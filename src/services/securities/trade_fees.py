"""股票買賣的價金取整、手續費/交易稅試算、預估變現淨值。

App `lib/models/investment_settings.dart` + `lib/services/investment/markets.dart`
(`securityKindOf`)、Web `packages/web-features/src/lib/investment.ts` 是同一套
規則,三端測試用同一組跟券商對帳單核對過的數字(0050 買 50 股 @97.45 手續費 6
→ 總成本 4,878;賣 50 股 @112.40 → ETF 交易稅 5)。改一邊要改另外兩邊。

- 台幣/日圓/韓圜沒有小數:成交價金、手續費、交易稅一律無條件捨去到整數
  (證交所與台灣券商結算慣例);其它幣別四捨五入到分。捨去前先四捨五入到
  小數 6 位,避免 1000 × 600.1 = 600099.99999… 被捨成 600,099。
- 台股證交稅依標的類型:普通股 0.3%、ETF 0.1%、債券 ETF 停徵(0%)。類型只看
  代號(`00` 開頭 = ETF,其中結尾 `B` = 債券 ETF),不查資料庫。

這支模組只用標準函式庫,`snapshot_mutator` 會 import 它。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

ZERO_DECIMAL_CURRENCIES = {"TWD", "JPY", "KRW"}

KIND_STOCK = "stock"
KIND_ETF = "etf"
KIND_BOND_ETF = "bond_etf"

_TW_MARKETS = {"TW", "TWO"}
_TW_ETF_SYMBOL = re.compile(r"^00\d{2,4}[A-Z]?$")

# 各市場預設費率,同 App `InvestmentSettings.defaultsFor`。
_TRADE_DEFAULTS: dict[str, dict[str, float]] = {
    "TW": {"feeRate": 0.001425, "feeDiscount": 1, "feeMin": 20, "sellTaxRate": 0.003,
           "etfSellTaxRate": 0.001, "bondEtfSellTaxRate": 0},
    "US": {"feeRate": 0.0025, "feeDiscount": 1, "feeMin": 0, "sellTaxRate": 0},
}
_TRADE_DEFAULTS["TWO"] = _TRADE_DEFAULTS["TW"]
_OTHER_DEFAULTS = {"feeRate": 0, "feeDiscount": 1, "feeMin": 0, "sellTaxRate": 0}


def security_kind(market: str | None, symbol: str | None) -> str:
    if (market or "").upper() not in _TW_MARKETS:
        return KIND_STOCK
    s = (symbol or "").strip().upper()
    if not _TW_ETF_SYMBOL.match(s):
        return KIND_STOCK
    return KIND_BOND_ETF if s.endswith("B") else KIND_ETF


def round_money(value: float, currency: str | None) -> float:
    cleaned = round(value + 0.0, 6)
    if (currency or "").upper() in ZERO_DECIMAL_CURRENCIES:
        return float(math.floor(cleaned))
    # 四捨五入(不用 Python 內建 round 的銀行家捨入,對齊 Dart/JS)。
    # 放大後再清一次殘渣(30.015 × 100 = 3001.4999…)才四捨五入。
    return math.floor(round(cleaned * 100, 6) + 0.5) / 100


def stock_gross(shares: float, price: float, currency: str | None) -> float:
    """成交價金 = 股數 × 價格,依幣別取整(台幣 4,872.5 → 4,872)。"""
    return round_money(shares * price, currency)


def _num(settings: dict[str, Any] | None, key: str) -> float | None:
    value = (settings or {}).get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def resolve_trade_settings(market: str | None, settings: dict[str, Any] | None) -> dict[str, float]:
    base = dict(_TRADE_DEFAULTS.get((market or "").upper(), _OTHER_DEFAULTS))
    for key in ("feeRate", "feeDiscount", "feeMin", "sellTaxRate", "etfSellTaxRate", "bondEtfSellTaxRate"):
        value = _num(settings, key)
        if value is not None:
            base[key] = value
    return base


def pnl_after_sell_costs(settings: dict[str, Any] | None) -> bool:
    """未實現損益要不要扣預估賣出費用(預設開,使用者關掉才存 false)。"""
    value = (settings or {}).get("pnlAfterSellCosts")
    return value if isinstance(value, bool) else True


def sell_tax_rate_for(market: str | None, symbol: str | None, settings: dict[str, Any] | None) -> float:
    r = resolve_trade_settings(market, settings)
    kind = security_kind(market, symbol)
    if kind == KIND_ETF:
        return r.get("etfSellTaxRate", r.get("sellTaxRate", 0.0))
    if kind == KIND_BOND_ETF:
        return r.get("bondEtfSellTaxRate", 0.0)
    return r.get("sellTaxRate", 0.0)


def suggest_fee(gross: float, market: str | None, currency: str | None, settings: dict[str, Any] | None) -> float:
    if gross <= 0:
        return 0.0
    r = resolve_trade_settings(market, settings)
    return max(round_money(gross * r["feeRate"] * r["feeDiscount"], currency), r["feeMin"])


def suggest_sell_tax(
    gross: float, market: str | None, symbol: str | None, currency: str | None, settings: dict[str, Any] | None,
) -> float:
    if gross <= 0:
        return 0.0
    return round_money(gross * sell_tax_rate_for(market, symbol, settings), currency)


@dataclass
class SellEstimate:
    gross: float
    fee: float
    tax: float

    @property
    def net(self) -> float:
        return max(self.gross - self.fee - self.tax, 0.0)


def estimate_sell(
    *, shares: float, price: float, market: str | None, symbol: str | None,
    currency: str | None, settings: dict[str, Any] | None,
) -> SellEstimate:
    """「現在全部賣掉」的預估手續費/交易稅(庫存的預估變現淨值)。"""
    gross = stock_gross(shares, price, currency)
    if gross <= 0:
        return SellEstimate(0.0, 0.0, 0.0)
    return SellEstimate(
        gross=gross,
        fee=suggest_fee(gross, market, currency, settings),
        tax=suggest_sell_tax(gross, market, symbol, currency, settings),
    )
