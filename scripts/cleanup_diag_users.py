#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass

from sqlalchemy import delete, func, select

from src.database import SessionLocal
from src.models import (
    AttachmentFile,
    AuditLog,
    BackupArtifact,
    BackupSnapshot,
    Device,
    Ledger,
    LedgerInvite,
    LedgerMember,
    RefreshToken,
    SyncChange,
    SyncCursor,
    SyncPushIdempotency,
    User,
    WebAccountProjection,
    WebCategoryProjection,
    WebLedgerProjection,
    WebTagProjection,
    WebTransactionProjection,
)


@dataclass(frozen=True)
class CleanupCounts:
    users: int
    ledgers: int


def _find_targets(session, email_like: str) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    users = session.execute(
        select(User.id, User.email).where(User.email.like(email_like)).order_by(User.created_at.asc())
    ).all()
    user_ids = [row[0] for row in users]
    ledgers = session.scalars(select(Ledger.id).where(Ledger.user_id.in_(user_ids))).all() if user_ids else []
    return user_ids, list(ledgers), [(row[0], row[1]) for row in users]


def _delete_where_in(session, model, column, ids: list[str]) -> int:
    if not ids:
        return 0
    result = session.execute(delete(model).where(column.in_(ids)))
    return int(result.rowcount or 0)


def cleanup_diag_users(*, email_like: str, apply: bool) -> CleanupCounts:
    session = SessionLocal()
    try:
        user_ids, ledger_ids, users = _find_targets(session, email_like)
        if not users:
            print(f"No users matched pattern: {email_like}")
            return CleanupCounts(users=0, ledgers=0)

        print("Matched users:")
        for user_id, email in users:
            print(f"  - {email} ({user_id})")
        print(f"Matched ledgers: {len(ledger_ids)}")

        if not apply:
            print("Dry-run only. Re-run with --apply to execute deletion.")
            return CleanupCounts(users=len(user_ids), ledgers=len(ledger_ids))

        # User scoped.
        _delete_where_in(session, SyncCursor, SyncCursor.user_id, user_ids)
        _delete_where_in(session, SyncPushIdempotency, SyncPushIdempotency.user_id, user_ids)
        _delete_where_in(session, RefreshToken, RefreshToken.user_id, user_ids)
        _delete_where_in(session, Device, Device.user_id, user_ids)

        # Membership and invites.
        if user_ids:
            session.execute(delete(LedgerMember).where(LedgerMember.user_id.in_(user_ids)))
            session.execute(delete(LedgerInvite).where(LedgerInvite.created_by_user_id.in_(user_ids)))

        # Ledger scoped data.
        if ledger_ids:
            session.execute(delete(LedgerMember).where(LedgerMember.ledger_id.in_(ledger_ids)))
            session.execute(delete(LedgerInvite).where(LedgerInvite.ledger_id.in_(ledger_ids)))
            session.execute(delete(WebTransactionProjection).where(WebTransactionProjection.ledger_id.in_(ledger_ids)))
            session.execute(delete(WebAccountProjection).where(WebAccountProjection.ledger_id.in_(ledger_ids)))
            session.execute(delete(WebCategoryProjection).where(WebCategoryProjection.ledger_id.in_(ledger_ids)))
            session.execute(delete(WebTagProjection).where(WebTagProjection.ledger_id.in_(ledger_ids)))
            session.execute(delete(WebLedgerProjection).where(WebLedgerProjection.ledger_id.in_(ledger_ids)))
            session.execute(delete(SyncChange).where(SyncChange.ledger_id.in_(ledger_ids)))
            session.execute(delete(BackupSnapshot).where(BackupSnapshot.ledger_id.in_(ledger_ids)))
            session.execute(delete(BackupArtifact).where(BackupArtifact.ledger_id.in_(ledger_ids)))
            session.execute(delete(AttachmentFile).where(AttachmentFile.ledger_id.in_(ledger_ids)))
            session.execute(delete(AuditLog).where(AuditLog.ledger_id.in_(ledger_ids)))

        # Remaining user-scoped rows that may reference shared ledgers.
        _delete_where_in(session, SyncChange, SyncChange.user_id, user_ids)
        _delete_where_in(session, BackupSnapshot, BackupSnapshot.user_id, user_ids)
        _delete_where_in(session, BackupArtifact, BackupArtifact.user_id, user_ids)
        _delete_where_in(session, AttachmentFile, AttachmentFile.user_id, user_ids)
        _delete_where_in(session, AuditLog, AuditLog.user_id, user_ids)

        if ledger_ids:
            _delete_where_in(session, Ledger, Ledger.id, ledger_ids)
        _delete_where_in(session, User, User.id, user_ids)

        session.commit()

        remaining = int(
            session.scalar(select(func.count()).select_from(User).where(User.email.like(email_like))) or 0
        )
        print(f"Cleanup finished. Remaining matched users: {remaining}")
        return CleanupCounts(users=len(user_ids), ledgers=len(ledger_ids))
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup diagnostic test users by email pattern.")
    parser.add_argument(
        "--email-like",
        default="diag_%@example.com",
        help="SQL LIKE pattern for target users (default: diag_%%@example.com).",
    )
    parser.add_argument("--apply", action="store_true", help="Execute deletion. Without this flag it is a dry-run.")
    args = parser.parse_args()

    cleanup_diag_users(email_like=args.email_like, apply=args.apply)


if __name__ == "__main__":
    main()

