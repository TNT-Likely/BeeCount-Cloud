"""贷款本金流水版本与接续日期。旧贷款默认保持快照，核对后升级。"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
import json

revision = "0021_loan_balance"
down_revision = "0020_category_parent_links"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_account_projection", sa.Column("loan_balance_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("user_account_projection", sa.Column("loan_balance_since", sa.DateTime(timezone=True), nullable=True))
    from src.models import UserAccountProjection, ReadTxProjection, SyncChange
    connection = op.get_bind()
    accounts, txs, changes = UserAccountProjection.__table__, ReadTxProjection.__table__, SyncChange.__table__
    exists = sa.select(txs.c.sync_id).where(txs.c.user_id == accounts.c.user_id, sa.or_(
        txs.c.account_sync_id == accounts.c.sync_id, txs.c.from_account_sync_id == accounts.c.sync_id,
        txs.c.to_account_sync_id == accounts.c.sync_id, txs.c.account_name == accounts.c.name,
        txs.c.from_account_name == accounts.c.name, txs.c.to_account_name == accounts.c.name)).exists()
    now = datetime.now(timezone.utc)
    since = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    for row in connection.execute(sa.select(accounts).where(accounts.c.account_type == 'loan',
            accounts.c.loan_balance_version == 0, accounts.c.initial_balance <= 0, ~exists)).mappings().all():
        payload = dict(syncId=row['sync_id'], name=row['name'], type='loan', currency=row['currency'],
            initialBalance=row['initial_balance'], hidden=bool(row['hidden']), loanBalanceVersion=1,
            loanBalanceSince=since.isoformat())
        for key, column in [('note', 'note'), ('creditLimit', 'credit_limit'), ('billingDay', 'billing_day'),
                ('paymentDueDay', 'payment_due_day'), ('bankName', 'bank_name'), ('cardLastFour', 'card_last_four')]:
            if row[column] is not None: payload[key] = row[column]
        result = connection.execute(changes.insert().values(user_id=row['user_id'], ledger_id=None,
            scope='user', entity_type='account', entity_sync_id=row['sync_id'], action='upsert',
            payload_json=payload, updated_at=now, updated_by_device_id='loan-migration-v1'))
        connection.execute(accounts.update().where(accounts.c.user_id == row['user_id'],
            accounts.c.sync_id == row['sync_id']).values(loan_balance_version=1,
            loan_balance_since=since, source_change_id=result.inserted_primary_key[0]))



def downgrade():
    op.drop_column("user_account_projection", "loan_balance_since")
    op.drop_column("user_account_projection", "loan_balance_version")
