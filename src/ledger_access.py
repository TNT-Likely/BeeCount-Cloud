"""Shared-ledger access helpers — backed by `ledger_members` table.

历史背景:本模块原本是 single-owner stub(只查 `Ledger.user_id == user_id`),
返回 `(ledger, None)` tuple 保持接口形状。Phase 1 共享账本上线后改为真正按
`ledger_members` 表查询;tuple 第二位返回 caller 的 role 字符串(`owner` /
`editor`)。绝大多数 caller 用 `ledger, _ = row` 解包丢弃 role,无需改动。

需要 role 的 caller(write 路径角色校验、tx 末尾"谁记的"等)可以直接拿。
"""

from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from .models import Ledger, LedgerMember

# Role 常量 — application 层使用,DB 不加 enum
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"  # 远期开放;Phase 1 不会出现在 ledger_members.role 里

READABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR, ROLE_VIEWER}
WRITABLE_ROLES = {ROLE_OWNER, ROLE_EDITOR}
ACTIVE_MEMBER_STATUS = "active"  # legacy alias,无实际状态字段


def get_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,
) -> tuple[Ledger, str] | None:
    """返回 ``(ledger, role)`` 当 user 能访问该账本 + 角色匹配,否则 None。

    - ``roles`` 可选:只接受角色集合内的成员(write 路径常用 ``{'owner','editor'}``)。
      None 表示不限制,任何 member 都能拿到。
    - 注意:`Ledger.external_id` 是 (user_id, external_id) 复合唯一(legacy 设计,
      非全局唯一)。查询条件用 ``Ledger.user_id == ledgerOwner`` + JOIN members 隐含。
      为兼容老 client 把 external_id 复用在多用户之间的情况,我们通过 join 而非
      Ledger.user_id 过滤 — caller 想访问哪个账本,由 (caller_user_id,
      external_id) 两个值唯一决定:在该 caller 的 membership 范围里找 external_id
      匹配的 ledger。
    """
    stmt = (
        select(Ledger, LedgerMember.role)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(
            Ledger.external_id == ledger_external_id,
            LedgerMember.user_id == user_id,
        )
    )
    if roles:
        stmt = stmt.where(LedgerMember.role.in_(roles))
    row = db.execute(stmt).first()
    if row is None:
        return None
    ledger, role = row
    return (ledger, role)


def require_accessible_ledger_by_external_id(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str] | None = None,
) -> tuple[Ledger, str]:
    """同 get_*,无访问权时抛 404(不抛 403,避免泄露账本存在性)。"""
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=roles,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row


def list_accessible_ledgers(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> list[Ledger]:
    """返回 user 能访问的所有 ledger,按 created_at desc 排序。"""
    stmt = (
        select(Ledger)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(LedgerMember.user_id == user_id)
    )
    if roles:
        stmt = stmt.where(LedgerMember.role.in_(set(roles)))
    stmt = stmt.order_by(Ledger.created_at.desc())
    return list(db.scalars(stmt).all())


def list_accessible_memberships(
    db: Session,
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> list[tuple[Ledger, str]]:
    """返回 [(ledger, role), ...] 给需要 role 的 caller。"""
    stmt = (
        select(Ledger, LedgerMember.role)
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(LedgerMember.user_id == user_id)
    )
    if roles:
        stmt = stmt.where(LedgerMember.role.in_(set(roles)))
    stmt = stmt.order_by(Ledger.created_at.desc())
    return [(ledger, role) for ledger, role in db.execute(stmt).all()]


def accessible_ledger_ids_subquery(
    *,
    user_id: str,
    roles: Iterable[str] | None = None,
) -> Select[tuple[str]]:
    """构造一个返回 ledger_id 的 subquery,用于 ``Tx.ledger_id.in_(...)`` 这种过滤。

    使用场景:sync pull 等需要批量过滤,不能每条都查 ledger_members。
    """
    stmt = select(LedgerMember.ledger_id).where(LedgerMember.user_id == user_id)
    if roles:
        stmt = stmt.where(LedgerMember.role.in_(set(roles)))
    return stmt


def get_member_role(
    db: Session, *, user_id: str, ledger_id: str
) -> str | None:
    """直接查角色;给 sync push 一类按 internal ledger_id (uuid) 校验的场景用。"""
    return db.scalar(
        select(LedgerMember.role).where(
            LedgerMember.ledger_id == ledger_id,
            LedgerMember.user_id == user_id,
        )
    )


def list_ledger_members(db: Session, *, ledger_id: str) -> list[LedgerMember]:
    """列账本所有成员;给 WS broadcast / 成员管理 endpoint 用。"""
    return list(
        db.scalars(
            select(LedgerMember).where(LedgerMember.ledger_id == ledger_id)
        ).all()
    )


def list_ledger_member_user_ids(db: Session, *, ledger_id: str) -> list[str]:
    """轻量版:只返 user_id 列表给 WS fan-out。"""
    return list(
        db.scalars(
            select(LedgerMember.user_id).where(LedgerMember.ledger_id == ledger_id)
        ).all()
    )


def ensure_owner_member(db: Session, *, ledger: Ledger) -> LedgerMember:
    """Idempotent:确保 ledger.user_id 在 ledger_members 表里有 owner 行。

    每个 ledger 创建路径都要调一次 — `write/ledgers.py` POST 和 `sync/push.py`
    隐式新 ledger 都依赖。重复调用安全(找到既有 owner 行直接返回)。
    """
    existing = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == ledger.user_id,
        )
    )
    if existing is not None:
        return existing
    member = LedgerMember(
        ledger_id=ledger.id,
        user_id=ledger.user_id,
        role=ROLE_OWNER,
    )
    db.add(member)
    db.flush()
    return member
