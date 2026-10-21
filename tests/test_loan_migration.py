"""实际迁移函数仅接续无历史、非正余额的贷款，并为其他设备追加用户事件。"""
import importlib.util
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from src.database import Base
from src.models import User, Ledger, UserAccountProjection, ReadTxProjection, SyncChange
from datetime import datetime, timezone


def test_upgrade_preserves_identity_amount_metadata_and_existing_history():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(id="u", email="migration@qa.example.com", password_hash="fixture"))
        db.add(Ledger(id="l", user_id="u", external_id="ledger"))
        for sid, amount, kind in [("no-history", -30000, "loan"), ("history", -50000, "loan"), ("positive", 100, "loan"), ("house", 800000, "real_estate")]:
            db.add(UserAccountProjection(user_id="u", sync_id=sid, name=sid, account_type=kind,
                currency="CNY", initial_balance=amount, hidden=True, note="retain"))
        db.add(ReadTxProjection(ledger_id="l", user_id="u", sync_id="old", tx_type="transfer", amount=1000,
            happened_at=datetime.now(timezone.utc), from_account_sync_id="bank", to_account_sync_id="history"))
        db.commit()
    path = Path(__file__).parents[1] / "alembic/versions/0021_loan_balance.py"
    spec = importlib.util.spec_from_file_location("loan_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE user_account_projection DROP COLUMN loan_balance_since"))
        connection.execute(text("ALTER TABLE user_account_projection DROP COLUMN loan_balance_version"))
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    with Session(engine) as db:
        rows = {row.sync_id: row for row in db.query(UserAccountProjection).all()}
        assert rows["no-history"].loan_balance_version == 1
        assert rows["no-history"].initial_balance == -30000
        assert rows["no-history"].loan_balance_since is not None
        assert rows["no-history"].hidden and rows["no-history"].note == "retain"
        for sid in ("history", "positive", "house"):
            assert rows[sid].loan_balance_version == 0
        assert rows["history"].initial_balance == -50000
        assert db.query(ReadTxProjection).count() == 1
        changes = db.query(SyncChange).all()
        assert len(changes) == 1
        assert changes[0].entity_sync_id == "no-history"
        assert changes[0].scope == "user" and changes[0].ledger_id is None
        assert changes[0].payload_json["loanBalanceVersion"] == 1
        assert changes[0].payload_json["hidden"] is True
