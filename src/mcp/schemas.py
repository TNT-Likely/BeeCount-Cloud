"""MCP 批量输入的公开 schema，与单笔使用相同的金额契约。"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def reject_boolean_money(value):
    if isinstance(value, bool):
        raise ValueError('amount must be a number, not a boolean')
    return value


Money = Annotated[float, BeforeValidator(reject_boolean_money), Field(allow_inf_nan=False)]
PositiveMoney = Annotated[Money, Field(gt=0)]


class BatchTransactionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(gt=0, allow_inf_nan=False, description="Positive finite amount; numeric strings are accepted")
    tx_type: Literal["expense", "income", "transfer"] = "expense"
    happened_at: str | None = None
    note: str | None = None
    category: str | None = None
    account: str | None = None
    tags: list[str] | None = None
    currency: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    from_account: str | None = None
    to_account: str | None = None
    native_amount: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    attachments: list[str] | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def reject_boolean_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("amount must be a number, not a boolean")
        return value


def positive_amount(value) -> float:
    return BatchTransactionItem(amount=value).amount
