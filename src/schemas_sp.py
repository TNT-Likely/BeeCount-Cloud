"""Pydantic schemas for 储蓄罐(SavingPlan) feature. Web-first, not sync."""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from .schemas_linked import LinkedTxOut
from pydantic import BaseModel, ConfigDict, Field, field_serializer

PlanType = Literal["free", "fixed", "wish"]
# active / completed / abandoned / archived(归档,不再显示在活跃列表)
PlanStatus = Literal["active", "completed", "abandoned", "archived"]


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """统一输出 UTC ISO8601(带 Z)。

    DB 里是 naive UTC(SQLite 不保留 tzinfo),直接序列化会让前端
    `new Date(str)` 当成浏览器本地时间解析 → 整体偏 8 小时。
    这里补上 UTC 标识,前端 `new Date('...Z')` 才能正确转本地显示。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class PlanCreate(BaseModel):
    ledger_id: str
    name: str = Field(min_length=1, max_length=128)
    icon: Optional[str] = None
    target_amount: float = Field(gt=0)
    currency_code: Optional[str] = None
    deadline: Optional[datetime] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    plan_type: PlanType = "free"
    monthly_amount: Optional[float] = Field(default=None, ge=0)
    note: Optional[str] = None

class PlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    icon: Optional[str] = None
    target_amount: Optional[float] = Field(default=None, gt=0)
    currency_code: Optional[str] = None
    deadline: Optional[datetime] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    plan_type: Optional[PlanType] = None
    monthly_amount: Optional[float] = Field(default=None, ge=0)
    status: Optional[PlanStatus] = None
    note: Optional[str] = None

class ContributionIn(BaseModel):
    amount: float = Field(gt=0)
    happened_at: Optional[datetime] = None
    note: Optional[str] = None
    tx_sync_id: Optional[str] = None
    # 扣款账户(可选):选中则自动生成一笔支出交易并扣减该账户
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    # 收款账户(可选):选中则自动生成一笔收入交易(例如转入储蓄卡)
    receive_account_id: Optional[str] = None
    receive_account_name: Optional[str] = None
    # 附件(凭证)
    attachments: Optional[list[dict[str, Any]]] = None
    # 是否记入收支统计/预算
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    # 交易级多币种
    currency_code: Optional[str] = None
    native_amount: Optional[float] = None
    # 分类名称(默认"储蓄",可自定义)
    category_name: Optional[str] = None


class ContributionUpdate(BaseModel):
    """存入记录的部分更新(全部可选,只改传了的字段)。"""

    amount: Optional[float] = Field(default=None, gt=0)
    happened_at: Optional[datetime] = None
    note: Optional[str] = None
    category_name: Optional[str] = None
    exclude_from_stats: Optional[bool] = None
    exclude_from_budget: Optional[bool] = None
    currency_code: Optional[str] = None
    native_amount: Optional[float] = None


class ContributionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    plan_id: str
    amount: float
    # 按关联流水实时状态派生的有效金额(流水被删=0 / 改额跟随 / 退款冲减)
    effective_amount: float = 0.0
    happened_at: datetime
    note: Optional[str] = None
    tx_sync_id: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    receive_account_id: Optional[str] = None
    receive_account_name: Optional[str] = None
    receive_tx_sync_id: Optional[str] = None
    # 0046:关联流水的可读摘要(存入流水 / 到账流水)。
    tx_sync_details: list[LinkedTxOut] = []
    receive_tx_sync_details: list[LinkedTxOut] = []
    category_name: Optional[str] = None
    currency_code: Optional[str] = None
    native_amount: Optional[float] = None
    attachments: Optional[list[dict[str, Any]]] = None
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    created_at: datetime

    @field_serializer("happened_at", "created_at")
    def _ser_utc(self, v: datetime) -> str:
        return _iso_utc(v) or ""

class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    ledger_id: str
    name: str
    icon: Optional[str] = None
    target_amount: float
    currency_code: Optional[str] = None
    deadline: Optional[datetime] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    plan_type: PlanType
    monthly_amount: Optional[float] = None
    status: PlanStatus
    note: Optional[str] = None
    saved_amount: float = 0.0
    progress: float = 0.0
    created_at: datetime
    updated_at: datetime

    @field_serializer("deadline", "created_at", "updated_at")
    def _ser_utc(self, v: Optional[datetime]) -> Optional[str]:
        return _iso_utc(v)