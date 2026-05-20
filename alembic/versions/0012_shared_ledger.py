"""shared ledger: ledger_members + ledger_invites + read_tx_projection.last_edited_by_user_id

Revision ID: 0012_shared_ledger
Revises: 0011_drop_legacy_ledger_snapshot
Create Date: 2026-05-16

参考 .docs/shared-ledger/02-database-changes.md。共享账本 Phase 1 schema:

- 新建 ledger_members PK=(ledger_id, user_id),role ∈ owner/editor
- 新建 ledger_invites,code 6 位明文 PK,used_at NULL 表未接受
- read_tx_projection 加 last_edited_by_user_id 列(created_by_user_id 已存在)
- 数据迁移:把所有现有 ledgers.user_id 写一行 (ledger_id, user_id, 'owner') 到 ledger_members

老 ledger.user_id 列保留(作为"原 Owner"冗余字段,Phase 1 不 drop)。
"""

import sqlalchemy as sa
from alembic import op


revision = "0012_shared_ledger"
down_revision = "0011_drop_legacy_ledger_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. ledger_members
    op.create_table(
        "ledger_members",
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "invited_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ledger_members_user_id", "ledger_members", ["user_id"]
    )
    op.create_index(
        "ix_ledger_members_ledger_id", "ledger_members", ["ledger_id"]
    )

    # 2. ledger_invites
    op.create_table(
        "ledger_invites",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column(
            "ledger_id",
            sa.String(36),
            sa.ForeignKey("ledgers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invited_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_role", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_invites_expires_at", "ledger_invites", ["expires_at"]
    )
    op.create_index(
        "ix_invites_ledger_id", "ledger_invites", ["ledger_id"]
    )

    # 3. read_tx_projection 加 last_edited_by_user_id
    # created_by_user_id 已在 0010 之前的 migration 里加过(见 models.py L445)
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.add_column(
            sa.Column("last_edited_by_user_id", sa.String(36), nullable=True)
        )

    # 4. 数据迁移:现有 ledger.user_id → ledger_members(owner)
    op.execute(
        """
        INSERT INTO ledger_members (ledger_id, user_id, role, joined_at)
        SELECT id, user_id, 'owner', COALESCE(created_at, CURRENT_TIMESTAMP)
        FROM ledgers
        """
    )

    # 5. 历史 tx 创建者填:用 ledger.user_id 兜底(老数据无法回溯真实创建者)
    # 仅填 NULL 的行(已有 created_by_user_id 的不动)
    op.execute(
        """
        UPDATE read_tx_projection
        SET created_by_user_id = (
            SELECT user_id FROM ledgers
            WHERE ledgers.id = read_tx_projection.ledger_id
        )
        WHERE created_by_user_id IS NULL
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("read_tx_projection") as batch:
        batch.drop_column("last_edited_by_user_id")

    op.drop_index("ix_invites_ledger_id", table_name="ledger_invites")
    op.drop_index("ix_invites_expires_at", table_name="ledger_invites")
    op.drop_table("ledger_invites")

    op.drop_index("ix_ledger_members_ledger_id", table_name="ledger_members")
    op.drop_index("ix_ledger_members_user_id", table_name="ledger_members")
    op.drop_table("ledger_members")
