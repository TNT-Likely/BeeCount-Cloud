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


class UserProfilePatchRequest(BaseModel):
    # 所有字段都可选：mobile 改配色时只送 `income_is_red`，web 改昵称时只送
    # `display_name`。handler 只更新非 None 字段。
    display_name: str | None = None
    income_is_red: bool | None = None
    theme_primary_color: str | None = None
    appearance: dict | None = None
    ai_config: dict | None = None

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
    ledger_id: str
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime


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
    ledger_id: str
    entity_type: str
    entity_sync_id: str
    action: SyncAction
    payload: dict[str, Any]
    updated_at: datetime
    updated_by_device_id: str | None


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


class AdminIntegrityIssueSample(BaseModel):
    """整体性扫描:单条 issue 样本(完整 list 可能很长,只回 5 条 spot check)"""

    sync_id: str
    label: str
    extra: dict[str, Any] | None = None


class AdminIntegrityIssue(BaseModel):
    """单类问题在某 ledger 下的统计"""

    issue_type: str  # "orphan_tx_category" | "orphan_tx_account" | ...
    ledger_id: str
    ledger_name: str | None
    owner_email: str | None
    count: int
    samples: list[AdminIntegrityIssueSample]


class AdminIntegrityScanOut(BaseModel):
    scanned_at: datetime
    ledgers_total: int
    issues_total: int
    issues: list[AdminIntegrityIssue]


class AdminLogListOut(BaseModel):
    items: list[AdminLogEntryOut]
    capacity: int
    latest_seq: int


class ReadLedgerOut(BaseModel):
    ledger_id: str
    ledger_name: str
    currency: str
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
    last_change_id: int
    ledger_id: str | None = None
    ledger_name: str | None = None
    created_by_user_id: str | None = None
    created_by_email: str | None = None
    created_by_display_name: str | None = None
    created_by_avatar_url: str | None = None
    created_by_avatar_version: int | None = None


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


class WriteLedgerMetaUpdateRequest(WriteBaseRequest):
    ledger_name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = Field(default=None, min_length=1, max_length=16)


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


class WriteBudgetCreateRequest(WriteBaseRequest):
    type: Literal["total", "category"]
    category_id: str | None = None
    amount: float = Field(gt=0)
    period: Literal["monthly", "weekly", "yearly"] = "monthly"
    start_day: int = Field(default=1, ge=1, le=28)
    enabled: bool = True


class WriteBudgetUpdateRequest(WriteBaseRequest):
    amount: float | None = Field(default=None, gt=0)
    period: Literal["monthly", "weekly", "yearly"] | None = None
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
