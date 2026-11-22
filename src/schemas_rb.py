"""Pydantic schemas for 报销(Reimbursement) feature. Web-first, not sync."""
from datetime import datetime, timezone
from typing import Any, Optional

from .schemas_linked import LinkedTxOut
from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """统一输出 UTC ISO8601(带 Z)，避免前端 new Date 按本地解析偏 8 小时。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class ReimbursementItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    ledger_id: str
    tx_sync_id: str
    # 0046:关联流水的可读摘要。
    tx_sync_details: list[LinkedTxOut] = []
    amount: float
    happened_at: datetime
    note: Optional[str] = None
    category_name: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    currency_code: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 报销公司维度(0037)：公司档案快照
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    status: str
    reimbursement_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("happened_at", "created_at", "updated_at")
    def _ser_utc(self, v: datetime) -> str:
        return _iso_utc(v) or ""


class ReimbursementItemCreateIn(BaseModel):
    """手动把一笔已有交易登记为报销项（编辑交易时勾选报销用）。"""
    ledger_id: str
    tx_sync_id: str
    amount: float = Field(gt=0)
    happened_at: Optional[datetime] = None
    note: Optional[str] = None
    category_name: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    currency_code: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 报销公司维度(0037)：勾选报销时公司必填；名称由服务端按档案回填。
    company_id: Optional[str] = None
    company_name: Optional[str] = None


class ReimbursementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    ledger_id: str
    amount: float
    happened_at: datetime
    note: Optional[str] = None
    icon: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    receive_account_id: Optional[str] = None
    receive_account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    # 0046:关联流水的可读摘要。
    tx_sync_details: list[LinkedTxOut] = []
    item_count: int = 0
    items: list[ReimbursementItemOut] = []
    # 报销公司维度(0037)：合并报销时从报销项继承的公司快照。
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("happened_at", "created_at", "updated_at")
    def _ser_utc(self, v: datetime) -> str:
        return _iso_utc(v) or ""


class ReimbursementCreateIn(BaseModel):
    """多笔合并报销。"""
    ledger_id: str
    item_ids: list[str] = Field(min_length=1)
    amount: float = Field(gt=0)
    happened_at: Optional[datetime] = None
    note: Optional[str] = None
    icon: Optional[str] = None
    attachments: Optional[list[dict[str, Any]]] = None
    # 收款账户（选填；选了会生成一笔 income 交易，不记收支/预算）
    receive_account_id: Optional[str] = None
    receive_account_name: Optional[str] = None
    # 报销公司维度(0037)：公司必填（服务端校验存在），名称按档案回填。
    company_id: str = Field(min_length=1)
    company_name: Optional[str] = None


class ReimbursementUpdateIn(BaseModel):
    """修改报销单的图标/备注等。"""
    icon: Optional[str] = None
    note: Optional[str] = None
    happened_at: Optional[datetime] = None
    attachments: Optional[list[dict[str, Any]]] = None
