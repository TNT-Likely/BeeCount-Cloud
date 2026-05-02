"""backup tables: remotes / schedules / schedule_remotes / runs / run_targets

Revision ID: 0004_backup_tables
Revises: 0003_account_extra_fields
Create Date: 2026-05-20

新增 5 张备份相关表,详见 .docs/backup-rclone-plan.md。

mobile sync 不涉及这些表 —— 备份是 server 单边能力,跟跨设备同步无关。
"""

import sqlalchemy as sa
from alembic import op


revision = "0004_backup_tables"
down_revision = "0003_account_extra_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_remotes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("backend_type", sa.String(32), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("config_summary", sa.JSON(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="uq_backup_remote_user_name"),
    )

    op.create_table(
        "backup_schedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("cron_expr", sa.String(64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column(
            "include_attachments", sa.Boolean(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "backup_schedule_remotes",
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("backup_schedules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "remote_id",
            sa.Integer(),
            sa.ForeignKey("backup_remotes.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "backup_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "schedule_id",
            sa.Integer(),
            sa.ForeignKey("backup_schedules.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'running'"), index=True),
        sa.Column("backup_filename", sa.String(128), nullable=True),
        sa.Column("bytes_total", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("log_text", sa.Text(), nullable=True),
    )

    op.create_table(
        "backup_run_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("backup_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "remote_id",
            sa.Integer(),
            sa.ForeignKey("backup_remotes.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bytes_transferred", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("backup_run_targets")
    op.drop_table("backup_runs")
    op.drop_table("backup_schedule_remotes")
    op.drop_table("backup_schedules")
    op.drop_table("backup_remotes")
