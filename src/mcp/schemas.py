"""MCP 批量输入的公开 schema，与单笔使用相同的金额契约。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("amount", mode="before")
    @classmethod
    def reject_boolean_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("amount must be a number, not a boolean")
        return value


def positive_amount(value) -> float:
    return BatchTransactionItem(amount=value).amount
