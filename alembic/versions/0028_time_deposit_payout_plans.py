"""add payout_plans_json to read_time_deposit_projection

Revision ID: 0028_time_deposit_payout_plans
Revises: 0027_account_alias_billing_in_current
Create Date: 2026-10-26

- payout_plans_json:定期存款的「到息计划」(可多笔)。JSON 数组文本,每项
  ``{"date":"YYYY-MM-DD","amount":123.0,"note":"..."}`` —— date 是到息日、
  amount 是该次的金额(自由金额,不区分利息/本息)。一个存款可挂多条,支持
  多次结息 / 多笔到期的场景。

  与退款腿 refund_legs_json 同款内嵌方式(不新增表):计划只是预期/提醒,
  不生成任何交易,也不参与余额;是否已到息由读端点按「今天」日期派生,
  与 expected_interest / days_remaining 的派生口径一致。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_time_deposit_payout_plans"
down_revision = "0027_account_alias_billing_in_current"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_time_deposit_projection",
        sa.Column("payout_plans_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_time_deposit_projection", "payout_plans_json")
