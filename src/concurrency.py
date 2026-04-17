from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_ledger_for_materialize(db: Session, ledger_id: str) -> None:
    """Serialize snapshot materialization per ledger.

    Holds a transaction-scoped advisory lock keyed on the ledger's UUID string.
    Lock is released automatically at COMMIT/ROLLBACK.

    Without this lock, two concurrent pushes for the same ledger can each read
    the latest snapshot, merge only their own changes, and write competing new
    snapshots — losing one side's individual changes.

    SQLite (dev/test) is a no-op: it serializes writes at the file level.
    """
    dialect_name = db.bind.dialect.name if db.bind is not None else ""
    if dialect_name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": ledger_id},
    )
