"""Pydantic schemas for the web-first Contacts & Loans feature.

These are NOT part of the mobile sync layer — they are server-direct REST
contracts for `routers/contacts_loans.py`.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .schemas_linked import LinkedTxOut

# --------------------------------------------------------------------------
# Contacts
# --------------------------------------------------------------------------

ContactTag = Literal["friend", "family", "colleague", "merchant"]


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    note: Optional[str] = None
    avatar_file_id: Optional[str] = None
    contact_tag: Optional[ContactTag] = None
    phone: Optional[str] = Field(default=None, max_length=64)
    wechat: Optional[str] = Field(default=None, max_length=128)
    alipay: Optional[str] = Field(default=None, max_length=128)
    # 关联人资料(头像 / 多联系方式 / 多银行卡)；None = 未提供，{} = 清空。
    profile: Optional[dict] = None


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    note: Optional[str] = None
    avatar_file_id: Optional[str] = None
    contact_tag: Optional[ContactTag] = None
    phone: Optional[str] = Field(default=None, max_length=64)
    wechat: Optional[str] = Field(default=None, max_length=128)
    alipay: Optional[str] = Field(default=None, max_length=128)
    profile: Optional[dict] = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    note: Optional[str] = None
    avatar_file_id: Optional[str] = None
    contact_tag: Optional[ContactTag] = None
    phone: Optional[str] = None
    wechat: Optional[str] = None
    alipay: Optional[str] = None
    profile: Optional[dict] = None
    # 归档/停用状态由专用动作端点维护(见 /archive /unarchive /disable /enable),
    # 不开放通用 PATCH,避免与借款列表过滤语义脱节。
    is_archived: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Loans
# --------------------------------------------------------------------------

LoanDirection = Literal["lend", "borrow"]
LoanInterestType = Literal["none", "fixed", "annual"]
LoanStatus = Literal["unpaid", "partial", "overdue", "settled"]


class LoanCreate(BaseModel):
    ledger_id: str
    contact_id: Optional[str] = None
    direction: LoanDirection
    amount: float = Field(gt=0)
    currency_code: Optional[str] = None
    happened_at: datetime
    due_at: Optional[datetime] = None
    interest_type: LoanInterestType = "none"
    interest_rate: Optional[float] = None
    interest_amount: Optional[float] = None
    note: Optional[str] = None
    voucher_file_ids: Optional[list[str]] = None
    # 选中的真实账户(扣款/到款账户)。借出=扣款账户,借入=到款账户。
    account_id: Optional[str] = None
    account_name: Optional[str] = None


class LoanUpdate(BaseModel):
    contact_id: Optional[str] = None
    direction: Optional[LoanDirection] = None
    amount: Optional[float] = Field(default=None, gt=0)
    currency_code: Optional[str] = None
    happened_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    interest_type: Optional[LoanInterestType] = None
    interest_rate: Optional[float] = None
    interest_amount: Optional[float] = None
    note: Optional[str] = None
    voucher_file_ids: Optional[list[str]] = None
    # 重新选择真实账户。传 account_id 即视为要更新账户(可配合 account_name)。
    account_id: Optional[str] = None
    account_name: Optional[str] = None


class LoanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    ledger_id: str
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    direction: LoanDirection
    amount: float
    currency_code: Optional[str] = None
    happened_at: datetime
    due_at: Optional[datetime] = None
    interest_type: LoanInterestType
    interest_rate: Optional[float] = None
    interest_amount: Optional[float] = None
    note: Optional[str] = None
    voucher_file_ids: list[str] = []
    status: LoanStatus
    total_repaid: float = 0.0
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    # 0046:借出流水的可读摘要(备注/时间/分类/账户/金额)。
    tx_sync_details: list[LinkedTxOut] = []
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------
# Repayments
# --------------------------------------------------------------------------


class RepaymentCreate(BaseModel):
    """记录一笔还款。

    amount = **还款总额**(用户实际付出的钱,如借 500 还 510 就填 510)。服务端按
    该借款的剩余本金自动拆分:
      - 本金部分 = min(总额, 剩余本金) → 计入 LoanRepayment.amount,用于结清判定;
      - 利息部分 = 总额 - 本金部分 → 写入 interest_amount;0041 起利息**并入同一笔流水**
        的金额并在备注注明「含利息 X 元」,不单独生成「借款利息」流水。
    若显式传 interest_amount(>0),则以用户指定的利息为准,本金 = 总额 - 利息。
    """
    amount: float = Field(gt=0)
    repaid_at: datetime
    note: Optional[str] = None
    # 还款(收款/扣款)账户:允许与借款账户不同。不传则沿用借款账户,
    # 兼容旧客户端。即使借款本身没有账户,也可以单独为这笔还款指定账户。
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    # 还款凭证 attachment file id。前端上传完图片后把 id 数组一起提交。
    attachment_file_ids: Optional[list[str]] = None
    # 利息(可选):显式指定时覆盖「按剩余本金自动拆分」的结果。
    interest_amount: Optional[float] = None
    interest_account_id: Optional[str] = None
    interest_account_name: Optional[str] = None


class RepaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    ledger_id: str
    loan_id: str
    amount: float
    repaid_at: datetime
    note: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    attachment_file_ids: list[str] = []
    created_at: datetime
    interest_amount: Optional[float] = None
    interest_account_id: Optional[str] = None
    interest_account_name: Optional[str] = None
    interest_tx_sync_id: Optional[str] = None
    # 合并组(0039):本笔还款所属的「一次实际收付款」组 id。
    group_id: Optional[str] = None
    # 合并组信息(0039):该笔还款所在组的本金合计、被合并的借款笔数。
    # 组内只有首行持有 tx_sync_id(那条合并本金流水),其余行 tx_sync_id 为空。
    group_amount: Optional[float] = None
    group_size: int = 0
    group_merged_from_sync_ids: list[str] = []
    # 0046:还款流水 / 利息流水的可读摘要。
    tx_sync_details: list[LinkedTxOut] = []
    interest_tx_sync_details: list[LinkedTxOut] = []


class MergedRepaymentCreate(BaseModel):
    """一次实际收付款,可能同时结清同联系人同方向下的多笔借款(0039)。

    场景:张三 2025-01-01 借出 50+50+50,实际收到**一笔** 170 元
    (150 合并本金 + 20 利息)。用户只填总额,服务端按 FIFO 把本金分配到各笔借款,
    但交易流水里只生成**一条**本金流水(150),而不是拆成 3 条各 50。

    amount = 本次实际收付的**总额**(本金 + 利息)。
    interest_amount = 本次利息(可选):不传则按「总额 - 剩余本金」自动拆分。
    """

    ledger_id: str
    contact_id: Optional[str] = None
    direction: LoanDirection
    amount: float = Field(gt=0)
    interest_amount: Optional[float] = None
    repaid_at: datetime
    note: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    attachment_file_ids: Optional[list[str]] = None
    interest_account_id: Optional[str] = None
    interest_account_name: Optional[str] = None


class MergedRepaymentOut(BaseModel):
    """一次实际收付款的结果(0039)。"""

    id: str
    ledger_id: str
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None
    direction: LoanDirection
    amount: float
    interest_amount: Optional[float] = None
    repaid_at: datetime
    note: Optional[str] = None
    account_id: Optional[str] = None
    account_name: Optional[str] = None
    tx_sync_id: Optional[str] = None
    interest_tx_sync_id: Optional[str] = None
    # 被合并的各笔借款本金流水 syncId(供交易详情「查看原单」)。
    merged_from_sync_ids: list[str] = []
    # 本次实际结清的各笔借款分摊明细。
    allocations: list[dict] = []
    created_at: datetime


# --------------------------------------------------------------------------
# Aggregations / views
# --------------------------------------------------------------------------


class ContactSummaryOut(BaseModel):
    contact: ContactOut
    loan_count: int
    lend_total: float
    borrow_total: float
    receivable: float  # outstanding lend (别人欠我,未还)
    payable: float  # outstanding borrow (我欠别人,未还)


class ContactLoanRankOut(BaseModel):
    """联系人借款排行的一行(0031)。

    与 ContactSummaryOut 同口径(逐笔判定结清),但一次查询返回所有联系人,
    供联系人页渲染「借出 / 借入」双条形排行与卡片统计,避免 N+1 次 summary 请求。
    联系人是 user-global 实体,因此默认跨全部账本聚合。
    """

    contact_id: str
    name: str
    contact_tag: Optional[ContactTag] = None
    loan_count: int
    lend_total: float
    borrow_total: float
    receivable: float
    payable: float


class VirtualAccountsOut(BaseModel):
    ledger_id: str
    receivable: float  # 应收款总额(未结清借出未还)
    payable: float  # 应付款总额(未结清借入未还)


class ReminderItem(BaseModel):
    loan: LoanOut
    contact_name: Optional[str] = None
    days_to_due: Optional[int] = None  # 负数 = 已逾期天数


class RemindersOut(BaseModel):
    near_due: list[ReminderItem]  # 临近到期(<=3 天且未结清)
    overdue: list[ReminderItem]  # 已逾期


class SuggestContactOut(BaseModel):
    name: str
    source: str  # 'transaction_note'
