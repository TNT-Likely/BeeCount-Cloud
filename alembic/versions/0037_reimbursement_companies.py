"""0037 — 报销公司维度：公司档案 + 报销项/报销单公司快照。

新增 companies（用户级公司档案，可编辑管理）；
reimbursement_items / reimbursements 增加 company_id / company_name 快照字段。
勾选报销时公司必填，历史记录保留公司名快照（删公司不影响历史）。

Revision ID: 0037_reimbursement_companies
Revises: 0036_saving_contrib_accounts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0037_reimbursement_companies"
down_revision = "0036_saving_contrib_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companies_user_id", "companies", ["user_id"])

    for table in ("reimbursement_items", "reimbursements"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("company_id", sa.String(36), nullable=True))
            batch_op.add_column(sa.Column("company_name", sa.String(128), nullable=True))
            batch_op.create_index(f"ix_{table}_company_id", ["company_id"])


def downgrade() -> None:
    for table in ("reimbursement_items", "reimbursements"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"ix_{table}_company_id")
            batch_op.drop_column("company_name")
            batch_op.drop_column("company_id")
    op.drop_index("ix_companies_user_id", table_name="companies")
    op.drop_table("companies")
