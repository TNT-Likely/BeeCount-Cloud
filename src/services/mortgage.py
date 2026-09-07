"""Mortgage amortization helpers for generated bookkeeping entries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class MortgagePrepayment:
    prepayment_date: date
    amount_cents: int
    effect: str = "reduce_term"


@dataclass(frozen=True)
class MortgageScheduleEntry:
    period_index: int
    due_date: date
    principal_cents: int
    interest_cents: int
    remaining_principal_cents: int
    prepayment_cents: int = 0
    prepayment_date: date | None = None


def yuan_to_cents(value: Decimal | float | int | str) -> int:
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    return int(amount * 100)


def cents_to_yuan(cents: int) -> float:
    return float((Decimal(cents) / Decimal(100)).quantize(CENT))


def build_mortgage_schedule(
    *,
    principal_cents: int,
    annual_rate_percent: Decimal,
    term_months: int,
    start_date: date,
    day_of_month: int,
    repayment_method: str,
    prepayments: list[MortgagePrepayment] | None = None,
) -> list[MortgageScheduleEntry]:
    if principal_cents <= 0:
        raise ValueError("principal must be positive")
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    if not 1 <= day_of_month <= 31:
        raise ValueError("day_of_month must be between 1 and 31")
    annual_rate_percent = Decimal(str(annual_rate_percent))
    if annual_rate_percent < 0:
        raise ValueError("annual_rate_percent must be non-negative")
    if repayment_method not in {"equal_principal_interest", "equal_principal"}:
        raise ValueError("unsupported repayment_method")

    events = sorted(prepayments or [], key=lambda item: item.prepayment_date)
    for event in events:
        if event.amount_cents <= 0:
            raise ValueError("prepayment amount must be positive")
        if event.effect not in {"reduce_term", "reduce_payment"}:
            raise ValueError("unsupported prepayment effect")

    monthly_rate = annual_rate_percent / Decimal(100) / Decimal(12)
    remaining = principal_cents
    payment_cents = _equal_principal_interest_payment(
        remaining,
        monthly_rate,
        term_months,
    )
    fixed_principal_cents = _round_cents(Decimal(principal_cents) / Decimal(term_months))
    schedule: list[MortgageScheduleEntry] = []
    event_index = 0

    period = 1
    while remaining > 0 and period <= term_months + len(events):
        due_date = _add_months_with_day(start_date, period - 1, day_of_month)

        while event_index < len(events) and events[event_index].prepayment_date <= due_date:
            event = events[event_index]
            paid = min(event.amount_cents, remaining)
            remaining -= paid
            schedule.append(
                MortgageScheduleEntry(
                    period_index=period,
                    due_date=due_date,
                    principal_cents=0,
                    interest_cents=0,
                    remaining_principal_cents=remaining,
                    prepayment_cents=paid,
                    prepayment_date=event.prepayment_date,
                )
            )
            if event.effect == "reduce_payment":
                remaining_months = max(term_months - period + 1, 1)
                payment_cents = _equal_principal_interest_payment(
                    remaining,
                    monthly_rate,
                    remaining_months,
                )
                fixed_principal_cents = _round_cents(
                    Decimal(remaining) / Decimal(remaining_months)
                )
            event_index += 1
            if remaining <= 0:
                return schedule

        interest_cents = _round_cents(Decimal(remaining) * monthly_rate)
        if repayment_method == "equal_principal_interest":
            principal_part = payment_cents - interest_cents
            if principal_part <= 0:
                principal_part = remaining
        else:
            principal_part = fixed_principal_cents

        if period >= term_months:
            principal_part = remaining
        principal_part = min(principal_part, remaining)
        remaining -= principal_part
        schedule.append(
            MortgageScheduleEntry(
                period_index=period,
                due_date=due_date,
                principal_cents=principal_part,
                interest_cents=max(interest_cents, 0),
                remaining_principal_cents=remaining,
            )
        )
        period += 1

    while remaining > 0 and event_index < len(events):
        event = events[event_index]
        paid = min(event.amount_cents, remaining)
        remaining -= paid
        schedule.append(
            MortgageScheduleEntry(
                period_index=period,
                due_date=event.prepayment_date,
                principal_cents=0,
                interest_cents=0,
                remaining_principal_cents=remaining,
                prepayment_cents=paid,
                prepayment_date=event.prepayment_date,
            )
        )
        event_index += 1
    return schedule


def _equal_principal_interest_payment(
    principal_cents: int,
    monthly_rate: Decimal,
    months: int,
) -> int:
    if principal_cents <= 0:
        return 0
    if monthly_rate == 0:
        return _round_cents(Decimal(principal_cents) / Decimal(months))
    factor = (Decimal(1) + monthly_rate) ** months
    payment = Decimal(principal_cents) * monthly_rate * factor / (factor - Decimal(1))
    return _round_cents(payment)


def _round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _add_months_with_day(start: date, months: int, day_of_month: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day_of_month, _days_in_month(year, month)))


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days
