"""Single-user-per-ledger access helpers.

Every ledger is owned by exactly one user (`Ledger.user_id`). There are no
shared ledgers and no role hierarchy; the legacy role constants below are kept
as module-level aliases only so existing callers that still import
``READABLE_ROLES`` / ``WRITABLE_ROLES`` don't break. New code should not use them.
"""

from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Ledger

# Legacy role aliases — retained for import compatibility only.
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
READABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER}
WRITABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR}
ACTIVE_MEMBER_STATUS = "active"


def get_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,  # noqa: ARG001 — back-compat, ignored
) -> tuple[Ledger, None] | None:
    """Return ``(ledger, None)`` iff the caller owns it, else None.

    The second tuple slot used to be a ``LedgerMember`` row; it is now always
    ``None``. Kept as a tuple so existing callers that destructure ``ledger,
    member = row`` keep working without touching every callsite.
    """
    ledger = db.scalar(
        select(Ledger).where(
            Ledger.external_id == ledger_external_id,
            Ledger.user_id == user_id,
        )
    )
    return (ledger, None) if ledger is not None else None


def require_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,  # noqa: ARG001 — back-compat, ignored
) -> tuple[Ledger, None]:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row


def list_accessible_ledgers(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,  # noqa: ARG001 — back-compat, ignored
) -> list[Ledger]:
    return list(
        db.scalars(
            select(Ledger)
            .where(Ledger.user_id == user_id)
            .order_by(Ledger.created_at.desc())
        ).all()
    )


# Legacy name — returns list of (Ledger, None) so older callers doing
# ``for ledger, _member in list_accessible_memberships(...)`` continue to work.
def list_accessible_memberships(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,  # noqa: ARG001 — back-compat, ignored
) -> list[tuple[Ledger, None]]:
    return [(ledger, None) for ledger in list_accessible_ledgers(db, user_id=user_id)]
