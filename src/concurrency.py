from __future__ import annotations

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session


def lock_sqlite_writer(db: Session) -> None:
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def lock_pg_sync_stream(connection: Connection) -> None:
    """Take the publication lock before assigning the first change_id in a transaction."""
    if connection.dialect.name != "postgresql":
        return
    transaction = connection.get_nested_transaction() or connection.get_transaction()
    # SQLAlchemy reuses pooled connections. A savepoint rollback also releases
    # locks acquired inside it, so memoize against the current transaction object.
    if transaction is not None and connection.info.get("sync_stream_lock") is transaction:
        return
    # Two-int advisory keys use a separate namespace from ledger UUID hashes.
    connection.execute(text("SELECT pg_advisory_xact_lock(1111704430, 1)"))
    connection.info["sync_stream_lock"] = transaction


def lock_sync_writer(db: Session) -> None:
    """Acquire the writer lock before reading versions or taking ledger/row locks."""
    if db.get_bind().dialect.name == "postgresql":
        lock_pg_sync_stream(db.connection())
    else:
        lock_sqlite_writer(db)


def lock_ledger_for_materialize(db: Session, ledger_id: str) -> None:
    """Serialize snapshot materialization per ledger.

    Holds a transaction-scoped advisory lock keyed on the ledger's UUID string.
    Lock is released automatically at COMMIT/ROLLBACK.

    Without this lock, two concurrent pushes for the same ledger can each read
    the latest snapshot, merge only their own changes, and write competing new
    snapshots — losing one side's individual changes.

    SQLite takes its database writer lock before reading the projection.
    """
    lock_sync_writer(db)
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": ledger_id},
    )
