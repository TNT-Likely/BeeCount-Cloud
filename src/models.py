from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    tx_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # 优惠/折扣(0021):JSON 文本列,形态
    #   {"mode": "percent"|"amount", "value": 0.85|20.0,
    #    "couponName": "满100减20", "originalAmount": 120.0,
    #    "discountAmount": 20.0, "finalAmount": 100.0}
    # mode=percent 时 value 为折扣率(0.85=85 折);mode=amount 时为减免额。
    # originalAmount/discountAmount/finalAmount 是前端算好的快照,
    # finalAmount 恒等于 amount(实付)。仅 expense 有意义;NULL=无优惠。
    discount_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 转账手续费(0021):JSON 文本列,形态
    #   {"amount": 5.0, "mode": "deduct_from_source"|"deduct_from_target"
    #    |"separate_account"|"discount", "feeAccountId": "...", "feeAccountName": "..."}
    # deduct_from_source=转出账户扣 amount+fee(到账 amount);
    # deduct_from_target=转出扣 amount(到账 amount-fee);
    # separate_account=手续费从 feeAccountId 另扣;
    # discount=转出方少扣 fee(实扣 amount-fee,到账 amount)——银行/平台优惠。
    # 仅 transfer 有意义。
    transfer_fee_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 多账户合并付款(0021):JSON 文本列,形态
    #   [{"accountId": "a1", "accountName": "支付宝", "amount": 60.0}, ...]
    # sum(legs) 恒等于 amount(服务端容差 0.01)。仅 expense 有意义;
    # 单账户付款不发该字段(旧行为),NULL 或空数组视作单账户。
    payment_legs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 成员分摊(0024):JSON 文本列,形态
    #   [{"memberId": "m1", "memberName": "爸爸", "ratio": 0.5, "amount": 50.0}, ...]
    # 跟 payment_legs 的关键差别:这是「按人」拆分,不影响账户余额,只用于
    # 成员维度统计;sum(shares.amount) 恒等于 amount(容差 0.011)。
    # 仅 expense 有意义(支出才谈得上谁花的);NULL = 未分摊,统计时按记账人归属。
    member_shares_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 归物(0029):关联物品的 sync_id,弱引用(不设 FK —— 物品被删不阻塞交易)。
    # 记一笔支出时可选挂到某件物品,该笔金额计入物品累计成本(维护/配件/
    # 延保)。跟 category_sync_id 同款只存 id,不冗余物品名。
    item_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 定期存款利息收入:一笔收入交易可选挂到某个定期存款(利息按键生成),
    # 该笔金额计入该存款「已收利息」。只存 id,弱引用(物品/存款被删不阻塞交易)。
    time_deposit_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


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
# 归物(0029):物品维度捞关联交易(物品详情列出所有关联交易)
Index(
    "ix_read_tx_ledger_item",
    ReadTxProjection.ledger_id,
    ReadTxProjection.item_sync_id,
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
    # 账户别名(0027)。所有账户类型可用 —— 账户名是"招商银行信用卡(1234)"
    # 这类正式名,别名是用户在列表/卡片上想看到的短名("招行卡")。为空 =
    # 沿用 name。只影响展示,不参与任何统计口径。
    alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credit_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    billing_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_due_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 出账日(账单日)当天消费计入哪一期账单(0027)。true = 计入当期账单
    # (周期 = 上期出账日+1 ~ 本期出账日);false = 计入下一期(周期 = 本期
    # 出账日 ~ 下期出账日-1)。非信用卡账户该字段无意义,但照样落库(避免
    # 类型来回切换时丢值)。默认 true —— 主流银行账单日当天消费入本期。
    billing_day_in_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_last_four: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 完整卡号(0026)。隐私敏感,仅用户手动录入;card_last_four 可由其派生。
    card_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 账户图标 key(0026):内置银行/支付图标注册表的主键(如 'icbc');空 = 按
    # account_type 回退默认图标。未识别银行时用户手动指定。
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # 账户隐藏(issue #240)。default false:既有行升级后不隐藏,旧 App 不发该
    # 字段时保持 false。只影响前端选择器/列表呈现,服务端不做任何统计过滤(D1)。
    hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )


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


# ============================================================================
# 定期存款 / 归物(0021)— ledger-scoped 实体,跟 tx/budget 同一条同步链路:
# mobile /sync/push → sync_changes 事件流 → 本投影表(web 读唯一权威源)。
# 语义对齐 BeeCount 仓 issue #80(定期存款)/ #465(归物/退款)。
# ============================================================================


class ReadTimeDepositProjection(Base):
    """定期存款投影。金额语义数据(native_amount 同类)不走 Float 的列用 String
    存 rate 之外的金额;principal / actual_interest 仍用 Float 对齐其它金额列
    (mobile Drift 同款 REAL 列),rate 是展示型数据。

    派生字段(expected_interest / days_remaining / matured)不落库 —— 读端点
    按今天日期现算,避免后台任务刷状态。
    """

    __tablename__ = "read_time_deposit_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    principal: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", server_default="CNY")
    # 年利率,小数(0.0325 = 3.25%)。展示型数据,不参与强一致金额计算。
    annual_rate: Mapped[float] = mapped_column(Float, default=0.0)
    # 期限(月)。到期日 maturity_date 是权威列;term_months 变更时由 mutator
    # 重算 maturity_date(保持两者一致)。
    term_months: Mapped[int] = mapped_column(Integer, default=12, server_default="12")
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    maturity_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 'holding'(存期内) | 'matured'(已到期未支取) | 'withdrawn'(已支取)。
    # holding→matured 由读端点按日期派生提示;withdrawn 是用户显式支取动作。
    status: Mapped[str] = mapped_column(String(16), default="holding", server_default="holding")
    # 资金来源账户(user-global account 的 sync_id;rename 不改 id,只刷 name)。
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 到期自动转存(本金+利息续存)。
    auto_renew: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # 计息方式:'simple'(单利,默认) | 'compound'(复利,按年)。
    interest_mode: Mapped[str] = mapped_column(
        String(16), default="simple", server_default="simple"
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 支取时间(status='withdrawn' 时必填;其它状态可空)。
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 实得利息(支取时用户填写;未支取为 NULL,读端点按利率现算预期利息)。
    actual_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 到息计划(0028,多笔):JSON 数组文本
    # [{"date":"YYYY-MM-DD","amount":123.0,"note":""}] —— date 到息日 /
    # amount 该次金额(自由金额)/ note 可选。按到息日升序存。
    # 计划仅作预期提醒:不生成交易、不进余额;是否已到息由读端点按今天派生。
    payout_plans_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_time_deposit_user_status",
    ReadTimeDepositProjection.user_id,
    ReadTimeDepositProjection.status,
)


class ReadItemReturnProjection(Base):
    """归物(退货/退还物品)投影。对应 BeeCount 仓 issue #465 的「退款」诉求:
    记录一笔购买的物品被归还,跟踪退款状态与金额。

    status: 'pending'(待归还) | 'returned'(已归还待退款)
          | 'refunded'(已退款入账) | 'cancelled'(已取消/不归了)。
    original_tx_id 关联原始购买交易(read_tx_projection.sync_id),弱引用 ——
    原始交易被删也不阻塞归物记录(不设 FK,只存 sync_id 字符串)。
    """

    __tablename__ = "read_item_return_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, default="")
    # 关联原始购买交易 sync_id(弱引用,可空)。
    original_tx_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 退款金额(实收;可能与原金额不同,比如扣了损耗)。
    refund_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # 归还/退款日期(归物记录的业务日期)。
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    # 退款入账账户(user-global account sync_id;可空,待定状态允许不选)。
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 退款对应分类(可选,用于统计退回原分类)。
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_item_return_user_status",
    ReadItemReturnProjection.user_id,
    ReadItemReturnProjection.status,
)


class ReadItemProjection(Base):
    """归物(物品持有管理)投影(0029)。

    把「归物」重新定义为**物品持有**:记录一件物品的购买日期与金额,按存活
    时间摊出日均成本,跟踪保修,直到二手卖出或报废才停止记录。

    - purchase_date / purchase_amount:购买日期与金额(账本本位币口径;关联
      交易也取 native_amount 累计,多币种账本下才自洽)。
    - warranty_until:保修到期日。到期只是**过保**,物品仍存活 —— 过保不改
      status,由读端点按今天派生 warranty_status。
    - status: 'in_use'(在用) | 'disposed'(已处置)。只有二手卖出
      (disposal_type='sold')或报废('scrapped')才转 disposed;处置日期
      disposal_date 之后的天数不再累计(持有天数定格)。
    - disposal_amount:二手卖出收入,抵减累计成本(净成本口径);报废无收入。

    派生字段(held_days / total_cost / daily_cost / warranty_status)不落库 ——
    读端点按今天现算,跟定期存款 expected_interest 同款,避免后台任务刷状态。
    """

    __tablename__ = "read_item_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text, default="")
    purchase_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purchase_amount: Mapped[float] = mapped_column(Float, default=0.0)
    # 保修到期日(NULL = 未记录保修)。到期只过保,不结束持有。
    warranty_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 'in_use'(在用,默认) | 'disposed'(已处置:二手卖出 or 报废)。
    status: Mapped[str] = mapped_column(
        String(16), default="in_use", server_default="in_use"
    )
    # 处置方式:'sold'(二手卖出,有收入) | 'scrapped'(报废,无收入)。
    # status='disposed' 时有意义。
    disposal_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    disposal_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 二手卖出收入(抵减成本);scrapped 时 NULL / 0。
    disposal_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 物品分类 / 购买账户(可选,弱引用 + 名称冗余,改名只刷 *_name)。
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_item_user_status",
    ReadItemProjection.user_id,
    ReadItemProjection.status,
)


class ReadRefundProjection(Base):
    """交易退款投影(0022)。对应 BeeCount 仓 issue #465 的「退款功能」:
    一笔支出被部分/全额退回时,挂在这笔原始交易上的退款记录。

    与归物(read_item_return_projection)的分工:归物跟踪「物品归还」流程
    (pending→returned→refunded),本表跟踪「钱」——任意原因的支出退款
    (服务退订、多扣费冲销、价保差价……),不依赖物品寄回。

    - 同一原始交易可有多条退款(部分退、多次退),累计(非 cancelled)
      不得超过原始交易金额 —— 由 web write 路径校验(见
      routers/write/refunds.py 的 _make_refund_validator)。
    - status: 'pending'(待退款/处理中) | 'received'(已到账)
      | 'cancelled'(已取消,不计入累计)。
    - original_tx_id 关联原始支出交易(read_tx_projection.sync_id),弱引用
      —— 原始交易被删也不阻塞退款记录(不设 FK,只存 sync_id 字符串),
      original_amount 是写入时从原交易快照的 denorm。
    """

    __tablename__ = "read_refund_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 关联原始支出交易 sync_id(弱引用;NOT NULL —— 退款必须挂靠原单)。
    original_tx_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # 原单金额快照(写入时从原交易带过来的 denorm,原交易删除后仍可展示)。
    original_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 本笔退款金额(> 0;部分退时小于原单)。
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", server_default="CNY")
    # 退款去向账户(user-global account sync_id;可空,待退款状态允许不选)。
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 退款日期(业务日期,日粒度)。
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 退款分摊模式(0025):'unified'(默认,统一退至单一账户) |
    # 'split'(分别退款,按原付款 legs 拆分到多张卡)。unified 时 refund_legs_json
    # 为 NULL,退到 account_sync_id;split 时 account_sync_id 可空,退到各腿账户。
    refund_mode: Mapped[str] = mapped_column(
        String(16), default="unified", server_default="unified"
    )
    # 分别退款(0025)的退款腿:JSON 文本列,形态
    #   [{"accountId": "a1", "accountName": "工行卡", "amount": 60.0}, ...]
    # sum(legs) 恒等于 amount(容差 0.011)。仅 split 模式有意义;
    # unified 模式为 NULL。每条腿把退款收入冲回对应卡余额。
    refund_legs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


# user_id 单列索引由列的 index=True 自动生成(ix_read_refund_projection_user_id),
# 跟 time_deposit / item_return 同款约定;这里只显式声明复合索引。
Index(
    "ix_read_refund_user_status",
    ReadRefundProjection.user_id,
    ReadRefundProjection.status,
)
# 累计退款查询(同一原单的非 cancelled 合计)按 (ledger, original_tx_id) 走。
Index(
    "ix_read_refund_ledger_original_tx",
    ReadRefundProjection.ledger_id,
    ReadRefundProjection.original_tx_id,
)


class ReadLoanProjection(Base):
    """借款投影(0023)。个人记账里的借入 / 借出跟踪:

    - direction: 'borrowed'(借入,我欠别人) | 'lent'(借出,别人欠我)。
    - counterparty:对方名称(人 / 机构),NOT NULL。
    - principal:本金(> 0)。
    - interest_rate:年利率,小数(0.05 = 5%),展示型;默认 0。
    - started_at:起借日;due_at:到期日(可空,无限期)。
    - repaid_amount:已还本金累计(默认 0),前端按 principal 现算还款进度;
      不额外建还款子实体,保持与 0021/0022 同款「单记录 + 状态」轻量模型。
    - status: 'active'(在借/在贷) | 'repaid'(已结清) | 'cancelled'(作废)。

    派生字段(is_overdue / days_to_due / repay_progress)不落库,读端点按今天
    现算。
    """

    __tablename__ = "read_loan_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(16), default="borrowed", server_default="borrowed")
    counterparty: Mapped[str] = mapped_column(Text, default="")
    principal: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", server_default="CNY")
    interest_rate: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    repaid_amount: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_loan_user_status",
    ReadLoanProjection.user_id,
    ReadLoanProjection.status,
)


class ReadReimbursementProjection(Base):
    """报销投影(0023)。个人记账里的「我先垫付、后找人 / 公司报销」跟踪:

    与退款(read_refund_projection)的分工:退款是「买的东西退了,钱回来」,
    报销是「我替公司 / 别人花了钱,对方把钱还我」—— 收款方不同、语义不同,
    故独立成表(App 端也单独要这两个能力)。

    - title:报销事由(必填)。
    - payer:实际垫付方(通常是我,可空)。
    - reimburser:报销给付方(公司 / 他人,可空)。
    - amount:应收报销金额(> 0)。
    - category_sync_id / category_name:对应支出分类(可选,用于归类)。
    - happened_at:垫付日期(业务日期,日粒度,必填)。
    - status: 'pending'(待报销) | 'reimbursed'(已报销到账)
      | 'cancelled'(已取消)。
    - account_sync_id / account_name:报销到账账户(可空,待报销允许不选)。
    - related_tx_id:关联的垫付交易 sync_id(弱引用,可空)。
    """

    __tablename__ = "read_reimbursement_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text, default="")
    payer: Mapped[str | None] = mapped_column(Text, nullable=True)
    reimburser: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), default="CNY", server_default="CNY")
    category_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_tx_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_reimbursement_user_status",
    ReadReimbursementProjection.user_id,
    ReadReimbursementProjection.status,
)


class ReadMemberProjection(Base):
    """账本内自定义成员投影(0024)。

    跟 `LedgerMember`(共享账本的**登录用户**成员)是两套东西:这里是账本自己
    维护的「人」名单(爸爸 / 妈妈 / 我),不要求对方有账号,单人账本也能用。
    用途是给支出账单做**人头分摊**(member_shares),让统计能回答「谁花了多少」。

    member_id 是 sync_id(跟其它 ledger-scope 实体同款 PK 约定)。
    """

    __tablename__ = "read_member_projection"

    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # 成员名(必填),如「爸爸」「妈妈」「我」。同账本内建议唯一,服务端不强制
    # (重名靠 sync_id 区分,UI 已可辨)。
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 备注(可空)。
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 成员图标 key:人物/家庭语义的 Material Symbols 图标名(见前端
    # memberIcons.ts 的 MEMBER_ICONS 注册表);空 = 前端回退默认 'person'。
    # 只影响展示,不参与任何统计口径。
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 关联的记账人(登录用户 id)。设了之后,统计口径里该用户记的、没有分摊的
    # 交易并入本成员(kind='member'),不再单独出一条 kind='user' —— 避免同一
    # 个人在「成员」和「记账人」两个维度里被算两遍。空 = 不关联。
    linked_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 列表排序权重,小的在前;默认 0 时按 sync_id 兜底,保证顺序稳定。
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_read_member_ledger_name",
    ReadMemberProjection.ledger_id,
    ReadMemberProjection.name,
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
