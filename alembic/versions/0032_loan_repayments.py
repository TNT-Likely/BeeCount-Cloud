"""add repayments_json to read_loan_projection

Revision ID: 0032_loan_repayments
Revises: 0031_member_icon_link
Create Date: 2026-10-28

登记还款(0032):借款支持「登记还款」—— 选定银行卡、记录本金(不进收支统计)
与利息(正常支出 / 收入),自动生成对应流水。还款明细以 JSON 文本列挂在借款
投影上,便于读端点序列化给前端展示与撤销。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032_loan_repayments"
down_revision = "0031_member_icon_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "read_loan_projection",
        sa.Column("repayments_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("read_loan_projection", "repayments_json")
