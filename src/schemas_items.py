"""Pydantic schemas for 物品管理(Item) feature. Web-first, not sync."""
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .schemas_linked import LinkedTxOut

LinkMode = Literal["new", "existing", "none"]
CostMode = Literal["none", "daily_avg", "per_day"]
FlowType = Literal["income", "expense"]


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """统一输出 UTC ISO8601(带 Z)，避免 SQLite naive UTC 被当成浏览器本地时间。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class ItemCreate(BaseModel):
    ledger_id: str
    name: str = Field(min_length=1, max_length=128)
    price: float = Field(ge=0, default=0.0)
    purchased_at: Optional[datetime] = None
    purchase_channel: Optional[str] = None
    category_name: Optional[str] = None
    location: Optional[str] = None
    link_mode: LinkMode = "none"
    # 关联已有交易流水的 syncId 列表(0040):一件物品的购入可能由多笔账单组成。
    # tx_sync_id 保留(单笔的旧写法),两者都传时以 tx_sync_ids 为准。
    tx_sync_ids: Optional[list[str]] = None
    tx_sync_id: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    average_mode: CostMode = "none"
    target_cost_mode: CostMode = "none"
    note: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 0044:关联了已有流水时,价格自动 = 各笔流水金额之和(成本汇总)。
    # 传 False 表示用户手动指定了价格,以传入值为准。
    auto_price: bool = True


class ItemUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    price: Optional[float] = Field(default=None, ge=0)
    purchased_at: Optional[datetime] = None
    purchase_channel: Optional[str] = None
    category_name: Optional[str] = None
    location: Optional[str] = None
    average_mode: Optional[CostMode] = None
    target_cost_mode: Optional[CostMode] = None
    note: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 关联的交易流水(0040):传列表即可替换全部关联;传空列表表示清空。
    tx_sync_ids: Optional[list[str]] = None
    tx_sync_id: Optional[str] = None
    # 0044:可改关联方式;auto_price=False 时保留用户手填的价格不自动汇总。
    link_mode: Optional[LinkMode] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    auto_price: Optional[bool] = None


class ItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    ledger_id: str
    name: str
    price: float
    purchased_at: datetime
    purchase_channel: Optional[str] = None
    category_name: Optional[str] = None
    location: Optional[str] = None
    link_mode: str = "none"
    # 关联的交易流水 syncId 列表(0040)。tx_sync_id 保留:第一个关联的 syncId。
    tx_sync_id: Optional[str] = None
    tx_sync_ids: list[str] = []
    # 0044:每笔关联流水的金额(syncId → 金额)。前端回填关联时用它算成本合计,
    # 不用再单独查流水(否则编辑弹窗里价格会被算成 0)。
    tx_sync_amounts: dict[str, float] = {}
    # 0045:关联流水的完整摘要(备注/金额/时间/分类/账户),详情页要显示可读信息。
    tx_sync_details: list[LinkedTxOut] = []
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    average_mode: str = "none"
    target_cost_mode: str = "none"
    note: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 派生统计
    total_income: float = 0.0
    total_expense: float = 0.0
    net_cost: float = 0.0
    flow_count: int = 0
    days_held: float = 0.0
    average_cost: Optional[float] = None
    target_cost: Optional[float] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    @field_serializer("purchased_at", "created_at", "updated_at")
    def _ser_utc(self, v: Optional[datetime]) -> Optional[str]:
        return _iso_utc(v)


class ItemFlowCreate(BaseModel):
    item_id: str
    flow_type: FlowType
    amount: float = Field(gt=0)
    happened_at: Optional[datetime] = None
    category_name: Optional[str] = None
    note: Optional[str] = None
    link_mode: LinkMode = "none"
    # 关联已有交易流水的 syncId 列表(0040);单笔时长度为 1。
    tx_sync_ids: Optional[list[str]] = None
    tx_sync_id: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 0044:关联了已有流水时,金额自动 = 各笔流水金额之和。
    auto_amount: bool = True


class ItemFlowUpdate(BaseModel):
    flow_type: Optional[FlowType] = None
    amount: Optional[float] = Field(default=None, gt=0)
    happened_at: Optional[datetime] = None
    category_name: Optional[str] = None
    note: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 关联的交易流水(0040):传列表即可替换全部关联;传空列表表示清空。
    tx_sync_ids: Optional[list[str]] = None
    tx_sync_id: Optional[str] = None
    # 0044:可改关联方式;auto_amount=False 时保留用户手填的金额不自动汇总。
    link_mode: Optional[LinkMode] = None
    auto_amount: Optional[bool] = None


class ItemFlowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    item_id: str
    ledger_id: str
    flow_type: str
    amount: float
    happened_at: datetime
    category_name: Optional[str] = None
    note: Optional[str] = None
    link_mode: str = "none"
    tx_sync_id: Optional[str] = None
    # 关联的交易流水 syncId 列表(0040)。
    tx_sync_ids: list[str] = []
    # 0044:每笔关联流水的金额(syncId → 金额),回填金额合计用。
    tx_sync_amounts: dict[str, float] = {}
    # 0045:关联流水的可读摘要,展示用。
    tx_sync_details: list[LinkedTxOut] = []
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    @field_serializer("happened_at", "created_at", "updated_at")
    def _ser_utc(self, v: Optional[datetime]) -> Optional[str]:
        return _iso_utc(v)
