"""extend read_account_projection with mobile-side extra fields

Revision ID: 0003_account_extra_fields
Revises: 0002_backfill_category_icons
Create Date: 2026-05-01

mobile lib/data/db.dart 的 Account 表 schema 比 server side 的
read_account_projection 多 6 个字段:note / creditLimit / billingDay /
paymentDueDay / bankName / cardLastFour。mobile sync_engine 一直在 push 这些
字段(sync_engine.dart 1483-1488 已经在 payload 里),server 之前直接丢弃。

为了让 web 端编辑账户也能完整保存(对齐 app 编辑页),给 projection 表加上
这些列,projection.upsert_account 同步落库,read endpoint 把它们 round-trip
出来。

老 snapshot 里没有这些 key 没事 —— payload.get(key) 返回 None,projection 写
NULL,read 端 None 序列化为 JSON null。新 web/mobile 写入时才会有真实值。
"""
import sqlalchemy as sa
from alembic import op


revision = '0003_account_extra_fields'
down_revision = '0002_backfill_category_icons'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("read_account_projection") as batch_op:
        batch_op.add_column(sa.Column("note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("credit_limit", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("billing_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("payment_due_day", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("bank_name", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("card_last_four", sa.String(8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("read_account_projection") as batch_op:
        batch_op.drop_column("card_last_four")
        batch_op.drop_column("bank_name")
        batch_op.drop_column("payment_due_day")
        batch_op.drop_column("billing_day")
        batch_op.drop_column("credit_limit")
        batch_op.drop_column("note")
