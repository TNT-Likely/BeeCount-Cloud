"""0036 — 储蓄罐存入:可选扣款账户/收款账户 + 归档状态。

扣款账户(可选)生成支出交易,收款账户(可选)生成收入交易,都不选则不产生流水。
SavingContribution 增加账户快照、收款交易 id、交易元数据字段。
SavingPlan.status 新增 'archived'(归档)。

Revision ID: 0036_saving_contrib_accounts
Revises: 0035_reimbursements
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0036_saving_contrib_accounts"
down_revision = "0035_reimbursements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("saving_contributions") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("account_name", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("receive_account_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("receive_account_name", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("receive_tx_sync_id", sa.String(36), nullable=True))
        batch_op.add_column(sa.Column("category_name", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("currency_code", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("native_amount", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("attachments_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("exclude_from_stats", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("exclude_from_budget", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_index("ix_saving_contributions_account_id", ["account_id"])
        batch_op.create_index("ix_saving_contributions_receive_account_id", ["receive_account_id"])
        batch_op.create_index("ix_saving_contributions_receive_tx_sync_id", ["receive_tx_sync_id"])


def downgrade() -> None:
    with op.batch_alter_table("saving_contributions") as batch_op:
        batch_op.drop_index("ix_saving_contributions_receive_tx_sync_id")
        batch_op.drop_index("ix_saving_contributions_receive_account_id")
        batch_op.drop_index("ix_saving_contributions_account_id")
        batch_op.drop_column("exclude_from_budget")
        batch_op.drop_column("exclude_from_stats")
        batch_op.drop_column("attachments_json")
        batch_op.drop_column("native_amount")
        batch_op.drop_column("currency_code")
        batch_op.drop_column("category_name")
        batch_op.drop_column("receive_tx_sync_id")
        batch_op.drop_column("receive_account_name")
        batch_op.drop_column("receive_account_id")
        batch_op.drop_column("account_name")
        batch_op.drop_column("account_id")
