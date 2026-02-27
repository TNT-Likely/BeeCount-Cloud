from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Ledger, LedgerMember

ACTIVE_MEMBER_STATUS = "active"
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

READABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER}
WRITABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR}


def get_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,
) -> tuple[Ledger, LedgerMember] | None:
    query = (
        select(Ledger, LedgerMember)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(
            Ledger.external_id == ledger_external_id,
            LedgerMember.user_id == user_id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
        .limit(1)
    )
    if roles:
        query = query.where(LedgerMember.role.in_(roles))
    row = db.execute(query).first()
    if not row:
        owner_ledger = db.scalar(
            select(Ledger).where(Ledger.external_id == ledger_external_id, Ledger.user_id == user_id)
        )
        if owner_ledger is not None:
            member = LedgerMember(
                ledger_id=owner_ledger.id,
                user_id=user_id,
                role=ROLE_OWNER,
                status=ACTIVE_MEMBER_STATUS,
            )
            db.add(member)
            db.flush()
            return owner_ledger, member
        return None
    return row[0], row[1]


def require_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,
) -> tuple[Ledger, LedgerMember]:
    out = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=roles,
    )
    if out is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    return out


def list_accessible_memberships(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> list[tuple[Ledger, LedgerMember]]:
    query = (
        select(Ledger, LedgerMember)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(
            LedgerMember.user_id == user_id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
    )
    role_list = list(roles) if roles else []
    if role_list:
        query = query.where(LedgerMember.role.in_(role_list))
    rows = db.execute(query.order_by(Ledger.created_at.desc())).all()
    return [(row[0], row[1]) for row in rows]
