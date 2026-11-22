"""0031 — 定期存款(Deposit)+ 周期结息(DepositInterestPayment)。

存单:名称/类型/本金/存入时间(秒级)/存期月数/年利率/利息覆盖/到期日/
到期处理/结息方式/状态/备注/存单照片/关联账户 + 三条自动流水 syncId。
结息记录级联归属 deposit,每笔联动一条 income 流水。

Revision ID: 0031_deposits
Revises: 0030_party_profiles
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0031_deposits"
down_revision = "0030_party_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deposits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("deposit_type", sa.String(32), nullable=False, server_default="fixed"),
        sa.Column("principal", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("currency_code", sa.String(16), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("term_months", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("annual_rate", sa.Float(), nullable=True),
        sa.Column("interest_override", sa.Float(), nullable=True),
        sa.Column("maturity_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("on_maturity", sa.String(16), nullable=False, server_default="withdraw"),
        sa.Column("interest_settlement", sa.String(16), nullable=False, server_default="maturity"),
        sa.Column("settlement_day", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="holding", index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("certificate_file_ids_json", sa.Text(), nullable=True),
        sa.Column("account_id", sa.String(36), nullable=True, index=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("flow_tx_sync_id", sa.String(36), nullable=True, index=True),
        sa.Column("principal_return_tx_sync_id", sa.String(36), nullable=True, index=True),
        sa.Column("interest_tx_sync_id", sa.String(36), nullable=True, index=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_interest", sa.Float(), nullable=True),
        sa.Column("renewed_to_deposit_id", sa.String(36), nullable=True, index=True),
        sa.Column("parent_deposit_id", sa.String(36), nullable=True, index=True),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "deposit_interest_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ledger_id", sa.String(36), sa.ForeignKey("ledgers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("deposit_id", sa.String(36), sa.ForeignKey("deposits.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("account_id", sa.String(36), nullable=True, index=True),
        sa.Column("account_name", sa.String(128), nullable=True),
        sa.Column("tx_sync_id", sa.String(36), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("deposit_interest_payments")
    op.drop_table("deposits")
