"""Add attachment_file_ids_json to loan_repayments (0029).

还款记录支持上传凭证附件(file id 数组)。旧行 NULL 等价于空数组。
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_loan_repayment_attachments"
down_revision = "0028_payment_splits"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loan_repayments",
        sa.Column("attachment_file_ids_json", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("loan_repayments", "attachment_file_ids_json")
