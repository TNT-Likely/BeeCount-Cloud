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


# 股票定期定額「只買整數股」的市場(2026-09-30 使用者回報)。台股不論定期定額
# 或盤中零股都要進證交所撮合,最小交易單位就是 1 股,券商沒辦法把 0.5 股放進
# 集保戶頭——每期投入金額(含手續費)能買幾個整股就買幾股,剩下的錢不扣款。
# 美股等市場的券商(含複委託)是券商自己吃下整股再切碎分配,允許碎股,維持
# 「投入金額 ÷ 股價」。App `kStockDcaWholeShareMarkets`
# (lib/services/investment/stock_dca.dart)、Web `STOCK_DCA_WHOLE_SHARE_MARKETS`
# 必須同一份清單。
STOCK_DCA_WHOLE_SHARE_MARKETS = frozenset({"TW", "TWO"})


def stock_dca_whole_shares(market: str | None) -> bool:
    return (market or "").upper() in STOCK_DCA_WHOLE_SHARE_MARKETS


@dataclass
class StockDcaOrder:
    shares: float
    gross: float  # 成交價金(= 綁定轉帳的金額)
    fee: float

    @property
    def total(self) -> float:
        """交割帳戶實際扣款 = 成交價金 + 手續費。"""
        return self.gross + self.fee


def stock_dca_order(
    amount: float,
    price: float,
    *,
    market: str | None,
    currency: str | None,
    fee_rate: float,
    fee_discount: float,
    fee_min: float,
) -> StockDcaOrder | None:
    """定期定額一期的下單結果;買不到任何股數回 None。

    - 整數股市場([stock_dca_whole_shares]):`amount` 是「含手續費」的扣款
      上限,股數 = 使 `成交價金 + 手續費 <= amount` 的最大整數(券商算法:
      (投入金額 − 手續費) ÷ 成交價,無條件捨去);手續費依實際成交價金計。
      連 1 股都買不起(amount < 股價 + 手續費)回 None。
    - 其它市場(碎股):成交價金 = amount,手續費另計(跟 2026-09-28 版本相同),
      股數 = amount ÷ 股價。

    App `stockDcaOrder`、Web `stockDcaOrder` 是同一套算法,改一邊要改另外兩邊。"""
    if price <= 0:
        return None
    budget = round_money(amount, currency)
    if budget <= 0:
        return None

    def fee_of(gross: float) -> float:
        if gross <= 0:
            return 0.0
        return max(round_money(gross * fee_rate * fee_discount, currency), fee_min)

    if not stock_dca_whole_shares(market):
        return StockDcaOrder(shares=budget / price, gross=budget, fee=fee_of(budget))

    def fits(n: int) -> bool:
        gross = stock_gross(n, price, currency)
        return gross + fee_of(gross) <= budget + 1e-9

    # 起點用券商公式(以整筆預算估手續費,估出來的一定買得起),再往上試:
    # 實際成交價金較小,手續費可能也較小,多出來的錢說不定夠再買 1 股。
    n = max(int(math.floor(round((budget - fee_of(budget)) / price, 9))), 0)
    while fits(n + 1):
        n += 1
    while n > 0 and not fits(n):
        n -= 1
    if n <= 0:
        return None
    gross = stock_gross(n, price, currency)
    return StockDcaOrder(shares=float(n), gross=gross, fee=fee_of(gross))
