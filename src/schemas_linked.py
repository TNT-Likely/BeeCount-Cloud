"""关联交易流水的公共摘要 schema(0046)。

凡是「业务实体引用了某条交易流水」的地方(物品 / 借款 / 还款 / 储蓄罐 /
定期存款 / 报销),详情页都要把这条流水**可读地**展示出来 —— 备注、时间、
分类、账户、金额 —— 而不是甩一串 syncId 给用户。

各模块的 Out schema 统一挂一个 `tx_sync_details: list[LinkedTxOut]`。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, field_serializer


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """统一输出 UTC ISO8601(带 Z),避免 SQLite naive UTC 被当成浏览器本地时间。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class LinkedTxOut(BaseModel):
    """关联交易流水的摘要。

    字段与交易流水行(TransactionRow)对齐,前端可以用同一套渲染:
    主标题取 note(无则 category_name)、副标题是时间 · 账户 · 分类、金额在右上。
    """

    sync_id: str
    tx_type: str = "expense"
    amount: float = 0.0
    happened_at: Optional[datetime] = None
    note: Optional[str] = None
    category_name: Optional[str] = None
    category_kind: Optional[str] = None
    account_name: Optional[str] = None
    currency_code: Optional[str] = None
    exclude_from_stats: bool = False

    @field_serializer("happened_at")
    def _ser_utc(self, v: Optional[datetime]) -> Optional[str]:
        return _iso_utc(v)
