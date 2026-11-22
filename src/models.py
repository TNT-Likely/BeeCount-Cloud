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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MCPWriteOperation(Base):
    """Durable normalized write plan; retries reuse the exact original payloads."""
    __tablename__ = "mcp_write_operations"
    __table_args__ = (UniqueConstraint("user_id", "request_key", name="uq_mcp_operation_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    plan_json: Mapped[list] = mapped_column(JSON)
    results_json: Mapped[list] = mapped_column(JSON, default=list)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MCPStateRecord(Base):
    """Versioned, ledger-scoped workflow data; never interpreted as executable code."""
    __tablename__ = "mcp_state_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    data_json: Mapped[dict] = mapped_column(JSON)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MCPSourceReference(Base):
    __tablename__ = "mcp_source_references"
    __table_args__ = (UniqueConstraint("ledger_id", "source_system", "source_id", name="uq_mcp_source_identity"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    tx_sync_id: Mapped[str] = mapped_column(String(255), index=True)
    source_system: Mapped[str] = mapped_column(String(128))
    source_id: Mapped[str] = mapped_column(String(255))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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

    # 用户级锁账(0067):is_locked 打开后,lock_date 当天及之前的交易一律
    # 禁止新建/修改/删除/变动;账户初始金额同样冻结(user-global,无账本维度)。
    # 解锁只需关掉开关,日期保留,方便再次锁定。
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    lock_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    # 转账手续费/优惠(0022):仅 transfer 主交易使用。fee/discount 金额为原币金额;
    # fee_account 指定手续费从哪个账户扣除(默认转出账户);fee/discount_tx 为关联生成的
    # 支出/收入流水 sync_id,用于列表标识、明细与级联更新删除。
    transfer_fee_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    transfer_discount_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    transfer_fee_account_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transfer_fee_account_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    transfer_fee_tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transfer_discount_tx_sync_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 支出优惠(0023):仅 tx_type=expense 使用,记录该笔消费享受的优惠/减免金额。
    # 金额为交易原币金额;amount 仍是实际入账金额,优惠只作为明细与统计元数据。
    discount_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 成员(0024):income/expense 可关联 1 个成员,也可按多个成员分摊。
    # member_sync_ids_json = JSON 数组(便于按成员筛选/统计);
    # member_names_csv = 反规范化姓名串(便于列表展示与 LIKE 搜索);
    # member_splits_json = [{memberId,name,amount}] 分摊明细,金额之和 = amount。
    # 三者都由写入链路从 memberIds/memberNames/memberSplits 快照字段同步维护。
    member_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    member_names_csv: Mapped[str | None] = mapped_column(Text, nullable=True)
    member_splits_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 退款(0025):tx_type='refund' 的交易是一笔退款,必须关联原单。
    # is_refund = 1 标记退款交易;refund_of_sync_id 指向被退款的原始交易 sync_id。
    # 退款退回接收账户余额(account_sync_id)、冲减原交易分类支出,但不计入收入、
    # 不污染月度收支(analytics 仅统计 income/expense,refund 被排除)。
    is_refund: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    refund_of_sync_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 支出组合付款(0028):一笔 expense 由多个账户分摊支付。
    # JSON 数组 [{accountId,accountName,amount}],金额之和=amount;
    # 存在时账户余额/统计按拆分扣减(不计主 account),总额仅计一次。
    payment_splits_json: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # 信用卡：账单日/还款日(秒精确)。不设卡账户为 None。
    billing_day: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payment_due_day: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 账单周期锚点日:按 billing_day 自动划分的账期起始日(本地日期)
    billing_cycle_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 账单状态:未出账(0)/已出账(1)/已还款(2)/部分还款(3)/已逾期(4)
    bill_status: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )
    # 本期应还/最低还款/已还/剩余待还(均为原币金额)
    bill_amount_due: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    bill_minimum_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=0.0
    )
    bill_paid_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=0.0
    )
    bill_remaining_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=0.0
    )
    bank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_last_four: Mapped[str | None] = mapped_column(String(23), nullable=True)
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)
    # 账户隐藏(issue #240)。default false:既有行升级后不隐藏,旧 App 不发该
    # 字段时保持 false。只影响前端选择器/列表呈现,服务端不做任何统计过滤(D1)。
    hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # 图标相关键：icon_key 对应公共图标库(如 bank-logos)的键；icon_type 标识图标类型(bank|card|custom)；background_key 对应自定义背景(渐变/纯色键)
    icon_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    icon_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    background_key: Mapped[str | None] = mapped_column(String(64), nullable=True)


# ============================================================================
# 储蓄罐 / 存款计划。user-scope projection(Q-side)。
# 两张表:savings_plan(计划本体)+ savings_deposit(每笔存入记录)。
# 业务键 (user_id, sync_id),跟账本无关 —— 计划跨账本可见,存款记录
# 关联到 plan 而非 ledger。
# ============================================================================


class UserSavingsPlanProjection(Base):
    """储蓄计划。用户自定义目标(旅游/买房/电子产品等),跟踪进度。"""

    __tablename__ = "user_savings_plan"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 关联储蓄账户的 sync_id(可空:不绑定具体账户,只做进度追踪)
    account_sync_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    # 计划类型:free(自由储蓄) / fixed(定期定额) / wish(心愿储蓄)
    plan_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="free", default="free"
    )
    # 当前已存金额(由 deposit 记录汇总得到,写入时同步更新)
    current_amount: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0", default=0.0
    )
    # 状态:active(进行中) / completed(已完成) / abandoned(已放弃)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active", default="active"
    )
    # 每月自动存款提醒金额(可空:None 表示不提醒)
    monthly_reminder_amount: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


class UserSavingsDepositProjection(Base):
    """储蓄计划的每笔存入记录。金额从关联账户扣减(前端/客户端负责记一笔
    对应支出交易;服务端只存 deposit 本身,不做账户余额联动 —— 余额由
    ledger 的 tx 流水驱动,deposit 是"标记"层,不重复造余额逻辑)。"""

    __tablename__ = "user_savings_deposit"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    savings_plan_sync_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    # 存入金额(原币)
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 存入时间(秒精确)
    deposited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # 关联的 ledger 交易 sync_id(从已有交易"标记为储蓄"时填,手动存入时可空)
    linked_tx_sync_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


Index(
    "ix_savings_deposit_plan",
    UserSavingsDepositProjection.user_id,
    UserSavingsDepositProjection.savings_plan_sync_id,
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


class UserMemberProjection(Base):
    """消费/收入「成员」(0024),user-global 实体,跟 tag/category 同构。

    与 `LedgerMember`(共享账本协作者,按 user 账号)完全不同:这里是用户自定义
    的家庭/团队成员名单,用于给收支打归属、按人分摊,以及作为统计维度。
    """

    __tablename__ = "user_member_projection"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sync_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 关联人资料(头像 / 多联系方式 / 多银行卡)，与 contacts 共用结构。
    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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


# ============================================================================
# Contacts & Loans (Web-first feature, NOT part of the mobile sync layer)
# ============================================================================
#
# 联系人(Contact)是 user-global 资源;借款(Loan)与还款(LoanRepayment)是
# ledger-scoped 资源。它们**不**走 sync_changes / read projection 那套 CQRS,
# 而是服务端直管的独立表 + 直连 REST CRUD(见 routers/contacts_loans.py)。
# 理由:这是 web 端新增的资金往来管理,不要求移动端离线双向同步,且借款与
# 账户/交易解耦(应收款/应付款是"虚拟账户",实时从贷款余额派生,不污染真实
# 账户余额与交易列表)。


class Contact(Base):
    """记账涉及的自然人 / 商家对手方。user-global(跟 tag/member 同层级)。

    - contact_tag:朋友/家人/同事/商家(friend/family/colleague/merchant),自由枚举。
    - phone/wechat/alipay:可选联系方式。
    - avatar_file_id:复用 attachment_files 的 id(存 user-global 头像)。
    """

    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # friend / family / colleague / merchant
    contact_tag: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wechat: Mapped[str | None] = mapped_column(String(128), nullable=True)
    alipay: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 关联人资料(头像 / 多联系方式 / 多银行卡)的 camelCase JSON，见 party_profile。
    profile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0050+ 状态管理:
    # - is_archived:归档。数据完整保留,但从默认列表/借款建议/排行榜中隐藏,
    #   只能在「已归档」筛选里查看,可随时恢复。
    # - is_active:停用。仍在列表可见(带「已停用」标记),但禁止新增借款与
    #   新关联引用,历史数据不变;可随时重新启用。两者互相独立。
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    @property
    def profile(self) -> dict | None:
        """REST 读视图：profile_json 为空时用旧单值列合成，兼容历史数据。"""
        from .party_profile import profile_with_legacy

        return profile_with_legacy(
            self.profile_json,
            phone=self.phone,
            wechat=self.wechat,
            alipay=self.alipay,
            avatar_file_id=self.avatar_file_id,
        )

    @profile.setter
    def profile(self, value) -> None:
        """REST 写视图：归一化后落 profile_json，并同步旧单值列。

        value is None 表示「本次未提供」——不动旧列，避免老客户端只传 phone
        时把单值清掉；显式传 {} / null 内容则视为清空，旧列一并归零。
        """
        from .party_profile import decode_profile, encode_profile, legacy_values

        if value is None:
            return
        self.profile_json = encode_profile(value)
        for key, legacy in legacy_values(decode_profile(self.profile_json)).items():
            setattr(self, key, legacy)


class Loan(Base):
    """借入 / 借出记录。ledger-scoped(跟 transaction 同层级)。

    direction:
      - 'lend'  = 借出(别人欠我)→ 计入「应收款」
      - 'borrow'= 借入(我欠别人)→ 计入「应付款」
    status(实时派生,但落库便于查询/索引):
      - 'unpaid'  未还清(未到期且 0 还款)
      - 'partial' 部分还款(已还 > 0 且未到期且未结清)
      - 'overdue' 已逾期(过了 due_at 且未结清)
      - 'settled' 已结清(已还 >= 本金)
    派生优先级:settled > overdue > partial > unpaid(逾期优先于部分还款,
    因为逾期更紧急;详情页仍展示还款进度条)。
    interest_type:none(无息)/ fixed(固定利息额)/ annual(年利率 %)。
    voucher_file_ids_json:借款凭证图片 attachment file id 的 JSON 数组。
    """

    __tablename__ = "loans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True
    )
    # 关联联系人(可为空:匿名借款也允许)。删除联系人时 SET NULL(由 REST 层
    # 在有关联借款时拒绝删除,正常情况下不会触发)。
    contact_id: Mapped[str | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 'lend' / 'borrow'
    direction: Mapped[str] = mapped_column(String(16), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 'none' / 'fixed' / 'annual'
    interest_type: Mapped[str] = mapped_column(String(16), default="none")
    # annual 用:年利率百分比(如 5 表示 5%)
    interest_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # fixed 用:固定利息金额(原币)
    interest_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    voucher_file_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="unpaid", index=True)
    # 借款关联的**真实账户**(扣款/到款账户)。借出=扣款账户(钱从此出);
    # 借入=到款账户(钱入此账)。用于生成对应流水并影响账户余额。
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 本笔借款自动生成的真实交易流水 syncId(排除统计的 expense/income),
    # 用于编辑/删除借款时联动维护这笔流水。None 表示未生成(如未选账户)。
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LoanRepayment(Base):
    """单笔还款记录(支持分期)。ledger-scoped,级联删除归属 loan。

    attachment_file_ids_json:还款凭证图片 attachment file id 的 JSON 数组,
    与 Loan.voucher_file_ids_json 同形态;生成还款流水时会转成 AttachmentRef
    写进交易的 attachments_json,让交易详情也能看到这些凭证。

    利息(0033):借出收回时利息为 income「借款利息」,借入偿还时利息为 expense
    「借款利息」,均 exclude_from_stats/budget；利息不计入本金结算,但与该笔还款关联。
    """

    __tablename__ = "loan_repayments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True
    )
    loan_id: Mapped[str] = mapped_column(
        ForeignKey("loans.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    repaid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 还款流水关联的真实账户(继承所属借款的账户)。
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 本笔还款自动生成的真实交易流水 syncId(排除统计的 income/expense)。
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 还款凭证附件 file id(JSON 数组)。旧行是 NULL,读取时按空数组处理。
    attachment_file_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 利息:金额>0 时并入该笔还款流水的金额(不单独成流水、不影响本金结算)。
    interest_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    interest_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    interest_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    interest_tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 合并组(0039):一次实际收付款覆盖多笔借款时,拆到各笔借款的还款行共享
    # 同一个 group_id;组内**只有一行**持有 tx_sync_id(合并后的唯一一条本金流水),
    # 其余行 tx_sync_id 为空,避免同一笔钱在交易流水里出现多条。
    group_id: Mapped[str | None] = mapped_column(
        ForeignKey("loan_payment_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoanPaymentGroup(Base):
    """一次实际收付款(可能合并结清多笔借款)(0039)。

    场景:「张三 2025-01-01 借出 50+50+50,实际收到一笔 170 元(150 本金 + 20 利息)」。
    真实银行流水只有**一笔**,所以交易流水里也应该只有**一条**本金流水(150),
    而不是按 FIFO 拆成 3 条各 50。合并组记录这笔实际收付款的全貌:

      - amount:本次本金合计(150);
      - interest_amount:本次利息(20),>0 时并入还款流水金额并在备注注明;
      - tx_sync_id:合并后的唯一一条本金流水;
      - merged_from_sync_ids_json:被合并的各笔借款本金流水 syncId(JSON 数组),
        供交易详情「查看原单」回溯到原始借款单。

    借款本金结算仍按 loan_repayments 逐笔归集(每笔借款一行),两套账口径互不干扰。
    """

    __tablename__ = "loan_payment_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 'lend'(借出收回) / 'borrow'(借入偿还)
    direction: Mapped[str] = mapped_column(String(16))
    # 本次实际收付的本金合计(不含利息)。
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    # 本次实际收付的利息(>0 时并入还款流水金额,备注注明含利息)。
    interest_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    repaid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 合并本金流水的 syncId(一次实际收付款只生成一条本金流水)。
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    interest_tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 被合并的各笔借款本金流水 syncId(JSON 数组),供「查看原单」回溯。
    merged_from_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachment_file_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================================
# Fixed-term deposits (Web-first feature, NOT part of the mobile sync layer)
# ============================================================================
#
# 定期存款(Deposit)管理银行定期存单 / 大额存单 / 结构性存款,ledger-scoped,
# 跟 loans 一样走「服务端直管表 + REST CRUD + 借 snapshot_mutator 生成真实流水」
# 的模式:
#   - 存入 → 生成一笔 transfer 流水(活期账户 → 虚拟「定期存款」账户),
#     转账不进收支统计,总资产不变(活期 -X,定期存款虚拟账户 +X)。
#   - 支取 → 本金 transfer 转回活期 + 利息 income 流水(计入统计)。
#   - 周期结息(零存整取/按月按季)→ 单独结息记录 + income 流水。
# 「定期存款」虚拟账户总额由 Deposit 记录实时派生(见 routers/deposits.py
# /summary),跟应收款/应付款同一思路。


class Deposit(Base):
    """一张定期存单。ledger-scoped(跟 transaction 同层级)。

    - deposit_type: fixed(整存整取)/ installment(零存整取)/ cd(大额存单)/
      structured(结构性存款)。
    - term_months:存期月数(3/6/12/24/36/60 或自定义)。
    - annual_rate:年利率百分比(如 2.75 表示 2.75%)。
    - interest_override:手动覆盖的到期利息;None = 按公式算
      (本金 × 年利率 × 存期月数 / 12)。
    - maturity_at:到期日 = started_at + term_months 个月(秒级精度)。
    - on_maturity:withdraw(到期支取)/ auto_renew(自动转存)。
    - interest_settlement:maturity(到期一次性)/ monthly / quarterly,
      settlement_day 为结息指定日(1..28)。
    - status(读时派生,落库便于查询):holding(持有中)/ matured(已到期)/
      withdrawn(已支取)/ renewed(已转存)。终态(withdrawn/renewed)一旦写入
      不再派生;holding/matured 由到期日实时判定。
    - certificate_file_ids_json:存单照片 attachment file id 的 JSON 数组。
    - flow_tx_sync_id:存入划转流水(transfer);principal_return_tx_sync_id /
      interest_tx_sync_id:支取时生成的本金转回 / 利息收入流水。
    """

    __tablename__ = "deposits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    # fixed / installment / cd / structured
    deposit_type: Mapped[str] = mapped_column(String(32), default="fixed")
    principal: Mapped[float] = mapped_column(Float, default=0.0)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    term_months: Mapped[int] = mapped_column(Integer, default=12)
    # 年利率百分比,如 2.75 表示 2.75%
    annual_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 手动覆盖的到期利息(None = 按公式自动计算)
    interest_override: Mapped[float | None] = mapped_column(Float, nullable=True)
    maturity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # withdraw(到期支取) / auto_renew(自动转存)
    on_maturity: Mapped[str] = mapped_column(String(16), default="withdraw")
    # maturity(到期一次性) / monthly / quarterly
    interest_settlement: Mapped[str] = mapped_column(String(16), default="maturity")
    # 结息指定日(1..28),仅 monthly/quarterly 有意义
    settlement_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # holding / matured / withdrawn / renewed
    status: Mapped[str] = mapped_column(String(16), default="holding", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 存单照片 attachment file id 的 JSON 数组
    certificate_file_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联银行账户(活期)。存入划转 / 支取转回都经它。
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 存入划转流水(transfer:活期 → 虚拟定期存款账户)
    flow_tx_sync_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    # 支取:本金转回流水(transfer)
    principal_return_tx_sync_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    # 支取:利息收入流水(income,计入统计)
    interest_tx_sync_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 支取时实际拿到的利息(记录用)
    withdrawn_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 转存链:本存单转存出的新存单 / 来源存单
    renewed_to_deposit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    parent_deposit_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DepositInterestPayment(Base):
    """定期存款的周期结息记录(按月/按季指定日结息)。

    级联删除归属 deposit。每次结息生成一笔 income 流水(计入统计),
    tx_sync_id 用于编辑/删除时联动维护流水。
    """

    __tablename__ = "deposit_interest_payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    ledger_id: Mapped[str] = mapped_column(
        ForeignKey("ledgers.id", ondelete="CASCADE"), index=True
    )
    deposit_id: Mapped[str] = mapped_column(
        ForeignKey("deposits.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tx_sync_id: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavingPlan(Base):
    """储蓄罐 / 存款计划。ledger-scoped, Web-first。"""
    __tablename__ = "saving_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_amount: Mapped[float] = mapped_column(Float, default=0.0)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # free / fixed / wish
    plan_type: Mapped[str] = mapped_column(String(16), default="free")
    monthly_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    # active / completed / abandoned
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SavingContribution(Base):
    """存款记录。每次存入一笔。"""
    __tablename__ = "saving_contributions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("saving_plans.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联已有交易(可选):标记为储蓄时填入 / 扣款账户生成的支出交易
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 扣款账户快照(可选):选中则生成支出交易
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 收款账户快照(可选):选中则生成收入交易(例如把钱转入储蓄账户)
    receive_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    receive_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    receive_tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 交易级元数据(随存入生成的一笔/两笔交易共用)
    category_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    native_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    exclude_from_stats: Mapped[bool] = mapped_column(Boolean, default=False)
    exclude_from_budget: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReimbursementItem(Base):
    """报销项：一笔被标记为"需报销"的交易流水（多为支出）。

    Web-first, ledger-scoped。status=pending 显示在"待报销"；
    status=reimbursed 关联到某张报销单，显示在"已报"。
    """
    __tablename__ = "reimbursement_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    # 关联原始交易(writer 层生成的 tx sync_id)
    tx_sync_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 报销公司维度(0037)：公司档案快照，user 级公司档案可编辑/删除，历史记录保留快照。
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # pending / reimbursed
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reimbursement_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Reimbursement(Base):
    """报销单 / 报销流水：把多笔报销项合并报销后生成的一笔记录。"""
    __tablename__ = "reimbursements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Material Symbols ligature，可设置
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 收款账户（报销到账）
    receive_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    receive_account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 报销公司维度(0037)：合并报销时从报销项继承的公司快照。
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # 报销生成的收入交易（可选）
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Company(Base):
    """报销公司档案（0037）：用户级可编辑管理，报销项/报销单保存公司名快照。

    Web-first, user-scoped。公司不属于单个账本，跨账本复用；删除公司档案不影响
    已产生的报销项/报销单（它们保留 company_name 快照）。
    """
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Item(Base):
    """物品（0038）：用户拥有的物品档案，ledger-scoped。

    价格/购入时间/渠道/分类/存储位置为物品属性；关联账单有三种模式：
    - new: 新建物品时生成一笔支出交易（扣款账户选填）
    - existing: 关联一笔已有交易(tx_sync_id)
    - none: 不关联交易
    均值计算(average_mode)/目标成本(target_cost_mode)：none/daily_avg/per_day，
    为展示层的派生计算配置。
    """
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    purchase_channel: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # new / existing / none
    link_mode: Mapped[str] = mapped_column(String(16), default="none", index=True)
    # 关联的**全部**交易流水 syncId(JSON 数组,0040)。一件物品的购入可能由多笔
    # 账单组成(定金/尾款/多平台),所以 existing 模式支持关联多笔。
    # tx_sync_id 单值列保留:存第一个关联(向后兼容旧客户端与旧数据)。
    tx_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 新建关联账单时的账户快照
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # none / daily_avg / per_day
    average_mode: Mapped[str] = mapped_column(String(16), default="none")
    target_cost_mode: Mapped[str] = mapped_column(String(16), default="none")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ItemFlow(Base):
    """物品流水（0038）：某件物品产生的收益或支出。

    flow_type=income（如出租、顺风车带客）/ expense（如保养、维修、税、装修）。
    可选关联一笔交易：提供 account_id 时生成对应 income/expense 交易，
    或关联已有交易(tx_sync_id)；都不填则仅作记录。
    """
    __tablename__ = "item_flows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ledger_id: Mapped[str] = mapped_column(ForeignKey("ledgers.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    # income / expense
    flow_type: Mapped[str] = mapped_column(String(16), default="expense", index=True)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    category_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # new / existing / none
    link_mode: Mapped[str] = mapped_column(String(16), default="none", index=True)
    # 关联的**全部**交易流水 syncId(JSON 数组,0040);单笔时长度为 1。
    tx_sync_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tx_sync_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attachments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
