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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    changes: Mapped[list["SyncChange"]] = relationship(back_populates="ledger")




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
    tx_type: Mapped[str] = mapped_column(String(16))
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
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


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
    source_change_id: Mapped[int] = mapped_column(BigInteger, default=0)


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
