import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# 6 位 hex，开头必须有 #；字母大小写都接受，validator 会归一化成大写。
_HEX6_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

MemberRole = Literal["owner", "editor", "viewer"]
SyncAction = Literal["upsert", "delete"]


class AuthRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AuthLoginRequest(BaseModel):
    email: str
    password: str
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AuthRefreshRequest(BaseModel):
    refresh_token: str


class AuthLogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserOut(BaseModel):
    id: str
    email: str
    is_admin: bool = False


class UserProfileOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_version: int = 0
    # 对齐 mobile `incomeExpenseColorSchemeProvider`。Nullable = 未设置过，web
    # 视为默认（红色收入）。
    income_is_red: bool | None = None
    # 主题色 hex（#RRGGBB），mobile 设置后推上来；web 把它当作"初始偏好"，
    # 用户在 web 本地改色会写 localStorage 优先生效。
    theme_primary_color: str | None = None
    # 外观类设置 JSON 对象（解析后的 dict）。mobile 推上来，web 只读展示。
    # 目前约定的 key：
    #   header_decoration_style (str) / compact_amount (bool) / show_transaction_time (bool)
    # 将来加新 key 不需要加 schema 字段。None = 没设置过。
    appearance: dict | None = None
    # AI 配置 JSON 对象。mobile 推上来,web 只读展示,另一台 mobile 设备也会拉。
    # key: providers (list) / binding (dict) / custom_prompt (str) /
    # strategy (str) / bill_extraction_enabled (bool) / use_vision (bool)
    ai_config: dict | None = None
    # 用户主币种,ISO 4217 大写代码(如 CNY / USD / JPY)。None = 未设置。
    primary_currency: str | None = None


class UserProfilePatchRequest(BaseModel):
    # 所有字段都可选：mobile 改配色时只送 `income_is_red`，web 改昵称时只送
    # `display_name`。handler 只更新非 None 字段。
    display_name: str | None = None
    income_is_red: bool | None = None
    theme_primary_color: str | None = None
    appearance: dict | None = None
    ai_config: dict | None = None
    primary_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3,8}$")

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Display name cannot be empty")
        if len(normalized) > 32:
            raise ValueError("Display name too long")
        return normalized

    @field_validator("theme_primary_color")
    @classmethod
    def validate_theme_primary_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        # 只接受 #RRGGBB 格式；太宽松会被当任意文本写入
        if not _HEX6_PATTERN.match(normalized):
            raise ValueError("theme_primary_color must be #RRGGBB hex")
        return normalized


class UserProfileAvatarUploadOut(BaseModel):
    avatar_url: str
    avatar_version: int


class AuthTokenResponse(BaseModel):
    user: UserOut
    access_token: str
    refresh_token: str
    expires_in: int
    device_id: str
    scopes: list[str] = Field(default_factory=list)


class AuthLoginResponse(BaseModel):
    """统一登录响应:requires_2fa=False 时直接是 token,True 时返回 challenge。

    设计思路:为了兼容老 App / 老 Web 客户端(只读 access_token 等字段),
    所有字段都做成 Optional;新客户端先看 requires_2fa 字段决定走哪条分支。
    """

    requires_2fa: bool = False

    # 2FA 未启用 / 已通过验证时填这些(等价老 AuthTokenResponse):
    user: UserOut | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_in: int | None = None
    device_id: str | None = None
    scopes: list[str] = Field(default_factory=list)

    # 2FA 启用且未通过验证时填这些:
    challenge_token: str | None = None
    available_methods: list[str] = Field(default_factory=list)


class TwoFASetupResponse(BaseModel):
    """启用 2FA 第一步:server 生成 secret,客户端拿去画 QR / 手输。"""

    secret: str  # base32,Web 端可手动输入到 authenticator
    qr_code_uri: str  # otpauth://...,Web 端用 qrcode 库渲染成图片
    expires_in: int = 300  # 5 分钟内未 confirm 则 secret 仍可被覆盖重来


class TwoFAConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)  # 允许带空格


class TwoFAConfirmResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str]  # 仅在这一刻明文返回,服务器只存 sha256


class TwoFAStatusResponse(BaseModel):
    enabled: bool
    enabled_at: datetime | None = None


class TwoFAVerifyRequest(BaseModel):
    challenge_token: str
    method: Literal["totp", "recovery_code"] = "totp"
    code: str
    # 登录时这些跟 login 一致,verify 通过后调 _issue_tokens 用
    device_id: str | None = None
    device_name: str | None = None
    platform: str | None = None
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    client_type: Literal["app", "web"] = "app"


class TwoFADisableRequest(BaseModel):
    password: str
    code: str  # TOTP 6 位码,确认本人操作


class TwoFARegenerateRequest(BaseModel):
    code: str  # 当前 TOTP 6 位码


class TwoFARegenerateResponse(BaseModel):
    recovery_codes: list[str]


class DeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    last_ip: str | None = None
    last_seen_at: datetime
    created_at: datetime
    session_count: int = 1


class SyncChangeIn(BaseModel):
    # user-global change(category/account/tag)在新协议下不依附 ledger,这里
    # 允许 None。老 mobile 会发当前 ledger_id —— server 按 entity_type 强制
    # 路由(参考 .docs/user-global-refactor/plan.md §3.2),不依赖 client 一定
    # 填对。
    ledger_id: str | None = None
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime
    # 'user' = category/account/tag 等 user-global 资源(server 端 SyncChange.scope)
    # 'ledger' = budget/transaction/ledger 等 ledger-scoped
    # 老 mobile 不发该字段;server 兜底按 entity_type 推断,不依赖 client 一定填对。
    scope: str | None = None


class SyncPushRequest(BaseModel):
    device_id: str
    changes: list[SyncChangeIn]


class SyncPushResponse(BaseModel):
    accepted: int
    rejected: int
    conflict_count: int = 0
    conflict_samples: list[dict[str, Any]] = Field(default_factory=list)
    server_cursor: int
    server_timestamp: datetime


class SyncChangeOut(BaseModel):
    change_id: int
    # user-scope change(scope='user')的 ledger_id 是 sentinel '__user_global__';
    # ledger-scope 是真实账本 external_id。mobile 按 scope 字段决定 apply 路径,
    # ledger_id 仅做日志 / cursor 标识。
    ledger_id: str
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime
    updated_by_device_id: str | None
    # 'user' / 'ledger'。SyncChange.scope 直接 round-trip。
    scope: str = "ledger"


class SyncPullResponse(BaseModel):
    changes: list[SyncChangeOut]
    server_cursor: int
    has_more: bool


class SyncFullResponse(BaseModel):
    ledger_id: str
    snapshot: SyncChangeOut | None
    latest_cursor: int


class SyncLedgerOut(BaseModel):
    ledger_id: str
    path: str
    updated_at: datetime | None
    size: int
    metadata: dict[str, Any]
    role: MemberRole


BackupArtifactKind = Literal["db", "snapshot"]


class AdminBackupCreateRequest(BaseModel):
    ledger_id: str
    note: str | None = None


class AdminBackupCreateResponse(BaseModel):
    snapshot_id: str
    ledger_id: str
    created_at: datetime


class AdminBackupRestoreRequest(BaseModel):
    snapshot_id: str
    device_id: str | None = None


class AdminBackupRestoreResponse(BaseModel):
    restored: bool
    ledger_id: str
    change_id: int


class AdminBackupUploadSnapshotRequest(BaseModel):
    ledger_id: str
    payload: dict[str, Any]
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminBackupArtifactOut(BaseModel):
    id: str
    ledger_id: str
    kind: BackupArtifactKind
    file_name: str
    content_type: str | None
    checksum: str
    size: int
    created_at: datetime
    created_by: str
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdminBackupArtifactUploadResponse(AdminBackupArtifactOut):
    snapshot_id: str | None = None


class UserAdminOut(BaseModel):
    id: str
    email: str
    is_admin: bool
    is_enabled: bool
    created_at: datetime
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_version: int = 0


class UserAdminListOut(BaseModel):
    total: int
    items: list[UserAdminOut]


class UserAdminPatchRequest(BaseModel):
    # 允许改邮箱 / 启用状态。角色(is_admin)不在这里 —— 建用户时定好后
    # 就不能在 UI 改,想变更只能走 `make grant-admin EMAIL=` 之类的运维路径。
    # 密码改走独立端点 POST /admin/users/{id}/password,需要管理员自己的
    # 当前密码二次验证,避免 PATCH 这种"顺手改一下"造成密码误改。
    email: str | None = None
    is_enabled: bool | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not normalized:
            return None
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class UserAdminPasswordChangeRequest(BaseModel):
    """修改目标用户密码。admin_password 是**当前操作 admin 自己的**密码,
    用于二次验证 —— 防止 session 被挟持或 UI 误操作把别人密码改掉。
    new_password 至少 6 位,跟 register / create-user 对齐。"""

    admin_password: str = Field(min_length=1)
    new_password: str = Field(min_length=6)


class UserAdminCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=6)
    is_admin: bool = False
    is_enabled: bool = True

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized or "." not in normalized.split("@")[-1]:
            raise ValueError("Invalid email format")
        return normalized


class AdminOverviewOut(BaseModel):
    users_total: int
    users_enabled_total: int
    ledgers_total: int
    transactions_total: int
    accounts_total: int
    categories_total: int
    tags_total: int


class AdminLogEntryOut(BaseModel):
    """Ring buffer 一条日志;字段对应 RingBufferLogHandler.emit 的 dict。"""

    seq: int
    ts: str
    level: str
    logger: str
    message: str
    ledger_id: str | None = None
    user_id: str | None = None
    device_id: str | None = None


class AdminLogListOut(BaseModel):
    items: list[AdminLogEntryOut]
    capacity: int
    latest_seq: int


# ────────── 数据清理(替代旧 IntegrityScan)─────────────────────────


class DataCleanupRequest(BaseModel):
    """POST /admin/data-cleanup/clean 请求体 — records 直接来自 scan 接口。"""

    records: list["DataCleanupRecord"]


class DataCleanupResult(BaseModel):
    success_count: int
    failures: list["DataCleanupFailure"] = []


class DataCleanupFailure(BaseModel):
    record_key: str
    error: str


class DataCleanupRecord(BaseModel):
    """单条孤儿数据 — 直接复用 services.data_cleanup.OrphanRecord 形态,但作为
    schema 出现避免 router 依赖 services 类型。"""

    type: str  # OrphanType 枚举字符串值
    title: str
    subtitle: str
    user_id: str | None = None
    row_id: str | None = None
    sync_id: str | None = None
    file_path: str | None = None
    size_bytes: int | None = None
    extra: dict[str, Any] | None = None


class DataCleanupScanReport(BaseModel):
    db_orphans: list[DataCleanupRecord] = []
    file_orphans: list[DataCleanupRecord] = []
    sync_orphans: list[DataCleanupRecord] = []
    total_count: int = 0
    total_size_bytes: int = 0


DataCleanupRequest.model_rebuild()
DataCleanupResult.model_rebuild()


class ReadLedgerOut(BaseModel):
    ledger_id: str
    ledger_name: str
    currency: str
    month_start_day: int = 1
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    exported_at: datetime | None
    updated_at: datetime
    role: MemberRole
    is_shared: bool = False
    member_count: int = 1


class ReadLedgerDetailOut(ReadLedgerOut):
    source_change_id: int


class ReadTransactionOut(BaseModel):
    id: str
    tx_index: int
    tx_type: str
    amount: float
    happened_at: datetime
    note: str | None
    category_name: str | None
    category_kind: str | None
    account_name: str | None
    from_account_name: str | None
    to_account_name: str | None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | None
    tags_list: list[str] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    attachments: list[dict[str, Any]] | None
    # 账单标记(.docs/transaction-flags)。exclude_from_stats=不计入收支统计;
    # exclude_from_budget=不计入预算用量。两者独立,旧数据 default False。
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    # 0022 退款进度:该支出当前有效退款合计(不含 cancelled;仅 expense 有意义)。
    # 前端 refunded_total >= amount(容差 0.011)时给流水金额画删除线(已退款)。
    refunded_total: float = 0.0
    # 0022 退款进度(卡维度):有效退款中实际退回到本交易所在卡的钱 ——
    # unified 退到本卡 / split 腿里本卡那部分。跨卡退款不计入:钱到了别的卡,
    # 本卡这笔支出仍然成立(余额也没回来),不能画删除线。
    # 删除线口径:refunded_to_account >= amount(容差 0.011);原交易无账户的
    # 老数据退化为 refunded_total 总口径。
    refunded_to_account: float = 0.0
    # 交易级多币种(0018):currency_code=原币种(null 视作账本本位币);
    # native_amount=折账本本位币快照(null 时前端 fallback 用 amount)。
    currency_code: str | None = None
    native_amount: float | None = None
    # 优惠/折扣(0021):null=无优惠。形态见 WriteTransactionCreateRequest.discount。
    discount: dict[str, Any] | None = None
    # 转账手续费(0021):null=无手续费。仅 transfer 有意义。
    transfer_fee: dict[str, Any] | None = None
    # 多账户合并付款(0021):null=单账户付款。仅 expense 有意义。
    payment_legs: list[dict[str, Any]] | None = None
    # 成员分摊(0024):null=未分摊(统计按记账人归属)。仅 expense 有意义。
    # 形态 [{"memberId","memberName","ratio","amount"}, ...],sum(amount)==amount。
    # 按人拆分,不影响账户余额。
    member_shares: list[dict[str, Any]] | None = None
    # 归物(0029):关联物品的 sync_id / 名称。null = 未关联任何物品。
    # 挂上的支出会计入该物品的累计成本(维护/配件/延保)。
    item_id: str | None = None
    item_name: str | None = None
    # 定期存款利息(0030):关联定期存款的 sync_id;null = 未关联。
    # 挂上的收入(利息)会计入该存款「已收利息」。
    time_deposit_id: str | None = None
    # 0031:关联定存的展示名(账户名 + 币种本金,服务端合成)。仅展示用,
    # 存款被删为 None —— 交易不阻塞,只是流水里不显示名字。
    time_deposit_name: str | None = None
    # 0032:退款到账关联交易回显的原账单摘要(备注 > 分类名 >「金额 · 日期」)。
    # 仅退款 received 生成的 income 关联交易有值;其余交易恒为 None。
    refund_source_label: str | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None
    created_by_display_name: str | None = None
    created_by_avatar_url: str | None = None
    created_by_avatar_version: int | None = None
    # §7 共享账本:tx 创建/编辑分离显示 — last_edited 跟 created 不同时,UI
    # 显示 "X 创建 · Y 编辑";相同时只显示创建者。
    last_edited_by_user_id: str | None = None
    last_edited_by_email: str | None = None
    last_edited_by_display_name: str | None = None
    last_edited_by_avatar_url: str | None = None
    last_edited_by_avatar_version: int | None = None
    # 资产流水余额:该交易完成后,其主账户(支出/收入=账户;转账=转出账户;
    # 账户筛选模式下=被筛选账户,转账双向都计入)的余额。仅 `with_balance=true`
    # 时在服务端计算并返回;否则为 null。口径与 WorkspaceAccount.balance 一致
    # (initial + income - expense + transfer_to - transfer_from,amount 口径),
    # 因此账户最后一笔交易的余额 = 该账户当前余额。多账本/多币种场景下为简化口径。
    balance_after: float | None = None
    # 多账户合并付款(0021)时,每笔交易完成后各个腿账户的余额,形如
    # {account_sync_id: balance_after_for_that_leg}。仅 `with_balance=true` 且
    # 该笔是合并付款时才有值;单账户交易此字段为 null(前端回退到 balance_after)。
    # 用途:交易流水中合并付款要把每个参与账户的余额分别展示,而非只显示一个。
    balance_by_account: dict[str, float] | None = None


class ReadAccountOut(BaseModel):
    id: str
    name: str
    account_type: str | None
    currency: str | None
    initial_balance: float | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None
    # 扩展字段(mobile sync_engine 一直在 push 这些,server 现在落库 + round-trip,
    # web 编辑也能完整保存):
    note: str | None = None
    credit_limit: float | None = None
    billing_day: int | None = None
    payment_due_day: int | None = None
    bank_name: str | None = None
    card_last_four: str | None = None
    # 完整卡号(0026)。用户手动录入,card_last_four 可由其后四位派生。
    card_number: str | None = None
    # 账户图标 key(0026):内置银行/支付图标注册表主键;空 = 按 account_type 回退。
    icon: str | None = None
    # 账户别名(0027):展示用短名,所有账户类型可用。空 = 沿用 name。
    alias: str | None = None
    # 出账日(账单日)当天计入当期账单(0027,信用卡口径)。非信用卡账户无意义。
    billing_day_in_current: bool = True
    # 账户隐藏(issue #240):只影响前端选择器/列表呈现,服务端不做任何统计
    # 过滤(D1)。WorkspaceAccountOut 继承本字段,不单独重复声明。
    hidden: bool = False


class ReadCategoryOut(BaseModel):
    id: str
    name: str
    kind: str
    level: int | None
    sort_order: int | None
    icon: str | None
    icon_type: str | None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None
    parent_sync_id: str | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class ReadTagOut(BaseModel):
    id: str
    name: str
    color: str | None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None


class ReadBudgetOut(BaseModel):
    """预算只读视图。mobile 同步上来的 snapshot.budgets 逐条 map 过来。
    category_name 不进 snapshot,这里从 categoryId 反查填上,跟 tx/account
    同一套 id→name 映射思路。"""
    id: str
    """`total` = 总预算(全账本),`category` = 分类预算"""
    type: str
    category_id: str | None = None
    category_name: str | None = None
    amount: float
    """`monthly` / `weekly` / `yearly`"""
    period: str
    start_day: int
    enabled: bool
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class TimeDepositPayoutPlanIn(BaseModel):
    """到息计划写入形态(0028):到息日 + 该次金额 + 可选备注。

    amount 是自由金额 —— 不区分「利息」还是「本息合计」,系统只负责记录。
    """

    # 'YYYY-MM-DD' 或 ISO datetime;服务端只取日期部分。
    date: str
    amount: float = Field(gt=0)
    note: str | None = None


class TimeDepositPayoutPlanOut(BaseModel):
    """到息计划只读形态:is_due 由读端点按「今天」派生(不落库)。"""

    date: str
    amount: float
    note: str | None = None
    is_due: bool = False


class ReadTimeDepositOut(BaseModel):
    """定期存款只读视图(0021,Cloud issue #80)。

    派生字段(expected_interest / days_remaining / is_matured)由读端点按
    "今天"现算,不落库 —— 避免后台任务刷状态,也避免跨时区日期口径分裂。
    """

    id: str
    principal: float
    currency: str
    # 年利率小数(0.0325 = 3.25%)。
    annual_rate: float
    term_months: int
    start_date: datetime
    maturity_date: datetime
    status: str
    account_id: str | None = None
    account_name: str | None = None
    auto_renew: bool = False
    interest_mode: str = "simple"
    note: str | None = None
    withdrawn_at: datetime | None = None
    actual_interest: float | None = None
    # --- 派生(读时计算)--------------------------------------------------- #
    # 预期利息:withdrawn 时 = actual_interest(实得);否则按利率 + 期限现算。
    expected_interest: float = 0.0
    # 距到期天数(负数 = 已过期未支取;withdrawn 恒 0)。
    days_remaining: int = 0
    # 今天是否已达到期日(status != 'withdrawn' 时有意义)。
    is_matured: bool = False
    # --- 到息计划(0028,多笔)--------------------------------------------- #
    # 计划只作预期/提醒:不生成交易、不进余额与收支统计。
    payout_plans: list[TimeDepositPayoutPlanOut] = Field(default_factory=list)
    # 计划金额合计(展示用)。
    payout_plans_total: float = 0.0
    # 下一个尚未到息的计划日期;全部已到息或无计划 → None。
    next_payout_date: str | None = None
    # 已收利息(0030):关联该存款、且为收入(利息)的交易金额合计(按账本本位币)。
    # 与「到息计划」(仅预期/提醒)不同,这是真实入账的利息流水。
    interest_received: float = 0.0
    # 已收利息笔数(关联交易中 tx_type='income' 的条数)。
    interest_tx_count: int = 0
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadItemOut(BaseModel):
    """归物 · 物品持有只读视图(0029)。

    记录一件物品的购买日期与金额,按存活时间摊出日均成本,跟踪保修,直到
    二手卖出或报废才停止记录。派生字段(held_days / total_cost / daily_cost /
    warranty_status)由读端点按「今天」现算,不落库 —— 处置后天数定格,不随
    时间继续增长。
    """

    id: str
    name: str
    purchase_date: datetime
    purchase_amount: float
    warranty_until: datetime | None = None
    # 'in_use'(在用,默认) | 'disposed'(已处置)
    status: str = "in_use"
    # 处置方式:'sold'(二手卖出,有收入) | 'scrapped'(报废)。仅 disposed 有意义。
    disposal_type: str | None = None
    disposal_date: datetime | None = None
    disposal_amount: float | None = None
    category_id: str | None = None
    category_name: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    note: str | None = None
    # --- 派生(读时计算)--------------------------------------------------- #
    # 持有天数(含购买当天;disposed 时定格在处置日)。
    held_days: int = 0
    # 净成本 = 购买金额 + 关联交易支出 - 二手卖出收入(可为负 = 处置回本还赚)。
    total_cost: float = 0.0
    # 日均成本 = 净成本 / 持有天数。
    daily_cost: float = 0.0
    # 挂在这件物品上的交易支出累计(维护/配件/延保……)。
    linked_expense: float = 0.0
    # 挂在这件物品上的交易收入累计(回收款 / 折价补偿 / 保修理赔入账……),
    # 同样抵减净成本。
    linked_income: float = 0.0
    # 'none'(没记保修) | 'active'(保修中) | 'expired'(已过保)
    # —— 过保后物品仍存活、继续摊成本。
    warranty_status: str = "none"
    # 保修剩余天数;expired 时是负的过期天数;none 时为 None。
    warranty_days_remaining: int | None = None
    is_disposed: bool = False
    # 关联交易笔数(物品详情列出所有关联交易)。
    linked_tx_count: int = 0
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadItemReturnOut(BaseModel):
    """归物(旧:物品归还/退款流程)只读视图(0021,App issue #465 退款诉求)。"""

    id: str
    name: str
    original_tx_id: str | None = None
    original_amount: float | None = None
    refund_amount: float
    happened_at: datetime
    reason: str | None = None
    # 'pending'(待归还) | 'returned'(已归还待退款) | 'refunded'(已退款入账)
    # | 'cancelled'(已取消)
    status: str
    account_id: str | None = None
    account_name: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    note: str | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadRefundOut(BaseModel):
    """交易退款只读视图(0022,App issue #465 退款功能)。

    挂在原始支出交易上的退款记录;同一原单可有多笔(部分退/多次退)。
    refunded_total / remaining_refundable 由读端点现算(按原单聚合,
    cancelled 不计入),不落库。
    """

    id: str
    original_tx_id: str
    original_amount: float | None = None
    amount: float
    currency: str = "CNY"
    account_id: str | None = None
    account_name: str | None = None
    happened_at: datetime
    reason: str | None = None
    # 'pending'(待退款/处理中) | 'received'(已到账) | 'cancelled'(已取消)
    status: str
    note: str | None = None
    # 退款分摊模式(0025):'unified'(默认,统一退至单一账户) | 'split'(分别退款)。
    refund_mode: str = "unified"
    # 分别退款(0025)腿:解析自 refund_legs_json 的
    #   [{"accountId": "a1", "accountName": "工行卡", "amount": 60.0}, ...]
    # 仅 split 模式非空;unified 时为 None(退到 account_id)。
    refund_legs: list[dict[str, Any]] | None = None
    # --- 派生(读时按原单聚合)--------------------------------------------- #
    # 该原单当前有效退款合计(不含 cancelled;含本笔)。
    refunded_total: float = 0.0
    # 该原单剩余可退金额(original_amount - refunded_total,负值钳 0)。
    remaining_refundable: float = 0.0
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadMemberOut(BaseModel):
    """账本内自定义成员只读视图(0024)。

    账本自己维护的「人」名单(爸爸/妈妈/我),不要求登录账号 —— 跟共享账本的
    LedgerMember 是两套东西。用途:给支出账单做**人头分摊**,让成员维度统计
    能回答「谁花了多少」。
    """

    id: str
    name: str
    note: str | None = None
    sort_order: int = 0
    # 成员图标 key(人物语义 Material Symbols 名);空 = 前端回退默认图标。
    icon: str | None = None
    # 关联的记账人(登录用户 id);空 = 未关联。
    linked_user_id: str | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadLoanOut(BaseModel):
    """借款只读视图(0023)。

    direction: 'borrowed'(借入) | 'lent'(借出)。
    repay_progress / days_to_due / is_overdue 由读端点按今天现算,不落库。
    """

    id: str
    direction: str
    counterparty: str
    principal: float
    currency: str = "CNY"
    interest_rate: float = 0.0
    started_at: datetime
    due_at: datetime | None = None
    repaid_amount: float = 0.0
    # 'active'(在借) | 'repaid'(已结清) | 'cancelled'(作废)
    status: str
    account_id: str | None = None
    account_name: str | None = None
    note: str | None = None
    # 登记还款(0032)明细:[{id, amount(本金), interestAmount(利息), repayAccountId,
    # repayAccountName, happenedAt, note}, ...];本金不进收支,利息记为支出 / 收入。
    repayments: list = []
    # --- 派生(读时按今天现算)--------------------------------------------- #
    # 还款进度 0..1(repaid_amount / principal)。
    repay_progress: float = 0.0
    # 距到期天数(无到期日 = None);负数 = 已过期未结清。
    days_to_due: int | None = None
    # 已逾期(due_at 早于今天 且 status='active')。
    is_overdue: bool = False
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadReimbursementOut(BaseModel):
    """报销只读视图(0023)。

    title 报销事由;payer 垫付方;reimburser 给付方;
    status: 'pending'(待报销) | 'reimbursed'(已到账) | 'cancelled'(已取消)。
    """

    id: str
    title: str
    payer: str | None = None
    reimburser: str | None = None
    amount: float
    currency: str = "CNY"
    category_id: str | None = None
    category_name: str | None = None
    happened_at: datetime
    status: str
    account_id: str | None = None
    account_name: str | None = None
    related_tx_id: str | None = None
    note: str | None = None
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None


class ReadBudgetUsageItemOut(BaseModel):
    """单个 budget 当前周期的已用金额。分类预算的 used 包含该分类自身 + 所有
    parent_sync_id 指向它的子分类支出(跟手机端 local_budget_repository 的
    OR c.parent_id = ? 语义对齐)。"""
    budget_id: str
    used: float


class ReadBudgetUsageOut(BaseModel):
    """`/ledgers/{id}/budgets/usage` 返回。周期窗口统一取账本 month_start_day
    (设计 D5:budget.start_day 弃用,所有 budget 共享同一周期),前端只用 used 数字。"""
    items: list[ReadBudgetUsageItemOut] = Field(default_factory=list)


class WorkspaceTransactionOut(ReadTransactionOut):
    pass


class WorkspaceTransactionPageOut(BaseModel):
    items: list[WorkspaceTransactionOut] = Field(default_factory=list)
    total: int
    limit: int
    offset: int


class WorkspaceAccountOut(ReadAccountOut):
    # 跨 workspace 对该账户聚合后的统计，列表接口一次性给，无需前端再聚合。
    # balance 包含 initialBalance + (income - expense)；income/expense 只统计本
    # 账户作为 accountId 的收支条目（不含 transfer 的对手方）。
    tx_count: int | None = None
    income_total: float | None = None
    expense_total: float | None = None
    balance: float | None = None


class WorkspaceCategoryOut(ReadCategoryOut):
    # 跨账本按该分类聚合的笔数。Web 列表展示用,跟 tags 的 tx_count 对齐。
    # 不带 expense/income total — 分类本身已经按 kind 区分(支出/收入),
    # 累计金额可在分类详情页另行查询。None = 历史接口可选不提供。
    tx_count: int | None = None


class WorkspaceTagOut(ReadTagOut):
    # 跨所有账本按该标签聚合的交易统计，列表接口一次性给。
    # 全部 None = list_workspace_tags 可选择不提供（legacy 调用）。
    tx_count: int | None = None
    expense_total: float | None = None
    income_total: float | None = None


AnalyticsScope = Literal["month", "year", "all"]
AnalyticsMetric = Literal["expense", "income", "balance"]


class WorkspaceLedgerCountsOut(BaseModel):
    """单账本全量记账统计，对齐 mobile `getCountsForLedger`：笔数 + 首次记账到
    今天的天数（`julianday(now) - julianday(MIN(happened_at)) + 1`）+ 有数据的天数
    （distinct DATE，备用）。首页"记账笔数 / 记账天数"读这里，不依赖 analytics scope。"""

    tx_count: int
    # "记账天数"：从首次记账那天算到今天（含当天），对应 mobile 的 dayCount。
    days_since_first_tx: int
    # 有数据的天数：只计入有 tx 的日期数，保留给别处使用。
    distinct_days: int
    first_tx_at: datetime | None = None


class WorkspaceAnalyticsSummaryOut(BaseModel):
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    # 记账天数：distinct(DATE(happened_at))。前端首页用来做"已记账 X 天"卡片。
    distinct_days: int = 0
    # 首次记账时间：min(happened_at)。配合 distinct_days 算"持续记账时长"。
    first_tx_at: datetime | None = None
    last_tx_at: datetime | None = None


class WorkspaceAnalyticsSeriesItemOut(BaseModel):
    bucket: str
    expense: float
    income: float
    balance: float


class WorkspaceAnalyticsCategoryRankOut(BaseModel):
    category_name: str
    total: float
    tx_count: int


class WorkspaceAnalyticsAnomalyAttributionOut(BaseModel):
    """异常月份的归因 — 某分类在该月超出"该分类其他月份中位数"的部分。"""

    category_name: str
    amount: float  # 该分类在异常月的总支出
    # 该分类在其他月份的中位数(本月独有时为 0)
    median_others: float
    # amount / median_others;median_others=0(本月独有)时为 None,前端显示"本月独有"。
    multiplier: float | None = None


class WorkspaceAnalyticsAnomalyMonthOut(BaseModel):
    """异常月份 — expense 显著高于已发生月份的 baseline。算法见
    `.docs/dashboard-anomaly-budget/plan.md`:
      baseline = median(已发生月份的 expense)
      异常判定:expense > baseline × 1.2 AND expense - baseline > ¥200
    """

    bucket: str  # "2026-05"
    expense: float
    baseline: float
    # (expense - baseline) / baseline,前端展示百分比
    deviation_pct: float
    # top 1-2 个归因分类,按 diff 降序
    top_attributions: list[WorkspaceAnalyticsAnomalyAttributionOut] = Field(
        default_factory=list
    )


class WorkspaceAnalyticsRangeOut(BaseModel):
    scope: AnalyticsScope
    metric: AnalyticsMetric
    period: str | None
    start_at: datetime | None
    end_at: datetime | None


class WorkspaceAnalyticsOut(BaseModel):
    summary: WorkspaceAnalyticsSummaryOut
    series: list[WorkspaceAnalyticsSeriesItemOut] = Field(default_factory=list)
    category_ranks: list[WorkspaceAnalyticsCategoryRankOut] = Field(default_factory=list)
    # 仅在 scope=year 填;月份数 < 3 时返回空 list(baseline 不稳)。
    anomaly_months: list[WorkspaceAnalyticsAnomalyMonthOut] = Field(default_factory=list)
    range: WorkspaceAnalyticsRangeOut


class ReadSummaryOut(BaseModel):
    ledger_id: str
    transaction_count: int
    income_total: float
    expense_total: float
    balance: float
    latest_happened_at: datetime | None


class WriteCommitMeta(BaseModel):
    ledger_id: str
    base_change_id: int
    new_change_id: int
    server_timestamp: datetime
    idempotency_replayed: bool = False
    entity_id: str | None = None


class WriteBaseRequest(BaseModel):
    base_change_id: int = Field(ge=0)
    request_id: str | None = Field(default=None, max_length=128)


class WriteLedgerCreateRequest(BaseModel):
    ledger_id: str | None = Field(default=None, min_length=3, max_length=128)
    ledger_name: str = Field(min_length=1, max_length=255)
    currency: str = Field(default="CNY", min_length=1, max_length=16)
    month_start_day: int = Field(default=1, ge=1, le=28)


class WriteLedgerMetaUpdateRequest(WriteBaseRequest):
    ledger_name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = Field(default=None, min_length=1, max_length=16)
    month_start_day: int | None = Field(default=None, ge=1, le=28)


class WriteTransactionCreateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer"] = "expense"
    amount: float
    happened_at: datetime
    note: str | None = None
    category_name: str | None = None
    category_kind: Literal["expense", "income", "transfer"] | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | list[str] | None = None
    tag_ids: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None
    # 账单标记(.docs/transaction-flags)。新建默认 False。
    exclude_from_stats: bool = False
    exclude_from_budget: bool = False
    # 交易级多币种(0018):Web 币种录入。currency_code=原币种;native_amount=
    # 折账本本位币快照(前端按汇率算好传入)。不传 → item 不产生字段(旧行为)。
    currency_code: str | None = None
    native_amount: float | None = None
    # 优惠/折扣(0021,App issue #465):仅 expense。形态
    # {"mode": "percent"|"amount", "value": ..., "couponName": ...,
    #  "originalAmount": ..., "discountAmount": ..., "finalAmount": ...}
    # finalAmount 恒等于 amount(实付)。服务端只做结构透传 + 落库,不重算。
    discount: dict[str, Any] | None = None
    # 转账手续费/优惠(0021):仅 transfer。形态
    # {"amount": ..., "mode": "deduct_from_source"|"deduct_from_target"
    #  |"separate_account"|"discount", "feeAccountId": ..., "feeAccountName": ...}
    # discount=转出方少扣 amount(实扣 amount-优惠额,到账方全收 amount)。
    transfer_fee: dict[str, Any] | None = None
    # 多账户合并付款(0021):仅 expense。形态
    # [{"accountId": ..., "accountName": ..., "amount": ...}, ...]
    # sum(legs) 必须等于 amount(容差 0.01),snapshot_mutator 校验。
    payment_legs: list[dict[str, Any]] | None = None
    # 成员分摊(0024):仅 expense。形态
    # [{"memberId": ..., "memberName": ..., "ratio": ..., "amount": ...}, ...]
    # sum(shares.amount) 必须等于 amount(容差 0.011)。按人拆分,不动账户余额。
    member_shares: list[dict[str, Any]] | None = None
    # 归物(0029):关联物品的 sync_id。挂上后该笔支出计入物品累计成本
    # (维护/配件/延保……);不传 = 不关联任何物品。
    item_id: str | None = None
    # 定期存款利息(0030):关联定期存款的 sync_id。挂上后该笔收入(利息)计入
    # 该存款「已收利息」;不传 = 不关联任何存款。
    time_deposit_id: str | None = None


class WriteTransactionUpdateRequest(WriteBaseRequest):
    tx_type: Literal["expense", "income", "transfer"] | None = None
    amount: float | None = None
    happened_at: datetime | None = None
    note: str | None = None
    category_name: str | None = None
    category_kind: Literal["expense", "income", "transfer"] | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    category_id: str | None = None
    account_id: str | None = None
    from_account_id: str | None = None
    to_account_id: str | None = None
    tags: str | list[str] | None = None
    tag_ids: list[str] | None = None
    attachments: list[dict[str, Any]] | None = None
    # 账单标记(.docs/transaction-flags)。None = 不变(沿用 update 其它字段语义)。
    exclude_from_stats: bool | None = None
    exclude_from_budget: bool | None = None
    # 交易级多币种(0018):显式传入优先(mutator 不再联动);None = 不变。
    currency_code: str | None = None
    native_amount: float | None = None
    # 0021 结构化扩展字段:显式传 null = 清除该扩展;缺键 = 不动。
    discount: dict[str, Any] | None = None
    transfer_fee: dict[str, Any] | None = None
    payment_legs: list[dict[str, Any]] | None = None
    member_shares: list[dict[str, Any]] | None = None
    # 归物(0029):关联物品;显式 null = 解除关联。
    item_id: str | None = None
    # 定期存款利息(0030):关联交易;显式 null = 解除关联。
    time_deposit_id: str | None = None


# --------------------------------------------------------------------------- #
# 定期存款 / 归物(0021)/ 交易退款(0022)                                         #
# --------------------------------------------------------------------------- #


class WriteTimeDepositCreateRequest(WriteBaseRequest):
    principal: float = Field(gt=0)
    currency: str | None = None
    # 年利率小数(0.0325 = 3.25%)。允许 0(活期式定存),不允许负。
    annual_rate: float = Field(default=0.0, ge=0)
    term_months: int = Field(default=12, ge=1, le=600)
    # 'YYYY-MM-DD' 或 ISO datetime;服务端只取日期部分。
    start_date: str
    # 到期日可不传 —— 服务端按 start_date + term_months 推算。
    maturity_date: str | None = None
    status: Literal["holding", "matured", "withdrawn"] = "holding"
    account_id: str | None = None
    account_name: str | None = None
    auto_renew: bool = False
    interest_mode: Literal["simple", "compound"] = "simple"
    note: str | None = None
    withdrawn_at: str | None = None
    actual_interest: float | None = None
    # 到息计划(0028,多笔):可挂多条;不传 = 无计划。
    payout_plans: list[TimeDepositPayoutPlanIn] | None = None


class WriteTimeDepositUpdateRequest(WriteBaseRequest):
    # 全部可选(partial update);显式 None = 清除可空字段。
    principal: float | None = Field(default=None, gt=0)
    currency: str | None = None
    annual_rate: float | None = Field(default=None, ge=0)
    term_months: int | None = Field(default=None, ge=1, le=600)
    start_date: str | None = None
    maturity_date: str | None = None
    status: Literal["holding", "matured", "withdrawn"] | None = None
    account_id: str | None = None
    account_name: str | None = None
    auto_renew: bool | None = None
    interest_mode: Literal["simple", "compound"] | None = None
    note: str | None = None
    withdrawn_at: str | None = None
    actual_interest: float | None = None
    # 到息计划(0028,多笔):不传 = 保持现值;显式 [] 或 null = 清空计划。
    payout_plans: list[TimeDepositPayoutPlanIn] | None = None


class WriteItemCreateRequest(WriteBaseRequest):
    """归物 · 物品持有创建(0029)。"""

    name: str = Field(min_length=1, max_length=255)
    # 'YYYY-MM-DD' 或 ISO datetime;服务端只取日期部分。
    purchase_date: str
    # 购买金额(账本本位币)。允许 0(赠品),不允许负。
    purchase_amount: float = Field(default=0.0, ge=0)
    # 保修到期日;不传 = 未记录保修。到期只是过保,物品仍存活。
    warranty_until: str | None = None
    status: Literal["in_use", "disposed"] = "in_use"
    # disposed 时必填语义:sold(有回收款)/ scrapped(无)。缺则按 scrapped。
    disposal_type: Literal["sold", "scrapped"] | None = None
    disposal_date: str | None = None
    disposal_amount: float | None = Field(default=None, ge=0)
    category_id: str | None = None
    category_name: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    note: str | None = None


class WriteItemUpdateRequest(WriteBaseRequest):
    """归物 · 物品持有更新(0029)。全部可选(partial update)。

    status 改回 in_use 会清掉 disposal_* 三个字段(由 validate_item 处理),
    避免「二手卖掉后又改回在用」时留下残余的处置信息。
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    purchase_date: str | None = None
    purchase_amount: float | None = Field(default=None, ge=0)
    warranty_until: str | None = None
    status: Literal["in_use", "disposed"] | None = None
    disposal_type: Literal["sold", "scrapped"] | None = None
    disposal_date: str | None = None
    disposal_amount: float | None = Field(default=None, ge=0)
    category_id: str | None = None
    category_name: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    note: str | None = None


class WriteItemReturnCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    original_tx_id: str | None = None
    original_amount: float | None = Field(default=None, ge=0)
    refund_amount: float = Field(default=0.0, ge=0)
    # 'YYYY-MM-DD' 或 ISO datetime;服务端只取日期部分。
    happened_at: str
    reason: str | None = None
    status: Literal["pending", "returned", "refunded", "cancelled"] = "pending"
    account_id: str | None = None
    account_name: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    note: str | None = None


class WriteItemReturnUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    original_tx_id: str | None = None
    original_amount: float | None = Field(default=None, ge=0)
    refund_amount: float | None = Field(default=None, ge=0)
    happened_at: str | None = None
    reason: str | None = None
    status: Literal["pending", "returned", "refunded", "cancelled"] | None = None
    account_id: str | None = None
    account_name: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    note: str | None = None


class WriteRefundCreateRequest(WriteBaseRequest):
    # 关联原始支出交易 sync_id(必填;服务端校验存在且 tx_type='expense',
    # 并把原单金额快照到 original_amount)。
    original_tx_id: str = Field(min_length=1, max_length=255)
    # 本笔退款金额(> 0)。同单多笔累计(非 cancelled)不得超原单金额。
    amount: float = Field(gt=0)
    # 缺省取原交易币种。
    currency: str | None = None
    # 退款去向账户(退款入账/期待的账户)。
    account_id: str | None = None
    account_name: str | None = None
    # 'YYYY-MM-DD' 或 ISO datetime;含时分秒时服务端**保留**时间(退款精确到分钟)。
    happened_at: str
    reason: str | None = None
    status: Literal["pending", "received", "cancelled"] = "pending"
    note: str | None = None
    # 退款分摊模式(0025):'unified'(默认,统一退至单一账户) | 'split'(分别退款)。
    refund_mode: Literal["unified", "split"] = "unified"
    # 分别退款(0025)腿:[{"accountId","accountName","amount"},...];sum==amount。
    # 仅 split 模式有意义;unified 时后端忽略并清空。
    refund_legs: list[dict[str, Any]] | None = None


class WriteRefundUpdateRequest(WriteBaseRequest):
    # 全部可选(partial update);显式 None = 清除可空字段。
    original_tx_id: str | None = Field(default=None, min_length=1, max_length=255)
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    happened_at: str | None = None
    reason: str | None = None
    status: Literal["pending", "received", "cancelled"] | None = None
    note: str | None = None
    # 退款分摊模式(0025)。
    refund_mode: Literal["unified", "split"] | None = None
    # 分别退款(0025)腿;[...sum==amount];仅 split 有意义。
    refund_legs: list[dict[str, Any]] | None = None


class WriteMemberCreateRequest(WriteBaseRequest):
    """账本内自定义成员(0024)。name 必填;sort_order 仅影响列表展示顺序。

    icon / linked_user_id 是成员档案:图标只影响展示;关联记账人影响统计
    归属(该用户记的、无分摊的交易并入本成员)。
    """

    name: str = Field(min_length=1, max_length=64)
    note: str | None = None
    sort_order: int = 0
    icon: str | None = Field(default=None, max_length=64)
    linked_user_id: str | None = Field(default=None, max_length=36)


class WriteMemberUpdateRequest(WriteBaseRequest):
    # 全部可选(partial update);显式 None = 清除可空字段。
    name: str | None = Field(default=None, min_length=1, max_length=64)
    note: str | None = None
    sort_order: int | None = None
    icon: str | None = Field(default=None, max_length=64)
    linked_user_id: str | None = Field(default=None, max_length=36)


class WriteLoanCreateRequest(WriteBaseRequest):
    direction: Literal["borrowed", "lent"] = "borrowed"
    counterparty: str = Field(min_length=1, max_length=500)
    principal: float = Field(gt=0)
    currency: str | None = None
    # 年利率小数(0.05 = 5%)。允许 0,不允许负。
    interest_rate: float = Field(default=0.0, ge=0)
    # 'YYYY-MM-DD' 或 ISO datetime;服务端只取日期部分。
    started_at: str
    # 到期日可空(无限期)。
    due_at: str | None = None
    status: Literal["active", "repaid", "cancelled"] = "active"
    account_id: str | None = None
    account_name: str | None = None
    note: str | None = None


class WriteLoanUpdateRequest(WriteBaseRequest):
    # 全部可选(partial update);显式 None = 清除可空字段。
    # 注:已还金额(repaidAmount)不再经此接口手工改 —— 由「登记还款」端点维护,
    # 保证本金流水 / 利息流水与所选银行卡一致。
    direction: Literal["borrowed", "lent"] | None = None
    counterparty: str | None = Field(default=None, min_length=1, max_length=500)
    principal: float | None = Field(default=None, gt=0)
    currency: str | None = None
    interest_rate: float | None = Field(default=None, ge=0)
    started_at: str | None = None
    due_at: str | None = None
    status: Literal["active", "repaid", "cancelled"] | None = None
    account_id: str | None = None
    account_name: str | None = None
    note: str | None = None


class LoanRepaymentCreateRequest(WriteBaseRequest):
    """登记一笔借款还款(0032)。前端生成 repayment_id 作为幂等 / 撤销锚点。

    - amount:本次还的**本金**部分(不进收支统计)。
    - interest_amount:本次利息(可选);> 0 时作为正常支出 / 收入计入统计。
    - repay_account_id / repay_account_name:还款所用银行卡(用户自选,不再强制
      走借款账户)。
    - happened_at:还款发生时间;note:备注。
    """

    repayment_id: str = Field(min_length=1, max_length=64)
    amount: float = Field(gt=0)
    interest_amount: float = Field(default=0.0, ge=0)
    repay_account_id: str = Field(min_length=1, max_length=255)
    repay_account_name: str | None = None
    happened_at: str
    note: str | None = None


class WriteReimbursementCreateRequest(WriteBaseRequest):
    # 报销事由(必填)。
    title: str = Field(min_length=1, max_length=500)
    # 实际垫付方(通常是我,可空)。
    payer: str | None = None
    # 报销给付方(公司 / 他人,可空)。
    reimburser: str | None = None
    amount: float = Field(gt=0)
    currency: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    # 'YYYY-MM-DD' 或 ISO datetime;服务端只取日期部分。
    happened_at: str
    status: Literal["pending", "reimbursed", "cancelled"] = "pending"
    account_id: str | None = None
    account_name: str | None = None
    # 关联垫付交易 sync_id(弱引用,可空)。
    related_tx_id: str | None = None
    note: str | None = None


class WriteReimbursementUpdateRequest(WriteBaseRequest):
    # 全部可选(partial update);显式 None = 清除可空字段。
    title: str | None = Field(default=None, min_length=1, max_length=500)
    payer: str | None = None
    reimburser: str | None = None
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = None
    category_id: str | None = None
    category_name: str | None = None
    happened_at: str | None = None
    status: Literal["pending", "reimbursed", "cancelled"] | None = None
    account_id: str | None = None
    account_name: str | None = None
    related_tx_id: str | None = None
    note: str | None = None



class WriteEntityDeleteRequest(WriteBaseRequest):
    pass


class WriteAccountCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None
    # 扩展字段:跟 mobile lib/data/db.dart Account 表对齐,跨端可编辑。
    note: str | None = None
    credit_limit: float | None = None
    billing_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    bank_name: str | None = None
    card_last_four: str | None = Field(default=None, max_length=8)
    # 完整卡号(0026)。card_last_four 缺省时服务端从其后四位派生。
    card_number: str | None = Field(default=None, max_length=64)
    # 账户图标 key(0026):内置银行/支付图标注册表主键;空 = 按 account_type 回退。
    icon: str | None = Field(default=None, max_length=64)
    # 账户别名(0027):展示用短名。空串 / None = 无别名(沿用 name)。
    alias: str | None = Field(default=None, max_length=255)
    # 出账日计入当期(0027):true = 账单日当天消费入本期账单;false = 入下期。
    billing_day_in_current: bool = True
    # 账户隐藏(issue #240):新建一般为 false,留字段以备批量导入。写路径接线
    # (mutator / write handler)是 Task 4,本字段暂不生效。
    hidden: bool = False


class WriteAccountUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_type: str | None = None
    currency: str | None = None
    initial_balance: float | None = None
    note: str | None = None
    credit_limit: float | None = None
    billing_day: int | None = Field(default=None, ge=1, le=31)
    payment_due_day: int | None = Field(default=None, ge=1, le=31)
    bank_name: str | None = None
    card_last_four: str | None = Field(default=None, max_length=8)
    # 完整卡号 + 图标 key(0026)。None = 清空(PATCH exclude_unset 下不带 key
    # 才是不改)。
    card_number: str | None = Field(default=None, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    # 别名(0027)。None = 清空别名(退回显示 name),不带 key = 不改。
    alias: str | None = Field(default=None, max_length=255)
    # 出账日计入当期(0027)。None = 不改(PATCH exclude_unset);显式 true/false
    # 才改 —— False 与"不传"可区分,不会被默认值吞掉。
    billing_day_in_current: bool | None = None
    # None = 不改(PATCH exclude_unset)。写路径接线是 Task 4,本字段暂不生效。
    hidden: bool | None = None


class WriteBudgetCreateRequest(WriteBaseRequest):
    type: Literal["total", "category"]
    category_id: str | None = None
    amount: float = Field(gt=0)
    period: Literal["monthly", "weekly", "yearly"] = "monthly"
    # deprecated:预算周期已统一跟随账本 month_start_day(D5),该字段仅作兼容保留
    start_day: int = Field(default=1, ge=1, le=28)
    enabled: bool = True


class WriteBudgetUpdateRequest(WriteBaseRequest):
    amount: float | None = Field(default=None, gt=0)
    period: Literal["monthly", "weekly", "yearly"] | None = None
    # deprecated:预算周期已统一跟随账本 month_start_day(D5),该字段仅作兼容保留
    start_day: int | None = Field(default=None, ge=1, le=28)
    enabled: bool | None = None


class WriteCategoryCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"]
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None
    parent_sync_id: str | None = None


class WriteCategoryUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: Literal["expense", "income", "transfer"] | None = None
    level: int | None = None
    sort_order: int | None = None
    icon: str | None = None
    icon_type: str | None = None
    custom_icon_path: str | None = None
    icon_cloud_file_id: str | None = None
    icon_cloud_sha256: str | None = None
    parent_name: str | None = None
    parent_sync_id: str | None = None


class WriteTagCreateRequest(WriteBaseRequest):
    name: str = Field(min_length=1, max_length=255)
    color: str | None = None


class WriteTagUpdateRequest(WriteBaseRequest):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    color: str | None = None


class AdminDeviceOut(BaseModel):
    id: str
    name: str
    platform: str
    app_version: str | None = None
    os_version: str | None = None
    device_model: str | None = None
    last_ip: str | None = None
    created_at: datetime
    last_seen_at: datetime
    is_online: bool
    user_id: str
    user_email: str


class AdminDeviceListOut(BaseModel):
    total: int
    items: list[AdminDeviceOut]


class AttachmentUploadOut(BaseModel):
    file_id: str
    ledger_id: str
    sha256: str
    size: int
    mime_type: str | None = None
    file_name: str | None = None
    created_at: datetime


class AttachmentExistsItem(BaseModel):
    sha256: str
    exists: bool
    file_id: str | None = None
    size: int | None = None
    mime_type: str | None = None


class AttachmentBatchExistsRequest(BaseModel):
    ledger_id: str
    sha256_list: list[str] = Field(default_factory=list)


class AttachmentBatchExistsResponse(BaseModel):
    items: list[AttachmentExistsItem] = Field(default_factory=list)


# ============================================================================
# Backup schemas — Web UI 写入 / 读取请求和响应。详见 .docs/backup-rclone-plan.md
# ============================================================================


class BackupRemoteCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")
    backend_type: str = Field(min_length=1, max_length=32)
    # rclone 配置字段(类型不同而异):s3 用 access_key_id/secret_access_key/...;
    # gdrive 用 client_id/client_secret/token。server 不在此校验具体字段,直接
    # 交给 rclone — 写完后立刻调 `rclone lsd <name>:` 测连通性,失败回写
    # last_test_error。
    config: dict[str, str] = Field(default_factory=dict)
    # 是否对 backup tarball 做 age passphrase 加密。开启时 age_passphrase 必填
    # (一旦丢失,该 remote 上的所有备份永久不可恢复)。
    encrypted: bool = False
    age_passphrase: str | None = None


class BackupRemoteUpdateRequest(BaseModel):
    config: dict[str, str] | None = None
    age_passphrase: str | None = None
    # 用户在编辑时切换 encrypted 状态(开/关) — 必须能持久化。
    encrypted: bool | None = None


class BackupRemoteOut(BaseModel):
    id: int
    name: str
    backend_type: str
    encrypted: bool
    config_summary: dict | None = None
    last_test_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_error: str | None = None
    created_at: datetime


class BackupRemoteTestResponse(BaseModel):
    ok: bool
    error: str | None = None
    listing: list[str] | None = None


class BackupScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    cron_expr: str = Field(min_length=1, max_length=64)
    retention_days: int = Field(ge=1, le=3650, default=30)
    include_attachments: bool = True
    enabled: bool = True
    remote_ids: list[int] = Field(min_length=1)


class BackupScheduleUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    cron_expr: str | None = Field(default=None, min_length=1, max_length=64)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    include_attachments: bool | None = None
    enabled: bool | None = None
    remote_ids: list[int] | None = None


class BackupScheduleOut(BaseModel):
    id: int
    name: str
    cron_expr: str
    retention_days: int
    include_attachments: bool
    enabled: bool
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    remote_ids: list[int] = Field(default_factory=list)
    created_at: datetime


class BackupRunTargetOut(BaseModel):
    id: int
    remote_id: int
    remote_name: str | None = None
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    bytes_transferred: int | None = None
    error_message: str | None = None


class BackupRunOut(BaseModel):
    id: int
    schedule_id: int | None = None
    schedule_name: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    backup_filename: str | None = None
    bytes_total: int | None = None
    error_message: str | None = None
    log_text: str | None = None
    targets: list[BackupRunTargetOut] = Field(default_factory=list)


class BackupRunListOut(BaseModel):
    items: list[BackupRunOut]
    total: int


# ============================================================================
# Restore schemas (PR3 用)
# ============================================================================


class BackupRestoreOut(BaseModel):
    """`<DATA_DIR>/restore/<run_id>/status.json` 的读视图。"""

    run_id: int
    phase: str  # 'downloading' / 'extracting' / 'done' / 'failed'
    started_at: datetime
    finished_at: datetime | None = None
    bytes_total: int | None = None
    bytes_downloaded: int | None = None
    error_message: str | None = None
    extracted_path: str | None = None
    source_remote_id: int | None = None
    source_remote_name: str | None = None
    backup_filename: str | None = None


class BackupRestoreListOut(BaseModel):
    items: list[BackupRestoreOut]
