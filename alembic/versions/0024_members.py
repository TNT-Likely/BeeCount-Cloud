"""账本内自定义成员 + 交易成员分摊(0024)

1) 新建 read_member_projection:账本自己维护的「人」名单(爸爸/妈妈/我),
   不要求登录账号;用于给支出账单做**人头分摊**。
   注意跟 LedgerMember(共享账本的登录用户成员)是两套东西。

2) read_tx_projection 加 member_shares_json:支出按成员拆分的份额
   [{"memberId","memberName","ratio","amount"}, ...]。
   跟 payment_legs_json 的关键差别:这是按**人**拆,不影响账户余额,
   只服务于成员维度统计;NULL = 未分摊(统计时按记账人归属)。

Revision ID: 0024_members
Revises: 0023_loans_reimbursements
Create Date: 2026-10-25
"""

import sqlalchemy as sa
from alembic import op


revision = "0024_members"
down_revision = "0023_loans_reimbursements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "read_member_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_member_projection_user_id",
        "read_member_projection",
        ["user_id"],
    )
    # 成员名点选 / 去重展示按 (ledger_id, name) 走。
    op.create_index(
        "ix_read_member_ledger_name",
        "read_member_projection",
        ["ledger_id", "name"],
    )

    op.add_column(
        "read_tx_projection",
        sa.Column("member_shares_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_tx_projection", "member_shares_json")
    op.drop_index("ix_read_member_ledger_name", table_name="read_member_projection")
    op.drop_index(
        "ix_read_member_projection_user_id", table_name="read_member_projection"
    )
    op.drop_table("read_member_projection")
