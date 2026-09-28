from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # 2FA(TOTP)。详见 .docs/2fa-design.md。
    # null = 未启用 / 未 setup。totp_enabled=False 但 secret 不为空 = setup 流程
    # 中途用户没 confirm,可以重新走 /setup 覆盖。
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # SSO(OIDC)登入時的身分識別(IdP 回傳的 `sub` claim)。null = 尚未透過
    # SSO 登入過(純密碼帳號,或尚未 link)。既有密碼帳號第一次用 SSO 登入、
    # email 對得上時會自動 link 這個欄位到現有 row,不會建出重複帳號。
    sso_subject: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True
    )


class RecoveryCode(Base):
    """2FA 一次性恢复码。启用 2FA 时一次生成 10 个,sha256 hash 存库。"""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    avatar_version: Mapped[int] = mapped_column(Integer, default=0)
    # 收支颜色方案：对齐 mobile `incomeExpenseColorSchemeProvider`
    # - True  = 红色收入 / 绿色支出（mobile app 旧默认）
    # - False = 红色支出 / 绿色收入（传统中式会计习惯）
    # Nullable 兜底老用户 / 老数据，None 视为 True。
    income_is_red: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    # 主题色：mobile 推给 server，web 当作"初始偏好"。Web 用户本地改过主题色
    # 后会写 localStorage，本地值永远优先；没改过的 web 客户端跟 mobile 同步。
    # 格式：hex `#RRGGBB`。长度给 7 预留 # + 6 位。
    theme_primary_color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # 外观类设置的 JSON blob（跟 theme_primary_color / income_is_red 性质相同
    # 但字段碎片化，打包到一起）。当前 mobile 推送的 key 包括：
    #   - header_decoration_style: 月显示头部装饰 "none"/"minimal"/…
    #   - compact_amount: 紧凑金额显示 true/false
    #   - show_transaction_time: 交易是否显示时间 true/false
    #   - category_icon_style: 类别图示画风 "material"（默认）/"cute"（手绘可
    #     爱线稿，跟 mobile CategoryIconStyle / web CategoryIconStyleContext 对齐）
    # 字体缩放 font_scale 故意不进来（跨设备屏幕尺寸不同，不该强行拉齐）。
    # 用 Text 存 JSON string；/profile/me 接口 GET/PATCH 时序列化为 dict。
    appearance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 配置 JSON blob:providers(服务商数组)、binding(能力 ↔ 服务商绑定)、
    # custom_prompt(自定义提示词)、strategy(cloud_first/local_first…)、
    # bill_extraction_enabled、use_vision。
    # API key 敏感,只在登录用户自己的 profile 上传下行,不对外暴露。
    ai_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 主币种(本位币):资产折算的目标币种,user-global 偏好。mobile prefs key
    # `baseCurrency`,PATCH /profile/me key `primary_currency`。大写 ISO 代码,
    # 预留 16 位对齐既有币种列宽。null = 客户端按自己的规则初始化,server 不猜。
    primary_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # SwipeSmart2 個人 API Key(Phase 14,docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md
    # §3.3.1(a)):使用者自行貼上的 SwipeSmart Personal API Key,用
    # `services.secret_crypto` 加密儲存,**不透過 sync 機制**同步到其他裝置。
    # 只在 `routers/swipesmart.py` 內短暫解密用於呼叫 SwipeSmart,絕不明文
    # 回傳給前端。
    swipesmart_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # expires_at/revoked_at 都有 index —— `refresh_token_retention` 排程 job
    # (services/scheduled_jobs.py)每天用這兩個欄位掃「失效超過 2 天」的舊列
    # 來刪,沒有 index 在百萬行規模下會全表掃描。
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PersonalAccessToken(Base):
    """长期 token,专供外部 LLM 客户端(Claude Desktop / Cursor / Cline)通过
    MCP 协议访问账本数据用。跟 access token / refresh token 完全独立:

    - access token:60 分钟过期,refresh 流复杂,LLM 客户端做不到
    - refresh token:绑 device,跨 LLM 客户端不通用
    - **PAT**:用户主动创建 → 自定义过期(默认 90 天 / 永久)→ 可独立撤销
      → 单独 scope(`mcp:read` / `mcp:write`),不污染 web/app 路径

    Token 明文格式 `bcmcp_<32 字节 base64url>`,只在创建时返回一次,之后表
    里只存 sha256。`prefix` 前 16 字符明文供列表展示用。详见
    .docs/mcp-server-design.md。
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    # sha256 hex = 64 字符,加 hash 算法标识可扩展到 128
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # 前 16 字符明文(如 `bcmcp_a1b2c3d4`)给列表展示用,识别哪个是哪个
    prefix: Mapped[str] = mapped_column(String(32), index=True)
    # JSON 数组:["mcp:read"] / ["mcp:write"] / 两者
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index(
    "ix_pat_user_active",
    PersonalAccessToken.user_id,
    PersonalAccessToken.revoked_at,
)


class MCPCallLog(Base):
    """每一次 MCP tool 调用的审计记录。给 Web 设置页"调用历史"用,也帮助用户
    debug 自己写的 LLM agent。

    **不**记录 args / result 的完整内容(交易备注可能含隐私) — 只存元数据:
      - tool_name + status + duration_ms → "Claude 今天调了 list_transactions 12 次,
        都成功"
      - args_summary 是结构化字段的脱敏摘要,例如 `tx_type=expense, amount=38, ...`,
        最多 200 字 — 帮回忆"我让它做了啥",不留 note 之类的自由文本
      - error 出错时存 truncated message

    保留期 30 天,过期由 APScheduler 定时清(同 backup 用同一套调度器)。
    """

    __tablename__ = "mcp_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # PAT 可能事后被删,pat_id 用 SET NULL 保住历史(知道是 LLM 调的,只是不
    # 知道哪个 token —— 删 token 也是用户主动行为,失去关联本就预期)
    pat_id: Mapped[str | None] = mapped_column(
        ForeignKey("personal_access_tokens.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # token 删了但 prefix 还在,UI 列表能显示"来自 bcmcp_xxx 的调用"
    pat_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 缓存当时 PAT 的用户起名(如 "Claude Desktop"),比 prefix 友好;
    # 即便日后 PAT 改名 / 删除,历史里仍能看到调用方身份
    pat_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64), index=True)
    # 'ok' | 'error'
    status: Mapped[str] = mapped_column(String(16), index=True)
    # 出错时存 error.__class__.__name__ + truncated str(error),最多 500 字
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    args_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


Index(
    "ix_mcp_call_user_time",
    MCPCallLog.user_id,
    MCPCallLog.called_at.desc(),
)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="Unknown Device")
    platform: Mapped[str] = mapped_column(String(32), default="unknown")
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Ledger(Base):
    __tablename__ = "ledgers"
    __table_args__ = (UniqueConstraint("user_id", "external_id", name="uq_ledgers_user_external"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 原先走 snapshot.currency,现在单独存列 —— read 路径不用再 parse snapshot。
    # 默认 CNY 对齐 mobile/web 默认币种。
    currency: Mapped[str] = mapped_column(String(16), default="CNY", server_default="CNY")
    # 自定义每月起始日(1-28):统计/预算按 [当月N日, 次月N日) 聚合,1=自然月。
    # mobile Drift 列 ledgers.month_start_day,sync payload key `monthStartDay`。
    # 口径与决策见 BeeCount 仓 .docs/period-start-date/design.md。
    month_start_day: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    changes: Mapped[list["SyncChange"]] = relationship(back_populates="ledger")
    members: Mapped[list["LedgerMember"]] = relationship(
        back_populates="ledger", cascade="all, delete-orphan"
    )


class LedgerMember(Base):
    __tablename__ = "ledger_members"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Phase 1: 'owner' / 'editor'。'viewer' 远期保留。
    role: Mapped[str] = mapped_column(String(16))
    invited_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    ledger: Mapped[Ledger] = relationship(back_populates="members")


Index("ix_ledger_members_user_id", LedgerMember.user_id)
Index("ix_ledger_members_ledger_id", LedgerMember.ledger_id)


class LedgerInvite(Base):
    __tablename__ = "ledger_invites"

    # 6 位邀请码,字符集排除 O/0/I/1,熵 ≈ 32^6 ≈ 10 亿
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True
    )
    invited_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    target_role: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncChange(Base):
    __tablename__ = "sync_changes"

    change_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # scope='user' 时 ledger_id 为 NULL(user-global change 不依附任何账本);
    # scope='ledger' 时必填,指向具体账本。alembic 0010 把这列从 NOT NULL 改 nullable。
    ledger_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # 'user' = category/account/tag 等 user-global 资源;
    # 'ledger' = budget/transaction/ledger/ledger_snapshot 等 ledger-scoped。
    # mobile 老协议不发 scope → server 按 entity_type 兜底改写。
    scope: Mapped[str] = mapped_column(
        String(8), default="ledger", server_default="ledger", index=True
    )
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_sync_id: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(16), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_by_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    ledger: Mapped[Ledger | None] = relationship(back_populates="changes")


Index("idx_sync_changes_user_cursor", SyncChange.user_id, SyncChange.change_id)
Index("idx_sync_changes_ledger_cursor", SyncChange.ledger_id, SyncChange.change_id)
Index(
    "idx_sync_changes_entity_latest",
    SyncChange.ledger_id,
    SyncChange.entity_type,
    SyncChange.entity_sync_id,
    SyncChange.change_id,
)
# user-scope pull cursor:`GET /sync/pull?ledger_external_id=__user_global__` 用
Index(
    "idx_sync_changes_user_scope_cursor",
    SyncChange.user_id,
    SyncChange.scope,
    SyncChange.change_id,
)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", "ledger_external_id", name="uq_sync_cursor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    ledger_external_id: Mapped[str] = mapped_column(String(128), index=True)
    last_cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncPushIdempotency(Base):
    __tablename__ = "sync_push_idempotency"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", "idempotency_key", name="uq_sync_push_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), index=True)
    request_hash: Mapped[str] = mapped_column(String(128))
    response_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BackupSnapshot(Base):
    __tablename__ = "backup_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AttachmentFile(Base):
    __tablename__ = "attachment_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # ledger_id 对 'transaction' kind 必填,对 'category_icon' kind 为 NULL
    # (分类自定义图标是 user-global,不绑账本)。
    ledger_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True, nullable=True,
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1024))
    # 区分附件类型:
    #   'transaction' (默认) - 交易附件,挂在某个 ledger 下,storage path
    #       含 ledger 维度
    #   'category_icon' - 分类自定义图标,user-global,storage path 不含 ledger
    attachment_kind: Mapped[str] = mapped_column(
        String(32), default="transaction", nullable=False, server_default="transaction",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


Index("idx_attachment_files_sha256", AttachmentFile.sha256)
Index("idx_attachment_files_ledger_created", AttachmentFile.ledger_id, AttachmentFile.created_at)


class BackupArtifact(Base):
    __tablename__ = "backup_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


Index("idx_backup_artifacts_ledger_created", BackupArtifact.ledger_id, BackupArtifact.created_at)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ledger_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledgers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Notification(Base):
    """用户通知中心(§2.1 MOZE_FEATURE_GAP_SD.md)。user-global,不进
    `sync_changes`/projection —— 走普通 REST,跨端各自 poll 或收 WS 推播。

    产生通知的来源分散在各功能里(budget 超支判断、recurring 到期、信用卡
    繳款日提醒等),故意不集中成一个 job,避免跨模块耦合。各功能调用
    `services.notifications.create_notification()` 落地一条记录即可。
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # 'reminder' | 'budget_alert' | 'card_due' | 'system'
    category: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 结构化附加数据(如关联的 ledger_id / tx_sync_id),前端跳转用
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # 使用者反饋:通知排序要先分優先度、同優先度再照時間——數字越大越優先,
    # 排在最上面,不隨新通知被擠走。目前 2=欠款(debt_reminders/
    # debt_unsettled_notifications)、1=信用卡帳單(credit_card_reminders)、
    # 0=其餘(預設值,installment_plans/recurring_materializer/card_reward)。
    # 3=管理者系統公告(admin_broadcasts)——排最前面,避免使用者累積一堆
    # 未讀提醒時公告被擠出 App 輪詢的前 50 筆而漏跳系統通知。
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    # 由管理者系統公告產生的通知才有值;撤回公告時靠這個欄位一次刪掉所有
    # 使用者名下的那一筆。其它功能產生的通知一律是 NULL。
    broadcast_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("system_broadcasts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )


Index(
    "ix_notifications_user_time",
    Notification.user_id,
    Notification.created_at.desc(),
)


class SystemBroadcast(Base):
    """管理者系統公告(後台 `/app/admin/broadcasts`)。

    發送時對當下所有 `is_enabled` 的使用者各寫一筆 `category='system'`
    的 `Notification`(帶 `broadcast_id` 指回這裡),App 與 web 都靠既有的
    通知輪詢拿到——App 輪詢到新未讀會順便跳本機系統通知,所以 App 在前景
    時最多延遲一個輪詢週期(60 秒)就會跳出來;背景/被殺掉時要等下次開 App
    才補跳(沒有接 FCM/APNs)。發送後才註冊的新帳號不會收到。

    撤回(`retracted_at`)= 刪掉所有使用者名下對應的那一筆通知,這張表的
    紀錄保留作為發送歷史。已經跳出去的系統通知收不回來。
    """

    __tablename__ = "system_broadcasts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================================
# Read projection tables (CQRS Q-side)
# ============================================================================
#
# snapshot 是权威源(mobile sync 继续吃它)。这几张投影表是 web `/read/*` 路径
# 专用的索引化视图,每次 materialize / diff emit 时**同事务**写入。web 读永远
# 走 SELECT + index,不再 parse 3MB JSON。
#
# 复合 PK `(ledger_id, sync_id)`:mobile 理论上不会跨账本复用 syncId,但 schema
# 层防御;单 ledger_id 就是 ON DELETE CASCADE 的自然作用域。
#
# `source_change_id` 记录"这行是哪次 materialize 写的",纯诊断用 —— projection
# 跟 snapshot 不一致时,对这列能反查到哪次 push 出问题。


class ReadTxProjection(Base):
    __tablename__ = "read_tx_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    tx_type: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 商店(需求 #11,Phase 11):選填,純展示用途,不參與任何統計/校驗。
    merchant: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 外键引用都存 sync_id,rename 时只改 *_name 列,id 不动。
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # tags_csv:逗号分隔的 name 串,ILIKE 搜索用;tag_sync_ids_json:sync_id 列表。
    tags_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    tag_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tx_index: Mapped[int] = mapped_column(Integer, default=0)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_edited_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # 账单标记(.docs/transaction-flags)。default false:既有行升级后不过滤,
    # 旧 App 不发该字段时保持 false。exclude_from_stats=不计入收支统计;
    # exclude_from_budget=不计入预算用量。两者独立。
    exclude_from_stats: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    exclude_from_budget: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # 交易级多币种(0018,.docs/multi-currency-ledger):currency_code=原币种
    # (NULL 视作账本本位币);native_amount=折账本本位币的金额快照(NULL 时
    # 统计端 COALESCE 回退 amount)。账本维度统计读 native_amount,账户维度仍 amount。
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    native_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction):
    # base_amount=使用者輸入的原始金額(信用卡回饋計算的權威基準,見
    # services/card_rewards.py::_reward_base_amount);fee_amount/discount_amount
    # =額外調整金額(可為 0),fee_label/discount_label=自訂名稱(NULL=用預設
    # 「手續費」「折扣」顯示)。既有 amount 欄位語意不變,仍是換算後、實際
    # 影響帳戶餘額的總額。base_amount 為 NULL = 從未使用過這個功能,不影響
    # 既有交易任何行為。
    base_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 跨幣別轉帳(2026-08):僅 tx_type=transfer 且轉出/轉入帳戶幣別不同時有值
    # ——轉入帳戶自身幣別的金額(既有 amount 欄位語意不變,仍是轉出帳戶自身
    # 幣別、驅動轉出帳戶餘額增減的那個數)。NULL = 同幣別轉帳(舊資料/舊版
    # App 皆是如此),讀取一律 COALESCE(to_amount, amount) 回退,不需要回填。
    to_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 退款(§2.6 MOZE_FEATURE_GAP_SD.md):指向被退款的那笔支出 tx 的 sync_id。
    # 有值 = 这笔(通常是 income)交易是对某笔支出的退款,统计口径从"当期收入"
    # 挪走,改冲抵该笔支出净额(见 read/_shared._projection_totals、
    # read/workspace.workspace_analytics 的 netting 逻辑)。None = 普通交易。
    refund_of_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 分期付款(§2.3):有值 = 这笔交易是某个分期计划自动生成的一期,反查
    # read_installment_plan_projection.sync_id。None = 普通交易(含分期计划
    # 本身可能已建了第一期时也带这个值)。
    installment_plan_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 週期性收支(§2.12.2 Phase 1.5):有值 = 这笔交易是某个 recurring rule
    # 生成的一次 occurrence,反查 read_recurring_rule_projection.sync_id。
    recurring_rule_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # True = 这笔 occurrence 被 PATCH .../occurrences/{tx_sync_id} 单独编辑过,
    # 之后 update-from / 视窗续产生都要跳过它,不能被批次覆盖。
    recurring_occurrence_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # 拆帳(§2.4 MOZE_FEATURE_GAP_SD.md):has_splits=True 时这笔交易的
    # category_sync_id/category_name 应为 NULL(前端显示"多分类"),明细行在
    # read_tx_split_projection。splits_json 是 LWW merge 的 fallback 值(跟
    # attachments_json 同款模式,权威可查询结构仍是下面那张子表,upsert_tx
    # 每次都从这个 JSON 整批 delete-then-insert 重建子表行)。
    has_splits: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    splits_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 借還款追蹤(§2.5 MOZE_FEATURE_GAP_SD.md Phase 3):有值 = 这笔交易是对
    # 某笔欠款的一次还款/收款,反查 read_debt_projection.sync_id。None = 普通
    # 交易。欠款的 remaining_amount/status 不落库,读路径实时从这个反查字段
    # 汇总算出(跟 installment_plan 的 paid_periods 同一惯例,见该 projection
    # 类的 docstring),所以这里**不**需要任何 upsert/delete 时的联动重算 ——
    # 纯粹是个跟 installment_plan_sync_id/recurring_rule_sync_id 同款的
    # denormalized 反查列。
    debt_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 專案(Phase 13,docs/PH13_PROJECT_SD.md §2.2):有值 = 使用者手動指定
    # 這筆交易屬於哪個 read_project_projection.sync_id。None = 沒掛專案。
    # 只支援 expense/income(跟 debt_sync_id 同款單值反查,寫入路徑
    # `_assert_project_exists` 拒絕 transfer/adjustment 帶這個欄位),不需要
    # 任何 upsert/delete 時的聯動重算——花費彙總(list_projects)從這個反查
    # 欄位即時 SUM 算出。
    project_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 信用卡紅利回饋(§2.9.5,2026-08-06 改版):使用者記這筆交易時手動勾選
    # 的 read_card_reward_rule_projection.sync_id 列表(nullable JSON array,
    # 跟 tag_sync_ids_json 同一模式)。系統不再依 category/金額自動比對「這筆
    # 該算哪條規則」——services.card_rewards 的當期回饋計算只加總這裡有勾選
    # 到該規則的交易,min_tx_amount/min_spend_threshold 這兩個金額門檻仍由
    # 系統在計算時判斷,不受使用者勾選影響。
    reward_rule_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 信用卡紅利回饋自動入帳(§2.9.5.4 補強,2026-08-04 使用者反饋):逐筆結算
    # (immediate_after_tx/after_posting_date)產生的回饋 income 交易,反查
    # 它是為了哪一筆原始消費入帳的 —— 跟 refund_of_sync_id/installment_plan_
    # sync_id 同款「denormalized 反查列」模式,只是方向相反(這裡是「回饋
    # 交易指向消費交易」,不需要像 refund 那样反向聚合查「誰退了我」,單向
    # 存就够)。None = 普通交易,或 period_end/manual 這種不對應單一原始
    # 交易的回饋入帳。
    reward_source_tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 延後入帳(§2.10 MOZE_FEATURE_GAP_SD.md Phase 5,對帳模式的必要前置):
    # 有值 = 這筆交易處於「延後入帳」狀態,值是使用者填的實際入帳日;None =
    # 正常,沿用 happened_at。對帳/信用卡帳單彙總等需要「入帳日口徑」的地方
    # 一律用 `services.deferred_posting.attribution_date_expr()` 產生的
    # `COALESCE(deferred_posting_at, happened_at)` 表達式,不要各自重寫。
    deferred_posting_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 對帳模式(§2.10,2026-08-09 改版為 Moze 式逐筆核對清單,取代舊版「單筆
    # 餘額比對記錄」):非空 = 使用者在對帳模式裡勾選確認過「這筆交易確實在
    # 這期信用卡帳單上」。跟 deferred_posting_at 一样是 tx 自身的字段,不再
    # 需要独立的 reconciliation entity/表——一笔交易只需要记「有没有被对过
    # 帐」这一个布尔状态(用哪一期帐单核对过它,由它自己的 attribution_date
    # 落在哪个週期窗口決定,不需要额外记"是哪一期确认的")。
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


Index(
    "ix_read_tx_ledger_time",
    ReadTxProjection.ledger_id,
    ReadTxProjection.happened_at.desc(),
    ReadTxProjection.tx_index.desc(),
)
Index(
    "ix_read_tx_ledger_category",
    ReadTxProjection.ledger_id,
    ReadTxProjection.category_sync_id,
)
Index(
    "ix_read_tx_ledger_account",
    ReadTxProjection.ledger_id,
    ReadTxProjection.account_sync_id,
)
# workspace/transactions 跨账本查询 —— 只按 user_id 过滤
Index(
    "ix_read_tx_user_time",
    ReadTxProjection.user_id,
    ReadTxProjection.happened_at.desc(),
)


# ============================================================================
# User-scope projection tables —— user-global 资源(category/account/tag)的
# 真·per-user 物化视图。PK=(user_id, sync_id),跟账本完全无关。详见
# .docs/user-global-refactor/plan.md。alembic 0010 同时 drop 老 read_*_projection
# 三张表。
# ============================================================================


class UserCategoryProjection(Base):
    __tablename__ = "user_category_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    icon_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    custom_icon_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    icon_cloud_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    icon_cloud_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 共享账本二级分类:存 parent 的 sync_id,跟 parent_name 同步维护。
    # parent_name 字段保留(老调用 / fallback / 显示用),parent_sync_id 才是
    # 稳定 FK,父分类重命名时不需要级联改子分类。
    parent_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_user_cat_kind",
    UserCategoryProjection.user_id,
    UserCategoryProjection.kind,
)


class UserAccountProjection(Base):
    __tablename__ = "user_account_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    initial_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    credit_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    billing_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_last_four: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 主帳戶(合併帳單,§2.9 Phase 4 MOZE_FEATURE_GAP_SD.md):自我參照,附卡/
    # 子卡的 sync_id 指向主卡的 sync_id;None = 沒有掛在任何主卡下。
    parent_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶,不再是一條完整的
    # 週期性收支規則。掛在信用卡群組(或沒有掛靠任何群組的獨立信用卡)自己
    # 身上,`auto_pay_from_account_id` 是同一個 user 底下另一個帳戶的
    # sync_id 自我參照,見 `services.credit_card_autopay`。
    auto_pay_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    auto_pay_from_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 帳戶頭像(2026-08-02 補強):使用者反饋光靠 bank_name 文字看不出是哪張
    # 卡,加一張自訂圖片。走跟 category icon 一樣的共用 attachment 池(見
    # `AttachmentFile`/`attachment_kind="account_avatar"`),`avatar_cloud_
    # file_id` 是唯一權威值,沒有 mobile 端"本地路徑"這種舊制概念要相容,
    # 所以只有這一個欄位 + sha256(dedup 用),不像 category 有 icon_type/
    # custom_icon_path 那麼多歷史包袱。
    avatar_cloud_file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_cloud_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # 账户隐藏(issue #240)。default false:既有行升级后不隐藏,旧 App 不发该
    # 字段时保持 false。只影响前端选择器/列表呈现,服务端不做任何统计过滤(D1)。
    hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # SwipeSmart2 卡片對照(Phase 14,docs/PH14_SWIPESMART_CARD_RECOMMEND_SD.md
    # §3.3.1(b)):對應 SwipeSmart 的 `CardId`,只有 credit_card 類型的帳戶有
    # 意義。None = 使用者尚未在卡片對照設定視窗裡勾選對應,不參與「反查帳戶」
    # 但推薦建議仍會顯示(降級為純文字)。
    swipesmart_card_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 納入總餘額(Phase 18,docs/PH17_USER_FEEDBACK_2026-08_SD.md):對齊 Moze
    # 「納入總餘額」開關,關閉後這個帳戶的餘額不列入淨資產/資產構成總額,但
    # 帳戶本身、個別餘額顯示、底部分組列表都不受影響 —— 跟 `hidden` 是兩個
    # 獨立維度(封存/隱藏管「要不要出現在列表」,這個欄位管「要不要計入總
    # 數」)。default true:既有帳戶升級後預設維持現況「全部計入」不變。
    include_in_total: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    # 帳戶清單拖曳排序(2026-09-05):跟 UserCategoryProjection.sort_order 同款,
    # nullable——舊資料/舊版 App 沒有這個值時留 None,read 端排序時 fallback
    # 到名稱(見 routers/read/ledgers.py)。
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 股票持股(2026-09-28,docs/STOCK_HOLDINGS_SD.md):投資理財帳戶的使用者
    # 自訂費用設定(手續費率/折扣/最低手續費/證交稅/股利手續費/預扣稅/二代
    # 健保/預設再投入/交割帳戶),wire 上是 `investmentSettings` 物件,這裡
    # json.dumps 成 TEXT 存(跟 recurring_rule.advanced_rule_json 同款)。
    # None = 沒設定過,讀端套各市場預設值。
    investment_settings_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserExchangeRateProjection(Base):
    """手动汇率 override 的 user-scope projection(Q-side)。

    方向约定:1 quote = rate base(与 mobile exchange_rate_overrides 表一致)。
    rate 存 decimal 字符串,不用 Float —— 金额语义数据不走浮点。
    业务键 (user_id, base_currency, quote_currency);主键沿用 (user_id, sync_id)
    对齐其它 user projection。双端离线各建同币对会出现两个 sync_id 行,server
    原样保留,App apply 端按币对收敛(BeeCount 仓 02-tech-design-app §七)。
    """

    __tablename__ = "user_exchange_rate_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    rate: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_user_rate_pair",
    UserExchangeRateProjection.user_id,
    UserExchangeRateProjection.base_currency,
    UserExchangeRateProjection.quote_currency,
)


class ExchangeRateCache(Base):
    """汇率代理的服务端缓存:每个 base 一行,payload 整存。

    方向约定:payload_json = {"USD": "0.1477", ...} 即 1 base = x quote
    (与上游一致,**不取倒数** —— 倒数是 App 落库时统一做的)。
    """

    __tablename__ = "exchange_rate_cache"

    base_currency: Mapped[str] = mapped_column(String(16), primary_key=True)
    rate_date: Mapped[str] = mapped_column(String(10), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Security(Base):
    """證券清單(股票持股 2026-09-28,docs/STOCK_HOLDINGS_SD.md)。全域、不分
    user、不進 sync —— 跟 `ExchangeRateCache` 同款「server 自己抓來的市場
    資料」。業務鍵 (market, symbol);`market` 代碼見
    `services/securities/markets.py`(TW=上市、TWO=上櫃、US、HK…)。

    台股清單由 `security_master_refresh` 排程從證交所/櫃買 OpenAPI 整批
    同步;其它市場在使用者搜尋(Yahoo)時才 upsert 進來。"""

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    # 'stock' | 'etf' | 'bond_etf' | 'other',只給搜尋結果顯示用。
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="stock")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


Index("ux_securities_market_symbol", Security.market, Security.symbol, unique=True)


class SecurityQuote(Base):
    """最新報價快取,每檔證券一行。`session`:'close' = 收盤價(收盤排程
    寫入),'intraday' = 盤中延遲報價(使用者打開 App/Web 時補抓)。"""

    __tablename__ = "security_quotes"

    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True
    )
    price: Mapped[float] = mapped_column(Float, nullable=False)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 報價本身的時間(交易所時間轉 UTC),不是抓取時間。
    quote_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session: Mapped[str] = mapped_column(String(16), nullable=False, default="close")
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecurityDividendEvent(Base):
    """除權息事件(股票持股 Phase 2,docs/STOCK_HOLDINGS_SD.md §7)。全域市場
    資料,同 `securities`。業務鍵 (security_id, ex_date)。

    - `cash_per_share`:每股現金股利(證券幣別)。
    - `stock_per_share`:每股配股數(台股「無償配股率」,例 0.05 = 每 1000 股
      配 50 股)。
    - `source`:'twse' / 'tpex'(官方預告表)優先於 'yahoo'——官方來源寫過的
      事件不會被 Yahoo 蓋掉(見 `services/securities/dividends.upsert_events`)。
    官方預告表常常先公告除息日、金額之後才補上,所以金額欄位會被更新。"""

    __tablename__ = "security_dividend_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), nullable=False
    )
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cash_per_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stock_per_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


Index(
    "ux_security_dividend_events_sec_date",
    SecurityDividendEvent.security_id,
    SecurityDividendEvent.ex_date,
    unique=True,
)


class PendingDividend(Base):
    """待確認股利(股票持股 Phase 2)。`security_dividend_detector` 排程在除息
    日當天(含)之後,依「除息日前一天收盤時」的持股股數替每個持有該標的的
    投資理財帳戶建一筆,並發通知;使用者在 App/Web 確認實收金額(或選擇
    再投入)後,由 server 建立 income 交易 + stock_trade(見
    `routers/write/stock_trades.py::confirm_pending_dividend_api`)。

    不是 sync entity:狀態只存在 server,App/Web 都透過 API 讀寫。估算欄位
    (`est_*`)在狀態還是 pending 時每次排程都會依最新持股重算(使用者補記
    除息日前的買進時會跟著變)。

    `status`:'pending' / 'confirmed' / 'dismissed'。已確認的股利如果使用者
    之後把建出來的明細刪掉,排程會把它改回 pending(不重發通知)。
    `ledger_id`:入帳要落在哪本帳(內部 PK)——取該帳戶這檔標的最近一筆
    明細所在的帳本。"""

    __tablename__ = "pending_dividends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False)
    account_sync_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("security_dividend_events.id", ondelete="CASCADE"), nullable=False
    )
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    security_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    shares: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    est_gross: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    est_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    est_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    est_net: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    est_stock_shares: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 確認時建立的 stock_trade syncId(JSON 陣列字串),用來判斷是否被刪掉。
    created_trade_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "ux_pending_dividends_user_account_event",
    PendingDividend.user_id,
    PendingDividend.account_sync_id,
    PendingDividend.event_id,
    unique=True,
)
Index("ix_pending_dividends_user_status", PendingDividend.user_id, PendingDividend.status)


class UserTagProjection(Base):
    __tablename__ = "user_tag_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


class ReadBudgetProjection(Base):
    __tablename__ = "read_budget_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    budget_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    start_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_budget_ledger_cat",
    ReadBudgetProjection.ledger_id,
    ReadBudgetProjection.category_sync_id,
)


class ReadRecurringRuleProjection(Base):
    """週期性收支规则(§2.2 / Phase 1.5 修正版 §2.12.2 MOZE_FEATURE_GAP_SD.md)。
    ledger-scoped,跟 budget 同款 PK=(ledger_id, sync_id)。建规则(或建交易
    当下顺便设週期)时就已经依 `services.recurring_schedule` 批次生成一个
    视窗的 occurrence transaction(带 `recurring_rule_sync_id` 反查),
    `generated_until_at` 记录生成到哪个时间点;没设 `end_at` 的长期规则由
    `services.recurring_materializer.refill_recurring_windows` 低频续产生。
    `next_run_at` 建规则之后不再被排程推进,只作为"这条规则的原始锚点时间"
    历史相容字段保留。"""

    __tablename__ = "read_recurring_rule_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    tx_type: Mapped[str] = mapped_column(String(16), default="expense")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Phase 24(§問題 B 第二層):「連同未來週期」批次更新希望也能涵蓋的
    # 「重複性交易固定屬性」——跟 note/category/account 同一批轉發字段。
    merchant: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):原本認為這幾個是「每一筆
    # 交易當下的獨立決定」,故意不加在規則上——結果每期自動產生的 occurrence
    # 都沒有東西可以繼承,只有「建交易當下順便設週期」那個特例因為整包複製
    # 原始請求才剛好保留。改成跟 merchant/project/tags 同一類「規則固定
    # 屬性」,一併儲存並在每次生成 occurrence 時轉發。語意跟
    # ReadTxProjection 同名欄位一致:base_amount=使用者輸入的原始金額
    # (信用卡回饋計算的權威基準);fee_amount/discount_amount=額外調整
    # 金額;fee_label/discount_label=自訂名稱(NULL=用預設「手續費」「折扣」
    # 顯示);reward_rule_sync_ids_json=使用者勾選的信用卡回饋規則 id 列表。
    # tx_type=="transfer" 的規則不支援這幾個欄位(見
    # write/_shared.py::_normalize_recurring_rule_fee_discount)。
    base_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    discount_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    reward_rule_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'daily' / 'weekly' / 'monthly' / 'yearly'
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")
    # 每隔几个 frequency 单位触发一次(1 = 每次都触发)
    interval: Mapped[int] = mapped_column(Integer, default=1)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Phase 1.5(§2.12.2):已经批次生成 occurrence 交易到哪个时间点了。
    # None = 尚未生成过(理论上不会出现,POST 建规则时就会立刻生成一个窗口)。
    generated_until_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 简单 frequency+interval 无法表达的规则(例如"每週六日"/"每月10号"),
    # 存 JSON 字串,None = 用 frequency+interval。详见 services.recurring_schedule。
    advanced_rule_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_recurring_rule_due",
    ReadRecurringRuleProjection.enabled,
    ReadRecurringRuleProjection.next_run_at,
)


class ReadInstallmentPlanProjection(Base):
    """分期付款计划(§2.3 / Phase 1.5 修正版见 §2.12.1 MOZE_FEATURE_GAP_SD.md)。
    ledger-scoped。建计画当下依 `repayment_method`/`interest_period`/
    `interest_rate`/`grace_period_months` 用 services.installment_amortization
    一次算出全部期数,同一个 commit 写入 N 笔 read_installment_period_projection
    + N 笔 read_tx_projection(每笔都带 installment_plan_sync_id 反查)。
    `next_period_at`/`paid_periods` 不再由排程写入,改成读路径
    (snapshot_builder.build)从 period 列即时算出的 derived 字段,只是保留
    列位置维持向下相容,不主动写入。
    `status`: 'active' / 'settled'(提前结清 payoff)/ 'terminated'(终止未来
    分期 terminate-future,没有生成结清交易)。"""

    __tablename__ = "read_installment_plan_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    periods: Mapped[int] = mapped_column(Integer, default=1)
    period_amount: Mapped[float] = mapped_column(Float, default=0.0)
    first_period_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 不再由排程推进,仅作历史相容字段,见类 docstring。
    next_period_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_periods: Mapped[int] = mapped_column(Integer, default=0)
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 'active' / 'settled' / 'terminated'
    status: Mapped[str] = mapped_column(String(16), default="active")
    # 攤還方式:'equal_installment'(等额本息)/ 'equal_principal'(等额本金,
    # 与既有 Phase 1 行为等价的默认值)/ 'fixed_interest'(固定利率算在原始
    # 本金上)。
    repayment_method: Mapped[str] = mapped_column(String(32), default="equal_principal")
    # 计息方式:'monthly'(每期固定 rate/12)/ 'daily'(按当期实际天数计息)。
    interest_period: Mapped[str] = mapped_column(String(16), default="monthly")
    # 年利率(如 0.06 = 6%/年),0 = 无息。
    interest_rate: Mapped[float] = mapped_column(Float, default=0.0)
    round_amounts: Mapped[bool] = mapped_column(Boolean, default=True)
    # 取整后的尾差塞进哪一期:'first' / 'last'。
    remainder_position: Mapped[str] = mapped_column(String(16), default="last")
    grace_period_months: Mapped[int] = mapped_column(Integer, default=0)
    # 帳單分期沖銷(§2.3,2026-08-02 第三輪):`{child_account_sync_id: amount}`
    # 的 JSON,server 端算好直接寫入,不接受 client 傳入。純虛擬記帳調整,
    # 不對應任何 read_tx_projection 交易(2026-08-02 使用者反饋:沖銷款不該
    # 出現在交易明細)——`services.credit_card_billing` 算應繳金額時直接扣掉
    # 這個值,刪除這個計畫這一行就自動失效,帳單恢復成「尚未沖銷」狀態。
    offset_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_installment_plan_due",
    ReadInstallmentPlanProjection.status,
    ReadInstallmentPlanProjection.next_period_at,
)


class ReadInstallmentPeriodProjection(Base):
    """分期付款每期明细(§2.12.1 Phase 1.5 新增)。ledger-scoped,PK 跟同组
    entity 一致 (ledger_id, sync_id)。永远只由 server 端(建计画 / rebalance /
    早偿 / 提前结清等写入口)生成,不接受 client 直接建立单笔 period,但仍走
    完整 sync entity 六步(需要跨装置可见)。`tx_sync_id` 反查该期实际生成的
    read_tx_projection 行(status='overridden' 之前若已生成过 tx,tx_sync_id
    维持指向原 tx,只是该 tx 的金额/日期被单独改过)。"""

    __tablename__ = "read_installment_period_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    plan_sync_id: Mapped[str] = mapped_column(String(255), index=True)
    period_no: Mapped[int] = mapped_column(Integer)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    principal_amount: Mapped[float] = mapped_column(Float, default=0.0)
    interest_amount: Mapped[float] = mapped_column(Float, default=0.0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # 'pending'(理论上不会出现,建立即生成)/ 'generated' / 'overridden'
    # (被 PATCH 单期改过)/ 'refunded'。
    status: Mapped[str] = mapped_column(String(16), default="generated")
    tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_installment_period_plan",
    ReadInstallmentPeriodProjection.plan_sync_id,
    ReadInstallmentPeriodProjection.period_no,
)


class ReadTxSplitProjection(Base):
    """拆帳(§2.4 MOZE_FEATURE_GAP_SD.md Phase 2):一笔交易拆成多个分类的
    明细行。不是独立 sync entity(没有自己的 client syncId,不接受单独的
    push/pull),而是挂在父交易上的只读投影 —— 权威值是父交易 SyncChange
    payload 里的 `splits` 字段(跟 attachments 一样整批带),每次
    `projection.upsert_tx` 都对这张表整批 delete-then-insert 重建
    (`ledger_id`, `tx_sync_id`) 下的所有行,不做增量 diff。"""

    __tablename__ = "read_tx_split_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    tx_sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    sort_order: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


Index(
    "ix_read_tx_split_ledger_tx",
    ReadTxSplitProjection.ledger_id,
    ReadTxSplitProjection.tx_sync_id,
)
Index(
    "ix_read_tx_split_ledger_category",
    ReadTxSplitProjection.ledger_id,
    ReadTxSplitProjection.category_sync_id,
)


class ReadDebtProjection(Base):
    """借還款追蹤(§2.5 MOZE_FEATURE_GAP_SD.md Phase 3)。ledger-scoped,跟
    budget/recurring_rule 同款 PK=(ledger_id, sync_id)。`principal_amount`
    创建后不可改(跟 installment_plan 的 total_amount 同一取舍 —— 改了语义
    等同删了重建)。**不**存 `remaining_amount`/`status`:每次还款/收款是一笔
    普通交易,带 `read_tx_projection.debt_sync_id` 反查这笔债务,读路径
    (`read/ledgers.py::list_debts`)从这些反查交易的 amount 加总即时算出
    remaining/status,不做任何跨表联动重算 —— 跟 `ReadInstallmentPlanProjection`
    的 `paid_periods` 从 period 明细 derive 是同一个理由:避免在 mobile push
    / web write 两条独立路径上都要挂一段"改交易时联动重算债务余额"的逻辑,
    省掉一整类潜在的漂移 bug。"""

    __tablename__ = "read_debt_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 'payable'(我欠别人) / 'receivable'(别人欠我)
    direction: Mapped[str] = mapped_column(String(16), default="payable")
    counterparty_name: Mapped[str] = mapped_column(Text, default="")
    principal_amount: Mapped[float] = mapped_column(Float, default=0.0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 結案(體驗補強,非 Phase 3 原始欄位):非空 = 已手動標記結束,不一定
    # 代表已還清全額(可能少還一點就結案)。读路径(list_debts)优先用它
    # 决定 status,盖过 remaining_amount 算出来的 open/partial/settled。
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 分類(App 端要求補上,原始設計刻意留空這點已不再準確):跟其他實體一樣
    # 存 sync_id,不做存在性驗證。
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 起點交易反查(建立時寫入,之後不變):App 建立欠款時會同時寫一筆帳戶
    # 餘額起點交易,但那筆交易刻意不帶 debt_sync_id(見上方 docstring,避免
    # 被還款彙總誤計入),所以需要這個欄位才能反查回那筆交易。
    origin_tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 排除計入總額(對齐 Moze「排除在帳戶總覽計算」),只影響淨資產/總額統計,
    # 不影響清單或通知——跟 UserAccountProjection.include_in_total 同款
    # 概念,但預設值相反(這裡預設 False = 預設計入)。
    excluded_from_total: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_debt_user_id",
    ReadDebtProjection.user_id,
)
Index(
    "ix_read_debt_ledger_due",
    ReadDebtProjection.ledger_id,
    ReadDebtProjection.due_at,
)


class ReadStockTradeProjection(Base):
    """股票交易明細(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。ledger-scoped,
    PK=(ledger_id, sync_id),跟 debt 同款。

    **持股不落庫**:股數/平均成本/已實現損益一律由這張表即時彙總
    (`services/securities/holdings.py`,移動平均成本法),跟 debt 的
    remaining_amount 從還款交易 derive 同一個理由 —— 不在 mobile push /
    web write 兩條路徑上各掛一段「改明細時聯動重算持股」的邏輯。

    `tx_sync_id` 指向對應的轉帳/收入交易(買進/賣出/現金股利/再投入才有;
    期初持股、配股沒有現金流動,為 None)。兩者由同一次寫入一起建立、一起
    刪除(App `LocalRepository`;web `snapshot_mutator.create_stock_trade`)。

    `amount` = 以證券幣別計的現金影響(正數):buy/opening = 股數×價格+手續費
    (成本),sell = 股數×價格−手續費−交易稅(淨收入),cash_dividend/
    reinvest = 實收金額,stock_dividend = 0。"""

    __tablename__ = "read_stock_trade_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    market: Mapped[str] = mapped_column(String(16), default="")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    security_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # buy / sell / opening / cash_dividend / stock_dividend / reinvest
    trade_type: Mapped[str] = mapped_column(String(32), default="buy")
    shares: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trade_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 股利明細對應的除權息事件(Phase 2,`security_dividend_events.id` 的字串);
    # Phase 1 一律 None。
    dividend_event_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_stock_trade_account",
    ReadStockTradeProjection.user_id,
    ReadStockTradeProjection.account_sync_id,
)
Index(
    "ix_read_stock_trade_symbol",
    ReadStockTradeProjection.market,
    ReadStockTradeProjection.symbol,
)


class ReadProjectProjection(Base):
    """專案(Phase 13,docs/PH13_PROJECT_SD.md)。ledger-scoped,PK 形狀比照
    `ReadBudgetProjection`/`ReadDebtProjection`:`(ledger_id, sync_id)`。跟
    `ReadBudgetProjection` 是兩條完全獨立的邏輯——專案有自己的
    `budget_amount` + 花費彙總(`SUM(amount) WHERE project_sync_id = X`),
    不讀也不寫 `read_budget_projection`。花費彙總不落庫,讀路徑
    (`read/ledgers.py::list_projects`)從 `read_tx_projection.project_sync_id`
    反查交易即時算出,跟 `ReadDebtProjection.remaining_amount` derive 的方式
    同一取舍——避免在 mobile push / web write 两条独立路径上都要挂一段
    「改交易时联动重算专案花費」的逻辑。"""

    __tablename__ = "read_project_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str | None] = mapped_column(String(32), nullable=True)
    budget_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 'fixed'(單次固定起訖日) / 'monthly' / 'yearly'
    period_type: Mapped[str] = mapped_column(String(16), default="monthly")
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    carryover_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    visible_on_home: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 分類子預算 + 期間切換(docs/2026-09-06-project-category-budget-period-switch-
    # design.md §2.1/§7.1)附加設定。`reminder_notified_period_key` 刻意不落在這裡
    # ——App 端 spec 明講那是本機專用欄位,不進同步 payload。
    income_included_in_budget: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_budget_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # 'fixed' / 'proportional',daily_budget_enabled=false 時忽略。
    daily_budget_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # null=不提醒;否則 1-200 的整數(可超過 100 代表「超支才提醒」)。
    reminder_threshold_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_project_user_id",
    ReadProjectProjection.user_id,
)
Index(
    "ix_read_project_ledger_sort",
    ReadProjectProjection.ledger_id,
    ReadProjectProjection.sort_order,
)


class ReadProjectCategoryBudgetProjection(Base):
    """專案分類子預算(docs/2026-09-06-project-category-budget-period-switch-
    design.md §2.2/§7.2)。ledger-scoped,PK 形狀比照 `ReadProjectProjection`:
    `(ledger_id, sync_id)`。`project_sync_id`/`category_sync_id` 跟其它同步實體
    的外鍵一樣只存 sync_id 字串,不建 SQL FK、不做存在性驗證(這個 repo 沒有
    「sync_id 轉內部 id」的機制)——存在性 + 一級分類校驗放在 write router 層
    (`routers/write/project_category_budgets.py`)。"""

    __tablename__ = "read_project_category_budget_projection"
    __table_args__ = (
        UniqueConstraint(
            "ledger_id", "project_sync_id", "category_sync_id",
            name="uq_read_pcb_project_category",
        ),
    )

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_sync_id: Mapped[str] = mapped_column(String(255))
    category_sync_id: Mapped[str] = mapped_column(String(255))
    # 'fixed'(固定金額) / 'percentage'(按專案總預算比例)
    mode: Mapped[str] = mapped_column(String(16), default="fixed")
    fixed_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    carryover_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_pcb_user_id",
    ReadProjectCategoryBudgetProjection.user_id,
)
Index(
    "ix_read_pcb_ledger_project",
    ReadProjectCategoryBudgetProjection.ledger_id,
    ReadProjectCategoryBudgetProjection.project_sync_id,
)


class ReadTxTemplateProjection(Base):
    """交易範本(§2.7 MOZE_FEATURE_GAP_SD.md Phase 3)。ledger-scoped,跟
    budget 同款 PK=(ledger_id, sync_id)。存一组常用的
    tx_type/amount/category/account 组合,`POST .../apply` 端点直接把内容
    套进一笔新交易(复用 `snapshot_mutator.create_transaction`),範本本身
    不生成任何交易、不带任何排程逻辑,是六步 checklist 里最简单的一种。"""

    __tablename__ = "read_tx_template_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, default="")
    tx_type: Mapped[str] = mapped_column(String(16), default="expense")
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tag_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_tx_template_user_id",
    ReadTxTemplateProjection.user_id,
)
Index(
    "ix_read_tx_template_ledger_sort",
    ReadTxTemplateProjection.ledger_id,
    ReadTxTemplateProjection.sort_order,
)


class ReadCardRewardRuleProjection(Base):
    """信用卡紅利回饋規則(§2.9.5 Phase 4.5 MOZE_FEATURE_GAP_SD.md)。
    user-global,PK=(user_id, sync_id)——不像 debt/recurring_rule/
    installment_plan 那樣掛 ledger_id,因為它綁定的 `account_sync_id`
    (信用卡帳戶)本身就是 user-global 實體,規則跟著帳戶走同一個 scope,
    跟 account/category/tag 同款。回饋金額不落庫:讀路徑
    (`services.card_rewards`)從 `account_sync_id` 當期交易即時算出,
    跟 `ReadDebtProjection.remaining_amount`/
    `ReadInstallmentPlanProjection.paid_periods` 是同一個「不落表、讀路徑
    即時加總」取捨,見那兩個 docstring。"""

    __tablename__ = "read_card_reward_rule_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_sync_id: Mapped[str] = mapped_column(String(255), default="")
    label: Mapped[str] = mapped_column(Text, default="")
    # nullable JSON array of category sync_id;None/空 = 所有消費都適用。
    category_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_type: Mapped[str] = mapped_column(String(16), default="percentage")
    rate_value: Mapped[float] = mapped_column(Float, default=0.0)
    # 單筆取整方式(round/floor/ceil/keep,keep = 保留小數不取整,見
    # services/card_rewards.py::_round_amount)。
    rounding: Mapped[str] = mapped_column(String(8), default="round")
    # 總額取整方式(Phase 8 #4,2026-08 使用者反饋「四捨五入」實際還有小數):
    # 對齊 Moze「單筆保留小數、總額才取整」的兩段式設計,round/floor/ceil 取整
    # 到整數,keep = 維持既有二位小數彙總行為(向下相容既有規則)。
    total_rounding: Mapped[str] = mapped_column(String(8), default="round")
    calc_basis: Mapped[str] = mapped_column(String(24), default="transaction_date")
    interval: Mapped[str] = mapped_column(String(16), default="billing_cycle")
    min_spend_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_tx_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    cap_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    cap_shared_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 自動入帳(§2.9.5.4,2026-08-07):"manual" = 純顯示不自動化(既有規則
    # 升級後的預設值,行為不變)。immediate_after_tx/after_posting_date 用
    # settlement_days(逐筆交易 happened_at + N 天);period_end 不需要
    # settlement_days(算法定的期間結束日);reward_account_id 是目的帳戶,
    # settlement_type != "manual" 時必填,見
    # src/services/card_reward_payout.py。
    settlement_type: Mapped[str] = mapped_column(String(24), default="manual")
    settlement_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 週期結束後一次結算的回饋入帳日(Phase 8 #15,2026-08 使用者反饋):
    # 僅 settlement_type == "period_end" 時有意義,兩者皆為 None 時維持現況
    # 行為(期間結束當天入帳,向下相容既有規則)。見
    # services/card_rewards.py::compute_settlement_date。
    settlement_month_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    settlement_day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reward_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


class CardRewardPayout(Base):
    """信用卡紅利回饋自動入帳(§2.9.5.4)去重台帳。不是 sync entity(不進
    sync_changes/projection),也不是 §2.1 通知中心的一部分——是內部
    idempotency 記錄。理由見 `src/services/card_reward_payout.py`
    docstring:`Notification` 表的「查歷史 payload 比對」去重法只適合
    「每個(帳戶,週期)至多一次」的既有排程(autopay/reminders),
    immediate_after_tx/after_posting_date 是逐筆交易觸發,量級不同,沿用
    `Notification` 會讓去重查詢隨時間無界成長、也會把通知中心灌爆。

    `dedup_key`:逐筆結算類型是交易的 sync_id;period_end 是
    period_end.isoformat()。"""

    __tablename__ = "card_reward_payouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    rule_sync_id: Mapped[str] = mapped_column(String(255))
    dedup_key: Mapped[str] = mapped_column(String(255))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    payout_tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index(
    "ux_card_reward_payouts_dedup",
    CardRewardPayout.user_id,
    CardRewardPayout.rule_sync_id,
    CardRewardPayout.dedup_key,
    unique=True,
)


Index(
    "ix_read_card_reward_rule_account",
    ReadCardRewardRuleProjection.user_id,
    ReadCardRewardRuleProjection.account_sync_id,
)


# ============================================================================
# Backup —— 备份配置 + 定时任务 + 历史。详见 .docs/backup-rclone-plan.md。
# 5 张表:remote / schedule / schedule_remote(M2M) / run / run_target(per-target)
# ============================================================================


class BackupRemote(Base):
    """rclone 远端配置。每条对应 rclone.conf 里一段 [name],可以是底层 backend
    (s3 / gdrive / ...)或 crypt 装饰层。`encrypted=True` 表示这条是 crypt 套
    在另一条 backend 之上,实际备份目标都用 crypt 远端 —— 底层 backend 通常不
    单独被 schedule 引用,只是给 crypt 当宿主。"""

    __tablename__ = "backup_remotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    backend_type: Mapped[str] = mapped_column(String(32))  # 's3' / 'gdrive' / 'crypt' / ...
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    config_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_backup_remote_user_name"),
    )


class BackupSchedule(Base):
    __tablename__ = "backup_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cron_expr: Mapped[str] = mapped_column(String(64))  # 5-field crontab
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    include_attachments: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BackupScheduleRemote(Base):
    """schedule ↔ remote 多对多 —— 一个 schedule 可以 fan-out 推到多个 remote
    做冗余备份。"""

    __tablename__ = "backup_schedule_remotes"

    schedule_id: Mapped[int] = mapped_column(
        ForeignKey("backup_schedules.id", ondelete="CASCADE"), primary_key=True
    )
    remote_id: Mapped[int] = mapped_column(
        ForeignKey("backup_remotes.id", ondelete="RESTRICT"), primary_key=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BackupRun(Base):
    """单次备份运行记录。一次 run 对应一份 tar.gz,可能并行推到 N 个 remote
    (每个 remote 一条 BackupRunTarget 子状态)。"""

    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("backup_schedules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'running' / 'succeeded' / 'partial' / 'failed' / 'canceled'
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    backup_filename: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class BackupRunTarget(Base):
    """每次 run 对每个 target remote 的 push 状态。fan-out 场景用,partial
    成功时哪个 remote 失败的写在这里。"""

    __tablename__ = "backup_run_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("backup_runs.id", ondelete="CASCADE"), index=True
    )
    remote_id: Mapped[int] = mapped_column(
        ForeignKey("backup_remotes.id"), index=True
    )
    # 'pending' / 'running' / 'succeeded' / 'failed'
    status: Mapped[str] = mapped_column(String(16), default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bytes_transferred: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScheduledJobConfig(Base):
    """背景排程管理後台(§ 排程管理 Phase 5)——把 `main.py` 裡原本散落在 4 條
    各自獨立 asyncio 迴圈的 7 個排程動作(mcp 日誌清理 / 週期性收支物化 /
    借還款提醒 / 信用卡繳款提醒 / 自動扣繳 / 信用卡紅利回饋入帳)收斂成一張
    設定表 + 統一的 60 秒輪詢迴圈(`main.py::_start_scheduled_jobs_loop`),
    讓 admin 可以在後台調整頻率/停用/立即執行,不需要改代碼重新部署。
    `job_key` 對應 `services/scheduled_jobs.py::JOB_REGISTRY` 的 key,是全域
    單例設定(不分 user),比照 `internal_tasks.py` 既有手動觸發端點一樣
    是運維層級操作。"""

    __tablename__ = "scheduled_job_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_run_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppVersionCheckConfig(Base):
    """App 端新版本提醒(docs/superpowers/specs/2026-09-08-app-update-reminder-design.md)。

    單例表,固定 `id=1`(比照 `ExchangeRateCache` 的單行寫法,但這裡連
    base_currency 這種天然 key 都沒有,索性用整數 PK 固定為 1)。
    `latest_version` 是公開端點 `/app-version/latest` 讀取的來源,由
    `services/scheduled_jobs.py` 的 `check_latest_app_version` job 定期從
    `nas_webdav_url` 偵測寫入,管理者也可以在後台直接手動改。

    `nas_webdav_password` 明文存 DB —— 跟 `BackupRemote` 的 rclone 密碼欄位
    同款考量:這台 server 本來就是自架、只有管理者能存取,不在這次範圍內
    加額外加密層。Admin API 讀取時只回傳「是否已設定」的布林值,不回顯明文
    (見 `routers/admin_app_version.py`)。
    """

    __tablename__ = "app_version_check_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    latest_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nas_webdav_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    nas_webdav_user: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nas_webdav_password: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # 最低可同步版本(docs/LICENSE_KEYS.md §版本門檻)。App 帶的 `X-App-Version`
    # 低於這個版本(或根本沒帶 —— 舊版 App)時,server 拒絕該 App 的所有
    # 鑑權請求(426)。null = 不限制。
    min_sync_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LicenseKey(Base):
    """授權金鑰(docs/LICENSE_KEYS.md)。

    管理者在後台 `/admin/licenses` 產生;一把金鑰只能被**一個帳號**啟用一次
    (`redeemed_by_user_id` 一旦寫入就不再變動),啟用當下才開始算效期:
    `expires_at = max(現在, 該帳號目前授權到期日) + duration_days`——已經有
    有效授權時再輸入新金鑰會「接在剩下的天數後面」,不會吃掉剩餘天數。

    使用者目前的授權狀態不另存欄位,一律即時從這張表算
    (`services/license.py::get_user_license_expiry`):該使用者名下
    未撤銷、`expires_at` 最大的那一筆。這樣撤銷一把金鑰(`revoked_at`)
    會立刻生效,不會有「User 表上的快取欄位忘了同步」的漏洞。

    `key` 明文存放 —— 跟 `AppVersionCheckConfig.nas_webdav_password` 同款
    考量:這台 server 只有管理者能碰 DB,管理者需要能在後台重新複製「還沒
    賣出去」的金鑰。已被啟用的金鑰就算外洩也無法再被別的帳號啟用。
    """

    __tablename__ = "license_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    duration_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    redeemed_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
