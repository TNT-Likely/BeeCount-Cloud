"""Pydantic schemas for the web-first fixed-term Deposits feature.

NOT part of the mobile sync layer — server-direct REST contracts for
`routers/deposits.py`, sibling of `schemas_cl.py`.
"""
from datetime import datetime
from typing import Literal, Optional

from .schemas_linked import LinkedTxOut
from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Deposits
# --------------------------------------------------------------------------

DepositType = Literal["fixed", "installment", "cd", "structured"]
DepositStatus = Literal["holding", "matured", "withdrawn", "renewed"]
OnMaturity = Literal["withdraw", "auto_renew"]
InterestSettlement = Literal["maturity", "monthly", "quarterly"]


class DepositCreate(BaseModel):
    ledger_id: str
    name: str = Field(min_length=1, max_length=128)
    deposit_type: DepositType = "fixed"
    principal: float = Field(gt=0)
    currency_code: Optional[str] = None
    happened_at: datetime
    term_months: int = Field(ge=1, le=600)
    annual_rate: Optional[float] = Field(default=None, ge=0, le=100)
    # 手动覆盖到期利息;None = 按公式自动计算。
    interest_override: Optional[float] = Field(default=None, ge=0)
    on_maturity: OnMaturity = "withdraw"
    interest_settlement: InterestSettlement = "maturity"
    settlement_day: Optional[int] = Field(default=None, ge=1, le=28)
    note: Optional[str] = None
    # 存单照片 attachment file id(前端上传后提交)。
    certificate_file_ids: Optional[list[str]] = None
    # 关联银行账户(活期):存入划转从此账户出。
    account_id: Optional[str] = None
    account_name: Optional[str] = None


class DepositUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    deposit_type: Optional[DepositType] = None
    principal: Optional[float] = Field(default=None, gt=0)
    currency_code: Optional[str] = None
    happened_at: Optional[datetime] = None
    term_months: Optional[int] = Field(default=None, ge=1, le=600)
    annual_rate: Optional[float] = Field(default=None, ge=0, le=100)
    interest_override: Optional[float] = Field(default=None, ge=0)
    on_maturity: Optional[OnMaturity] = None
    interest_settlement: Optional[InterestSettlement] = None
    settlement_day: Optional[int] = Field(default=None, ge=1, le=28)
    note: Optional[str] = None
    certificate_file_ids: Optional[list[str]] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None


class DepositOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    ledger_id: str
    name: str
    deposit_type: DepositType
    principal: float
    currency_code: Optional[str] = None
    started_at: datetime
    term_months: int
    annual_rate: Optional[float] = None
    interest_override: Optional[float] = None
    maturity_at: Optional[datetime] = None
    on_maturity: OnMaturity
    interest_settlement: InterestSettlement
    settlement_day: Optional[int] = None
    status: DepositStatus
    note: Optional[str] = None
    certificate_file_ids: list[str] = []
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    # 自动生成的流水 syncId:存入划转 / 支取本金转回 / 支取利息收入
    flow_tx_sync_id: Optional[str] = None
    principal_return_tx_sync_id: Optional[str] = None
    interest_tx_sync_id: Optional[str] = None
    # 0046:关联流水的可读摘要(存入 / 本金回流 / 利息)。
    tx_sync_details: list[LinkedTxOut] = []
    # 派生:预期到期利息(覆盖值或公式值)
    expected_interest: float = 0.0
    # 派生:本息合计
    total_at_maturity: float = 0.0
    # 派生:已周期结息总额
    settled_interest: float = 0.0
    # 派生:距到期天数(负数 = 已过期);无到期日为 None
    days_to_maturity: Optional[int] = None
    withdrawn_at: Optional[datetime] = None
    withdrawn_interest: Optional[float] = None
    renewed_to_deposit_id: Optional[str] = None
    parent_deposit_id: Optional[str] = None
    renewed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------


class DepositWithdrawRequest(BaseModel):
    withdrawn_at: datetime
    # 支回到账账户(默认沿用存单关联账户)。
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    # 实际支取利息;None = 预期利息 - 已结息(不小于 0)。
    interest_amount: Optional[float] = Field(default=None, ge=0)


class DepositRenewRequest(BaseModel):
    new_rate: Optional[float] = Field(default=None, ge=0, le=100)
    new_term_months: Optional[int] = Field(default=None, ge=1, le=600)
    new_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    # 新一期开始时间(默认本期到期日)。
    started_at: Optional[datetime] = None
    # 转存本金(默认原本金;include_interest=true 时默认本金+利息)。
    new_principal: Optional[float] = Field(default=None, gt=0)
    include_interest: bool = True
    account_id: Optional[str] = None
    account_name: Optional[str] = None


class DepositInterestSettleRequest(BaseModel):
    amount: float = Field(gt=0)
    settled_at: datetime
    note: Optional[str] = None
    # 结息入账账户(默认沿用存单关联账户)。
    account_id: Optional[str] = None
    account_name: Optional[str] = None


class DepositInterestPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    ledger_id: str
    deposit_id: str
    amount: float
    settled_at: datetime
    note: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    # 0046:关联流水的可读摘要。
    tx_sync_details: list[LinkedTxOut] = []
    created_at: datetime


# --------------------------------------------------------------------------
# Aggregations / views
# --------------------------------------------------------------------------


class DepositSummaryOut(BaseModel):
    ledger_id: str
    # 持有中 + 已到期(未支取/未转存)的本金总额 =「定期存款」虚拟账户余额
    total_principal: float
    # 这些存单的预期到期利息总额
    total_interest_expected: float
    count: int


class DepositReminderItem(BaseModel):
    deposit: DepositOut
    days_to_maturity: int  # 负数 = 已到期天数


class DepositRemindersOut(BaseModel):
    near_due: list[DepositReminderItem]  # 到期前 7 天 / 3 天 / 当天
    overdue: list[DepositReminderItem]  # 已到期未处理
