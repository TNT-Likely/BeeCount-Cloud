"""Ledger access helpers — Phase 1 shared-ledger 之后从 LedgerMember 表读权限。

每个 ledger 通过 ``LedgerMember`` 行授权,role ∈ {owner, editor}。原 ``Ledger.user_id``
保留为冗余字段(标识原始 Owner / 兜底),但实际权限判定全部走 ``LedgerMember``。

API 兼容性:历史 caller 形如 ``ledger, _ = get_accessible_ledger_by_external_id(...)``
现在 ``_`` 收到的是 ``role`` 字符串(原本是 None)。caller 普遍 ignore,无破坏。
"""

from collections.abc import Iterable
from typing import TypeAlias

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .metrics import metrics
from .models import Ledger, LedgerMember

# 角色 enum-like 字符串(application 层校验,无 server enum)
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"  # Phase 1 不实现,占位以备远期

READABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER}
WRITABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR}
ACTIVE_MEMBER_STATUS = "active"  # 历史 alias,LedgerMember 表不存 status


LedgerRow: TypeAlias = tuple[Ledger, str]
"""``(ledger, role)``。role 是当前 caller 在该账本的角色。"""


def get_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,
) -> LedgerRow | None:
    """Return ``(ledger, role)`` if caller is a member; honor ``roles`` filter.

    ``roles`` 提供时,caller 角色必须在集合内(用于写路径限定 owner/editor)。
    不在集合内 → 返 None(404),不返 403,避免泄露账本存在性。
    """
    q = (
        select(Ledger, LedgerMember.role)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(
            Ledger.external_id == ledger_external_id,
            LedgerMember.user_id == user_id,
        )
    )
    if roles:
        q = q.where(LedgerMember.role.in_(roles))
    row = db.execute(q).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


def require_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,
) -> LedgerRow:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=roles,
    )
    if row is None:
        # Sprint 5.5 metric:区分"账本不存在"vs"非 member / 角色不足"两种情况。
        # 仅当账本存在但 caller 不在 LedgerMember(或 role 不达标)才记 denied —
        # 反映真实的"被拒"事件,纯不存在的 404 不计入。
        exists = db.scalar(
            select(Ledger.id).where(Ledger.external_id == ledger_external_id)
        )
        if exists is not None:
            reason = "role" if roles else "not_member"
            metrics.inc_labeled(
                "beecount_shared_ledger_access_denied_total",
                {"reason": reason},
            )
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row


def list_accessible_ledgers(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> list[Ledger]:
    """List ledgers the user can access (via LedgerMember)."""
    q = (
        select(Ledger)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(LedgerMember.user_id == user_id)
        .order_by(Ledger.created_at.desc())
    )
    role_set = set(roles) if roles else None
    if role_set:
        q = q.where(LedgerMember.role.in_(role_set))
    return list(db.scalars(q).all())


def list_accessible_memberships(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> list[LedgerRow]:
    """List (ledger, role) tuples for ledgers the user can access."""
    q = (
        select(Ledger, LedgerMember.role)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(LedgerMember.user_id == user_id)
        .order_by(Ledger.created_at.desc())
    )
    role_set = set(roles) if roles else None
    if role_set:
        q = q.where(LedgerMember.role.in_(role_set))
    return [(row[0], row[1]) for row in db.execute(q).all()]


def get_accessible_ledger_ids(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> Select[tuple[str]]:
    """Return a subquery selecting ledger ids accessible to ``user_id``.

    用于 ``Tx.ledger_id.in_(get_accessible_ledger_ids(db, user_id=...))``,
    避免拼大 IN list。
    """
    q = select(LedgerMember.ledger_id).where(LedgerMember.user_id == user_id)
    role_set = set(roles) if roles else None
    if role_set:
        q = q.where(LedgerMember.role.in_(role_set))
    return q


def get_member_role(
    db: Session, *, user_id: str, ledger_id: str
) -> str | None:
    """单纯查角色。返 ``None`` 表示非成员。"""
    return db.scalar(
        select(LedgerMember.role).where(
            LedgerMember.ledger_id == ledger_id,
            LedgerMember.user_id == user_id,
        )
    )


def list_ledger_members(
    db: Session, *, ledger_id: int
) -> list[tuple[str, str]]:
    """``[(user_id, role), ...]``。用于 WS fan-out / 权限审计。

    ``ledger_id`` 是 ``Ledger.id`` (INT 主键),不是 ``external_id`` 字符串。
    """
    return [
        (row.user_id, row.role)
        for row in db.execute(
            select(LedgerMember.user_id, LedgerMember.role).where(
                LedgerMember.ledger_id == ledger_id
            )
        ).all()
    ]


def count_ledger_members(db: Session, *, ledger_id: int) -> int:
    """成员数(invite accept 时检查上限用)。"""
    from sqlalchemy import func

    return db.scalar(
        select(func.count()).select_from(LedgerMember).where(
            LedgerMember.ledger_id == ledger_id
        )
    ) or 0
