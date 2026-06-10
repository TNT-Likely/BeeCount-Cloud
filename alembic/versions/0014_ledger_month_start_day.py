"""ledgers: month_start_day 列 — 自定义每月起始日(1-28)

Revision ID: 0014_ledger_month_start_day
Revises: 0013_user_category_parent_sync_id
Create Date: 2026-06-10

账本级「每月起始日」:统计/预算按 [当月N日, 次月N日) 聚合,1=自然月。
mobile 端对应 Drift 列 ledgers.month_start_day,sync payload key
`monthStartDay`。设计文档:BeeCount/.docs/period-start-date/design.md。
"""

import sqlalchemy as sa
from alembic import op


revision = "0014_ledger_month_start_day"
down_revision = "0013_user_category_parent_sync_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("ledgers") as batch_op:
        batch_op.add_column(
            sa.Column("month_start_day", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )


def downgrade() -> None:
    with op.batch_alter_table("ledgers") as batch_op:
        batch_op.drop_column("month_start_day")
