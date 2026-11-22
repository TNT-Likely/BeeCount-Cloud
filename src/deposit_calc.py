"""定期存款的利息 / 到期日 / 状态派生纯函数。

公式(产品口径):
  到期利息 = 本金 × 年利率 × (存期月数 / 12)
  本息合计 = 本金 + 到期利息
到期日 = 存入时间 + 存期月数(日历月,月末自动收敛,秒级精度保留)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


def add_months(dt: datetime, months: int) -> datetime:
    """日历月加法:2024-01-31 + 1M = 2024-02-29(月末收敛)。

    保留时分秒;naive/aware 跟随入参。
    """
    if months == 0:
        return dt
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    # 月末日收敛:day 超过目标月天数时取该月最后一天。
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)
    return dt.replace(year=year, month=month, day=day)


def compute_maturity(started_at: datetime, term_months: int) -> datetime:
    return add_months(started_at, term_months)


def compute_interest(principal: float, annual_rate: Optional[float], term_months: int) -> float:
    """到期利息 = 本金 × 年利率% × 存期月数 / 12。缺利率按 0。"""
    if not annual_rate:
        return 0.0
    return round(principal * (annual_rate / 100.0) * (term_months / 12.0), 2)


def expected_interest(
    principal: float,
    annual_rate: Optional[float],
    term_months: int,
    interest_override: Optional[float],
) -> float:
    """到期利息:手动覆盖优先,否则按公式。"""
    if interest_override is not None:
        return round(float(interest_override), 2)
    return compute_interest(principal, annual_rate, term_months)


def _to_utc(dt: datetime) -> datetime:
    """SQLite 返回 naive datetime;统一补 UTC 再比较。"""
    if dt.tzinfo is None:
        from datetime import timezone

        return dt.replace(tzinfo=timezone.utc)
    return dt


def derive_status(stored_status: str, maturity_at: Optional[datetime], now: datetime) -> str:
    """状态派生:withdrawn/renewed 是终态不再变;holding/matured 由到期日判定。"""
    if stored_status in ("withdrawn", "renewed"):
        return stored_status
    if maturity_at is not None:
        if _to_utc(now) >= _to_utc(maturity_at):
            return "matured"
    return "holding"


def days_until(target: datetime, now: datetime) -> int:
    """now 到 target 的整天数(向下取整;负数 = 已过去)。"""
    delta = target.timestamp() - now.timestamp()
    return int(delta // 86400)
