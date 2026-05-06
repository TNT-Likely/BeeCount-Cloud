"""2FA: User TOTP columns + recovery_codes table

Revision ID: 0005_2fa_totp
Revises: 0004_backup_tables
Create Date: 2026-05-06

User 表加 totp_secret_encrypted / totp_enabled / totp_enabled_at 三列;
新建 recovery_codes 表(sha256 hash 存,used_at 标记一次性消费)。

详见 .docs/2fa-design.md。
"""

import sqlalchemy as sa
from alembic import op


revision = "0005_2fa_totp"
down_revision = "0004_backup_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "totp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("totp_enabled_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("recovery_codes")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_enabled")
    op.drop_column("users", "totp_secret_encrypted")
