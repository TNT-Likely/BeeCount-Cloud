"""repair runtime tables missing from legacy backups

Revision ID: 0014_repair_runtime_tables_from_legacy_backups
Revises: 0013_user_category_parent_sync_id
Create Date: 2026-05-28

早期云备份为了减小体积,曾经把 refresh_tokens / audit_logs 等运行时表
DROP 掉。恢复这种历史备份后 alembic_version 仍然指向最新版本,后续启动不会
重跑 0001/0004/0008,登录写 refresh_tokens 时就会撞 "no such table"。

本迁移只在表缺失时重建空 schema,不改已有数据。
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_repair_runtime_tables_from_legacy_backups"
down_revision = "0013_user_category_parent_sync_id"
branch_labels = None
depends_on = None


def _existing_tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _create_refresh_tokens() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("device_id", sa.String(36), nullable=True),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_device_id", "refresh_tokens", ["device_id"])
    op.create_index(
        "ix_refresh_tokens_token_hash",
        "refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])


def _create_sync_push_idempotency() -> None:
    op.create_table(
        "sync_push_idempotency",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(128), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "device_id",
            "idempotency_key",
            name="uq_sync_push_idempotency",
        ),
    )
    op.create_index(
        "ix_sync_push_idempotency_device_id",
        "sync_push_idempotency",
        ["device_id"],
    )
    op.create_index(
        "ix_sync_push_idempotency_expires_at",
        "sync_push_idempotency",
        ["expires_at"],
    )
    op.create_index(
        "ix_sync_push_idempotency_idempotency_key",
        "sync_push_idempotency",
        ["idempotency_key"],
    )
    op.create_index(
        "ix_sync_push_idempotency_user_id",
        "sync_push_idempotency",
        ["user_id"],
    )


def _create_audit_logs() -> None:
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("user_id", sa.String(36), nullable=True),
        sa.Column("ledger_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_ledger_id", "audit_logs", ["ledger_id"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])


def _create_backup_runs() -> None:
    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column("backup_filename", sa.String(128), nullable=True),
        sa.Column("bytes_total", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("log_text", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["schedule_id"], ["backup_schedules.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_runs_schedule_id", "backup_runs", ["schedule_id"])
    op.create_index("ix_backup_runs_status", "backup_runs", ["status"])
    op.create_index("ix_backup_runs_user_id", "backup_runs", ["user_id"])


def _create_backup_run_targets() -> None:
    op.create_table(
        "backup_run_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bytes_transferred", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["remote_id"], ["backup_remotes.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["backup_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_run_targets_remote_id", "backup_run_targets", ["remote_id"])
    op.create_index("ix_backup_run_targets_run_id", "backup_run_targets", ["run_id"])


def _create_mcp_call_logs() -> None:
    op.create_table(
        "mcp_call_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("pat_id", sa.String(36), nullable=True),
        sa.Column("pat_prefix", sa.String(32), nullable=True),
        sa.Column("pat_name", sa.String(128), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("args_summary", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=True),
        sa.Column(
            "called_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["pat_id"], ["personal_access_tokens.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mcp_call_logs_called_at", "mcp_call_logs", ["called_at"])
    op.create_index("ix_mcp_call_logs_pat_id", "mcp_call_logs", ["pat_id"])
    op.create_index("ix_mcp_call_logs_status", "mcp_call_logs", ["status"])
    op.create_index("ix_mcp_call_logs_tool_name", "mcp_call_logs", ["tool_name"])
    op.create_index("ix_mcp_call_logs_user_id", "mcp_call_logs", ["user_id"])
    op.create_index(
        "ix_mcp_call_user_time",
        "mcp_call_logs",
        ["user_id", sa.text("called_at DESC")],
    )


def upgrade() -> None:
    tables = _existing_tables()
    if "refresh_tokens" not in tables:
        _create_refresh_tokens()
    if "sync_push_idempotency" not in tables:
        _create_sync_push_idempotency()
    if "audit_logs" not in tables:
        _create_audit_logs()
    if "backup_runs" not in tables:
        _create_backup_runs()
    if "backup_run_targets" not in tables:
        _create_backup_run_targets()
    if "mcp_call_logs" not in tables:
        _create_mcp_call_logs()


def downgrade() -> None:
    # 这是兼容性修复迁移:表可能来自旧迁移,也可能由本迁移补建。降级时无法
    # 区分来源,为避免误删运行中数据,保持 no-op。
    pass
