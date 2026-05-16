"""共享账本成员管理 endpoint。

4 个 endpoint:
  - GET    /ledgers/{ext}/members              (任意 member) 列出成员
  - PATCH  /ledgers/{ext}/members/{user_id}    (owner) 改成员角色(Phase 1 仅 owner ↔ editor 通过 transfer)
  - DELETE /ledgers/{ext}/members/{user_id}    (owner 踢任何人 / 自己退出) 删成员
  - POST   /ledgers/{ext}/transfer             (owner) 转让 owner

权限策略:
- Owner 可以管所有人(包括自己,通过 transfer 给别人)。
- 非 Owner 只能 DELETE 自己(走"退出"语义)。
- 最后一个 Owner 不能直接被踢 / 退出 — 必须先 transfer。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import (
    ROLE_EDITOR,
    ROLE_OWNER,
    require_accessible_ledger_by_external_id,
)
from ..models import LedgerMember, User, UserProfile
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

router = APIRouter()
logger = logging.getLogger(__name__)

_AUTH_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE)
_WRITE_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime | None) -> str | None:
    aware = _to_utc(value)
    return aware.isoformat() if aware is not None else None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class MemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: str
    joined_at: datetime
    invited_by_user_id: str | None
    is_self: bool

    @field_serializer("joined_at")
    def _ser_dt(self, v: datetime) -> str:
        return _utc_iso(v) or ""


class MemberRoleUpdateRequest(BaseModel):
    # Phase 1:只允许把 Editor 维持为 Editor(no-op safe)。owner 切换走 transfer
    # endpoint,viewer 远期再加。这里保留 schema 给后续 Phase 2 扩展。
    role: Literal["editor"] = Field(..., description="目标角色;Phase 1 仅 editor")


class TransferOwnershipRequest(BaseModel):
    new_owner_user_id: str = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hydrate_member(
    db: Session,
    *,
    member: LedgerMember,
    caller_user_id: str,
) -> MemberOut:
    user = db.scalar(select(User).where(User.id == member.user_id))
    display_name = db.scalar(
        select(UserProfile.display_name).where(UserProfile.user_id == member.user_id)
    )
    email = user.email if user is not None else ""
    return MemberOut(
        user_id=member.user_id,
        email=email,
        display_name=display_name,
        role=member.role,
        joined_at=member.joined_at,
        invited_by_user_id=member.invited_by,
        is_self=(member.user_id == caller_user_id),
    )


def _owner_count(db: Session, ledger_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(LedgerMember.user_id)).where(
                LedgerMember.ledger_id == ledger_id,
                LedgerMember.role == ROLE_OWNER,
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/ledgers/{ledger_external_id}/members",
    response_model=list[MemberOut],
)
def list_members(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    ledger, _caller_role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )
    rows = db.scalars(
        select(LedgerMember)
        .where(LedgerMember.ledger_id == ledger.id)
        .order_by(LedgerMember.joined_at.asc())
    ).all()
    return [_hydrate_member(db, member=r, caller_user_id=current_user.id) for r in rows]


@router.patch(
    "/ledgers/{ledger_external_id}/members/{user_id}",
    response_model=MemberOut,
)
async def update_member_role(
    ledger_external_id: str,
    user_id: str,
    req: MemberRoleUpdateRequest,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    ledger, _caller_role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles={ROLE_OWNER},
    )

    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Owner cannot change own role; use transfer endpoint",
        )

    target = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == user_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # Phase 1 仅允许 editor → editor;现实里这就是 no-op,但保留路径未来扩展。
    # 不允许把 editor 改成 owner(走 transfer 才行,保证账本永远恰好 1 个 owner)。
    if req.role != ROLE_EDITOR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported role: {req.role}",
        )
    target.role = req.role
    db.commit()

    logger.info(
        "member.role_update ledger=%s target=%s role=%s actor=%s",
        ledger_external_id, user_id, req.role, current_user.id,
    )

    from ..websocket_manager import broadcast_to_ledger
    await broadcast_to_ledger(
        db=db,
        ws_manager=request.app.state.ws_manager,
        ledger_id=ledger.id,
        payload={
            "type": "member_change",
            "ledgerId": ledger_external_id,
            "changeType": "role_changed",
            "userId": user_id,
            "newRole": req.role,
        },
    )

    return _hydrate_member(db, member=target, caller_user_id=current_user.id)


@router.delete(
    "/ledgers/{ledger_external_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    ledger_external_id: str,
    user_id: str,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """删除成员。

    - Owner 可踢任意 non-owner 成员
    - 任意成员可删自己(退出),但 Owner 退出前必须 transfer
    """
    # 注意:这里不限定 caller role,因为非 Owner 也可以"删除自己"。caller 必须是
    # ledger member,具体能不能执行根据 target 关系判断。
    ledger, caller_role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )

    target = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == user_id,
        )
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    is_self = user_id == current_user.id
    if not is_self and caller_role != ROLE_OWNER:
        # 非 Owner 试图踢别人 → 404 (不泄露存在性)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # 不允许踢 Owner / Owner 自己退(必须先 transfer)
    if target.role == ROLE_OWNER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove owner; transfer ownership first",
        )

    # 先记被踢者 — broadcast 包含他们才能让 client 端正确清理本地。
    removed_user_id = target.user_id
    db.delete(target)
    db.commit()

    logger.info(
        "member.remove ledger=%s target=%s self=%s actor=%s",
        ledger_external_id, user_id, is_self, current_user.id,
    )

    # 给"现有成员 + 被踢者"都推一份。被踢者已经不在 ledger_members 表里,要靠
    # extra_user_ids 强制带上,client 才能收到 removed 事件触发本地清理。
    from ..websocket_manager import broadcast_to_ledger
    await broadcast_to_ledger(
        db=db,
        ws_manager=request.app.state.ws_manager,
        ledger_id=ledger.id,
        payload={
            "type": "member_change",
            "ledgerId": ledger_external_id,
            "changeType": "removed",
            "userId": removed_user_id,
            "isSelf": is_self,
        },
        extra_user_ids=[removed_user_id],
    )


@router.post(
    "/ledgers/{ledger_external_id}/transfer",
    response_model=list[MemberOut],
)
async def transfer_ownership(
    ledger_external_id: str,
    req: TransferOwnershipRequest,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    """把 Owner 角色转给另一个已是成员的用户。

    单事务内:current owner → editor,target editor → owner。任何一步失败回滚。
    """
    ledger, _caller_role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles={ROLE_OWNER},
    )

    target_user_id = req.new_owner_user_id.strip()
    if not target_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="new_owner_user_id is required",
        )
    if target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Target is already the owner",
        )

    target_member = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == target_user_id,
        )
    )
    if target_member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user is not a member of this ledger",
        )

    # 当前 owner 行
    current_owner_member = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == current_user.id,
            LedgerMember.role == ROLE_OWNER,
        )
    )
    if current_owner_member is None:
        # 防御性 — 理论上 require_accessible_ledger 已经验证
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Owner row missing",
        )

    # 同事务双更新
    current_owner_member.role = ROLE_EDITOR
    target_member.role = ROLE_OWNER

    # Ledger.user_id 同步更新为新 Owner,保留"原 owner 字段"语义。这样未来如果
    # 有 code path 仍按 ledger.user_id 直接查(虽然 Sprint 1 已扫光),也能拿到
    # 当前真实 Owner。
    ledger.user_id = target_user_id

    db.commit()

    # 防御性 sanity:转让后恰好 1 个 Owner
    if _owner_count(db, ledger.id) != 1:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Invariant violated: ledger must have exactly one owner",
        )

    logger.info(
        "member.transfer ledger=%s from=%s to=%s",
        ledger_external_id, current_user.id, target_user_id,
    )

    # 双 role_changed 事件让 client 即时刷新 UI(两端 myRole 都翻转)
    from ..websocket_manager import broadcast_to_ledger
    for changed_uid, new_role in (
        (target_user_id, ROLE_OWNER),
        (current_user.id, ROLE_EDITOR),
    ):
        await broadcast_to_ledger(
            db=db,
            ws_manager=request.app.state.ws_manager,
            ledger_id=ledger.id,
            payload={
                "type": "member_change",
                "ledgerId": ledger_external_id,
                "changeType": "role_changed",
                "userId": changed_uid,
                "newRole": new_role,
            },
        )

    # 返回更新后的成员列表方便 UI 一次性刷新
    rows = db.scalars(
        select(LedgerMember)
        .where(LedgerMember.ledger_id == ledger.id)
        .order_by(LedgerMember.joined_at.asc())
    ).all()
    return [_hydrate_member(db, member=r, caller_user_id=current_user.id) for r in rows]
