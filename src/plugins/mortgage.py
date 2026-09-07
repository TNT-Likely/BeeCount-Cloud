"""Mortgage auto-accounting reference plugin."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..services.mortgage import (
    MortgagePrepayment,
    build_mortgage_schedule,
    cents_to_yuan,
    yuan_to_cents,
)
from .base import PluginDefinition, PluginRunResult


class MortgagePrepaymentInput(BaseModel):
    prepayment_date: date
    amount: Decimal = Field(gt=0)
    effect: Literal["reduce_term", "reduce_payment"] = "reduce_term"


class MortgagePluginInput(BaseModel):
    loan_name: str = Field(default="房贷", min_length=1, max_length=80)
    principal_amount: Decimal = Field(gt=0)
    annual_rate_percent: Decimal = Field(ge=0)
    term_months: int = Field(gt=0, le=600)
    start_date: date
    day_of_month: int = Field(ge=1, le=31)
    repayment_method: Literal["equal_principal_interest", "equal_principal"]
    account_name: str | None = None
    account_id: str | None = None
    principal_category_name: str = "房贷本金"
    principal_category_id: str | None = None
    interest_category_name: str = "房贷利息"
    interest_category_id: str | None = None
    prepayment_category_name: str = "提前还款"
    prepayment_category_id: str | None = None
    tag_names: list[str] = Field(default_factory=lambda: ["房贷"])
    prepayments: list[MortgagePrepaymentInput] = Field(default_factory=list)


def run_mortgage_plugin(input_model: BaseModel) -> PluginRunResult:
    req = MortgagePluginInput.model_validate(input_model)
    schedule = build_mortgage_schedule(
        principal_cents=yuan_to_cents(req.principal_amount),
        annual_rate_percent=req.annual_rate_percent,
        term_months=req.term_months,
        start_date=req.start_date,
        day_of_month=req.day_of_month,
        repayment_method=req.repayment_method,
        prepayments=[
            MortgagePrepayment(
                prepayment_date=item.prepayment_date,
                amount_cents=yuan_to_cents(item.amount),
                effect=item.effect,
            )
            for item in req.prepayments
        ],
    )
    transactions = _build_mortgage_tx_payloads(req, schedule)
    return PluginRunResult(
        transactions=transactions,
        summary={
            "schedule_entries": len(schedule),
            "transaction_count": len(transactions),
            "total_principal": cents_to_yuan(
                sum(item.principal_cents + item.prepayment_cents for item in schedule)
            ),
            "total_interest": cents_to_yuan(sum(item.interest_cents for item in schedule)),
            "total_prepayment": cents_to_yuan(sum(item.prepayment_cents for item in schedule)),
        },
    )


def _build_mortgage_tx_payloads(
    req: MortgagePluginInput,
    schedule: list[Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tags = [tag.strip() for tag in req.tag_names if tag.strip()]
    for entry in schedule:
        if entry.prepayment_cents > 0:
            out.append(
                _tx_payload(
                    amount=cents_to_yuan(entry.prepayment_cents),
                    happened_at=_as_utc_datetime(entry.prepayment_date or entry.due_date),
                    note=f"{req.loan_name} 提前还款",
                    category_name=req.prepayment_category_name,
                    category_id=req.prepayment_category_id,
                    account_name=req.account_name,
                    account_id=req.account_id,
                    tags=tags,
                )
            )
            continue
        if entry.principal_cents > 0:
            out.append(
                _tx_payload(
                    amount=cents_to_yuan(entry.principal_cents),
                    happened_at=_as_utc_datetime(entry.due_date),
                    note=f"{req.loan_name} 第{entry.period_index}期本金",
                    category_name=req.principal_category_name,
                    category_id=req.principal_category_id,
                    account_name=req.account_name,
                    account_id=req.account_id,
                    tags=tags,
                )
            )
        if entry.interest_cents > 0:
            out.append(
                _tx_payload(
                    amount=cents_to_yuan(entry.interest_cents),
                    happened_at=_as_utc_datetime(entry.due_date),
                    note=f"{req.loan_name} 第{entry.period_index}期利息",
                    category_name=req.interest_category_name,
                    category_id=req.interest_category_id,
                    account_name=req.account_name,
                    account_id=req.account_id,
                    tags=tags,
                )
            )
    return out


def _tx_payload(
    *,
    amount: float,
    happened_at: datetime,
    note: str,
    category_name: str,
    category_id: str | None,
    account_name: str | None,
    account_id: str | None,
    tags: list[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tx_type": "expense",
        "amount": amount,
        "happened_at": happened_at.isoformat(),
        "note": note,
        "category_name": category_name,
        "category_kind": "expense",
    }
    if category_id:
        payload["category_id"] = category_id
    if account_name:
        payload["account_name"] = account_name
    if account_id:
        payload["account_id"] = account_id
    if tags:
        payload["tags"] = tags
    return payload


def _as_utc_datetime(value: date) -> datetime:
    return datetime.combine(value, time(hour=12), tzinfo=timezone.utc)


MORTGAGE_PLUGIN = PluginDefinition(
    plugin_id="mortgage_auto_accounting",
    name="Mortgage auto accounting",
    description="Generate BeeCount transactions split into mortgage principal, interest, and prepayments.",
    name_i18n={"zh-CN": "房贷自动记账"},
    description_i18n={
        "zh-CN": "根据利率、期限、还款方式和提前还款生成拆分本金/利息的 BeeCount 交易。"
    },
    input_model=MortgagePluginInput,
    run=run_mortgage_plugin,
)
