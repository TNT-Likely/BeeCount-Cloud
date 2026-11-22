"""0039 — 合并还款：一次实际收付款覆盖多笔借款。

背景:一笔实际收付款(如「张三 2025-01-01 收到 170 元 = 150 合并本金 + 20 利息」)
可能同时结清同一联系人同一方向下的多笔借款。此前导入脚本按 FIFO 把这笔款拆成
N 条本金流水(50+50+50),导致交易流水里出现 3 条「收回借款」,与真实银行流水
(一笔 170)对不上。

本迁移引入「还款合并组」:
- loan_repayments 新增 group_id —— 同一次实际收付款拆到多笔借款的还款行共享该组 id;
  组内只有一行持有 tx_sync_id(合并后的唯一一条本金流水),其余行 tx_sync_id 为空。
- 新增 loan_payment_groups 表 —— 记录一次实际收付款的总额/账户/时间,
  以及 merged_from_sync_ids_json(被合并的各笔借款本金流水 syncId 列表),
  供交易详情「查看原单」回溯原始借款单。

Revision ID: 0039_loan_merged_repayment
Revises: 0038_items
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0039_loan_merged_repayment"
down_revision = "0038_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 还款行归属的合并组(外键指向 loan_payment_groups.id)。
    op.add_column("loan_repayments", sa.Column("group_id", sa.String(36), nullable=True))
    op.create_index("ix_loan_repayments_group_id", "loan_repayments", ["group_id"])

    op.create_table(
        "loan_payment_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.String(36), nullable=True),
        # lend = 借出(收回),borrow = 借入(偿还)
        sa.Column("direction", sa.String(16), nullable=False),
        # 本次实际收付总额(本金合计),不含利息。
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        # 本次实际收付的利息金额(>0 时单独生成一条「借款利息」流水)。
        sa.Column("interest_amount", sa.Float(), nullable=True),
        sa.Column("account_id", sa.String(36), nullable=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("repaid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        # 合并本金流水的 syncId(一条实际收付款只生成一条本金流水)。
        sa.Column("tx_sync_id", sa.String(36), nullable=True),
        sa.Column("interest_tx_sync_id", sa.String(36), nullable=True),
        # 被合并的各笔借款本金流水 syncId(JSON 数组),供「查看原单」回溯。
        sa.Column("merged_from_sync_ids_json", sa.Text(), nullable=True),
        sa.Column("attachment_file_ids_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_loan_payment_groups_user_id", "loan_payment_groups", ["user_id"])
    op.create_index("ix_loan_payment_groups_ledger_id", "loan_payment_groups", ["ledger_id"])
    op.create_index("ix_loan_payment_groups_contact_id", "loan_payment_groups", ["contact_id"])
    op.create_index("ix_loan_payment_groups_tx_sync_id", "loan_payment_groups", ["tx_sync_id"])
    op.create_index("ix_loan_payment_groups_interest_tx_sync_id", "loan_payment_groups", ["interest_tx_sync_id"])


def downgrade() -> None:
    op.drop_index("ix_loan_payment_groups_interest_tx_sync_id", table_name="loan_payment_groups")
    op.drop_index("ix_loan_payment_groups_tx_sync_id", table_name="loan_payment_groups")
    op.drop_index("ix_loan_payment_groups_contact_id", table_name="loan_payment_groups")
    op.drop_index("ix_loan_payment_groups_ledger_id", table_name="loan_payment_groups")
    op.drop_index("ix_loan_payment_groups_user_id", table_name="loan_payment_groups")
    op.drop_table("loan_payment_groups")
    op.drop_index("ix_loan_repayments_group_id", table_name="loan_repayments")
    op.drop_column("loan_repayments", "group_id")
