from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import (
    ACTIVE_MEMBER_STATUS,
    READABLE_ROLES,
    ROLE_OWNER,
    get_accessible_ledger_by_external_id,
)
from ..models import AuditLog, Ledger, LedgerInvite, LedgerMember, User, UserProfile
from ..schemas import (
    LedgerMemberOut,
    ShareInviteCreateRequest,
    ShareInviteCreateResponse,
    ShareInviteListItem,
    ShareInviteRevokeRequest,
    ShareInviteRevokeResponse,
    ShareJoinRequest,
    ShareJoinResponse,
    ShareLeaveRequest,
    ShareLeaveResponse,
    ShareMemberAddRequest,
    ShareMemberAddResponse,
    ShareMemberRemoveRequest,
    ShareMemberRemoveResponse,
    ShareMemberRoleRequest,
    ShareMemberRoleResponse,
)
from ..security import SCOPE_APP_WRITE, SCOPE_OPS_WRITE, SCOPE_WEB_WRITE, hash_invite_code

router = APIRouter()
settings = get_settings()
_SHARE_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE)


def _avatar_url(*, user_id: str, avatar_version: int | None = None) -> str:
    base = f"{settings.api_prefix}/profile/avatar/{user_id}"
    if avatar_version is None:
        return base
    return f"{base}?v={avatar_version}"


def _require_owner_membership(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
) -> tuple[Ledger, LedgerMember]:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=READABLE_ROLES,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger, member = row
    if member.role != ROLE_OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Share role forbidden")
    return row


def _require_manage_ledger(
    db: Session,
    *,
    current_user: User,
    ledger_external_id: str,
) -> Ledger:
    if current_user.is_admin:
        ledger = db.scalar(
            select(Ledger).where(Ledger.external_id == ledger_external_id).limit(1)
        )
        if ledger is None:
            raise HTTPException(status_code=404, detail="Ledger not found")
        return ledger
    ledger, _ = _require_owner_membership(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )
    return ledger


def _require_readable_ledger(
    db: Session,
    *,
    current_user: User,
    ledger_external_id: str,
) -> Ledger:
    if current_user.is_admin:
        ledger = db.scalar(
            select(Ledger).where(Ledger.external_id == ledger_external_id).limit(1)
        )
        if ledger is None:
            raise HTTPException(status_code=404, detail="Ledger not found")
        return ledger
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles=READABLE_ROLES,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger, _ = row
    return ledger


def _invite_status(invite: LedgerInvite, *, now: datetime) -> str:
    expires_at = invite.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if invite.revoked_at is not None:
        return "revoked"
    if expires_at <= now:
        return "expired"
    if invite.max_uses is not None and invite.used_count >= invite.max_uses:
        return "exhausted"
    return "active"


@router.post("/invite", response_model=ShareInviteCreateResponse)
def create_invite(
    req: ShareInviteCreateRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareInviteCreateResponse:
    ledger, _ = _require_owner_membership(
        db,
        user_id=current_user.id,
        ledger_external_id=req.ledger_id,
    )
    now = datetime.now(timezone.utc)
    invite_code = token_urlsafe(18)
    invite = LedgerInvite(
        code_hash=hash_invite_code(invite_code),
        ledger_id=ledger.id,
        role=req.role,
        max_uses=req.max_uses,
        used_count=0,
        expires_at=now + timedelta(hours=req.expires_in_hours),
        created_by_user_id=current_user.id,
    )
    db.add(invite)
    db.flush()
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_invite_create",
            metadata_json={
                "inviteId": invite.id,
                "ledgerId": ledger.external_id,
                "role": req.role,
                "maxUses": req.max_uses,
            },
        )
    )
    db.commit()
    return ShareInviteCreateResponse(
        invite_id=invite.id,
        invite_code=invite_code,
        ledger_id=ledger.external_id,
        role=req.role,
        max_uses=req.max_uses,
        expires_at=invite.expires_at,
    )


@router.post("/invite/revoke", response_model=ShareInviteRevokeResponse)
def revoke_invite(
    req: ShareInviteRevokeRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareInviteRevokeResponse:
    row = db.execute(
        select(LedgerInvite, Ledger, LedgerMember)
        .join(Ledger, Ledger.id == LedgerInvite.ledger_id)
        .outerjoin(
            LedgerMember,
            (LedgerMember.ledger_id == Ledger.id)
            & (LedgerMember.user_id == current_user.id)
            & (LedgerMember.status == ACTIVE_MEMBER_STATUS),
        )
        .where(
            LedgerInvite.id == req.invite_id,
        )
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite, ledger, member = row
    if member is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if member.role != ROLE_OWNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Share role forbidden")
    invite.revoked_at = datetime.now(timezone.utc)
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_invite_revoke",
            metadata_json={"inviteId": invite.id, "ledgerId": ledger.external_id},
        )
    )
    db.commit()
    return ShareInviteRevokeResponse(invite_id=invite.id, revoked=True)


@router.post("/join", response_model=ShareJoinResponse)
def join_share(
    req: ShareJoinRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareJoinResponse:
    code_hash = hash_invite_code(req.invite_code.strip())
    now = datetime.now(timezone.utc)
    row = db.execute(
        select(LedgerInvite, Ledger)
        .join(Ledger, Ledger.id == LedgerInvite.ledger_id)
        .where(
            LedgerInvite.code_hash == code_hash,
            LedgerInvite.revoked_at.is_(None),
            LedgerInvite.expires_at > now,
        )
        .limit(1)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    invite, ledger = row
    if invite.max_uses is not None and invite.used_count >= invite.max_uses:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite exhausted")

    membership = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == current_user.id,
        )
    )
    if membership is None:
        membership = LedgerMember(
            ledger_id=ledger.id,
            user_id=current_user.id,
            role=invite.role,
            status=ACTIVE_MEMBER_STATUS,
            joined_at=now,
        )
        db.add(membership)
    else:
        membership.role = invite.role
        membership.status = ACTIVE_MEMBER_STATUS
        membership.joined_at = now
        membership.left_at = None

    invite.used_count += 1
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_join",
            metadata_json={
                "inviteId": invite.id,
                "ledgerId": ledger.external_id,
                "role": invite.role,
            },
        )
    )
    db.commit()
    return ShareJoinResponse(joined=True, ledger_id=ledger.external_id, role=invite.role)


@router.post("/leave", response_model=ShareLeaveResponse)
def leave_share(
    req: ShareLeaveRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareLeaveResponse:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=req.ledger_id,
        roles=READABLE_ROLES,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger, membership = row
    if membership.role == ROLE_OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner cannot leave ledger",
        )

    now = datetime.now(timezone.utc)
    membership.status = "left"
    membership.left_at = now
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_leave",
            metadata_json={"ledgerId": ledger.external_id},
        )
    )
    db.commit()
    return ShareLeaveResponse(left=True, ledger_id=ledger.external_id)


@router.post("/member/add", response_model=ShareMemberAddResponse)
def add_member(
    req: ShareMemberAddRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareMemberAddResponse:
    ledger = _require_manage_ledger(
        db,
        current_user=current_user,
        ledger_external_id=req.ledger_id,
    )
    target_user = db.scalar(select(User).where(User.email == req.member_email).limit(1))
    if target_user is None:
        raise HTTPException(status_code=404, detail="Share member user not found")

    member = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == target_user.id,
        )
    )
    if target_user.id == ledger.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner role is immutable",
        )

    now = datetime.now(timezone.utc)
    if member is None:
        member = LedgerMember(
            ledger_id=ledger.id,
            user_id=target_user.id,
            role=req.role,
            status=ACTIVE_MEMBER_STATUS,
            joined_at=now,
        )
        db.add(member)
        result = "created"
    elif member.status != ACTIVE_MEMBER_STATUS:
        member.status = ACTIVE_MEMBER_STATUS
        member.left_at = None
        member.joined_at = now
        member.role = req.role
        result = "reactivated"
    elif member.role != req.role:
        member.role = req.role
        result = "updated"
    else:
        result = "unchanged"

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_member_add",
            metadata_json={
                "ledgerId": ledger.external_id,
                "targetUserId": target_user.id,
                "targetUserEmail": target_user.email,
                "role": req.role,
                "result": result,
            },
        )
    )
    db.commit()
    return ShareMemberAddResponse(
        result=cast("Any", result),
        ledger_id=ledger.external_id,
        user_id=target_user.id,
        user_email=target_user.email,
        role=cast("Any", member.role),
        status=cast("Any", member.status),
    )


@router.post("/member/remove", response_model=ShareMemberRemoveResponse)
def remove_member(
    req: ShareMemberRemoveRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareMemberRemoveResponse:
    ledger = _require_manage_ledger(
        db,
        current_user=current_user,
        ledger_external_id=req.ledger_id,
    )
    target_user = db.scalar(select(User).where(User.email == req.member_email).limit(1))
    if target_user is None:
        raise HTTPException(status_code=404, detail="Share member user not found")

    member = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == target_user.id,
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.user_id == ledger.user_id or member.role == ROLE_OWNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner role is immutable",
        )

    removed = member.status == ACTIVE_MEMBER_STATUS
    if removed:
        member.status = "left"
        member.left_at = datetime.now(timezone.utc)

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_member_remove",
            metadata_json={
                "ledgerId": ledger.external_id,
                "targetUserId": target_user.id,
                "targetUserEmail": target_user.email,
                "removed": removed,
            },
        )
    )
    db.commit()
    return ShareMemberRemoveResponse(
        removed=removed,
        ledger_id=ledger.external_id,
        user_id=target_user.id,
        user_email=target_user.email,
        role=cast("Any", member.role),
        status=cast("Any", member.status),
    )


@router.post("/member/role", response_model=ShareMemberRoleResponse)
def set_member_role(
    req: ShareMemberRoleRequest,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareMemberRoleResponse:
    ledger = _require_manage_ledger(
        db,
        current_user=current_user,
        ledger_external_id=req.ledger_id,
    )
    member = db.scalar(
        select(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id == req.user_id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if member.user_id == ledger.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Owner role is immutable",
        )

    member.role = req.role
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="share_member_role_update",
            metadata_json={
                "ledgerId": ledger.external_id,
                "targetUserId": req.user_id,
                "role": req.role,
            },
        )
    )
    db.commit()
    return ShareMemberRoleResponse(
        updated=True,
        ledger_id=ledger.external_id,
        user_id=req.user_id,
        role=req.role,
    )


@router.get("/members", response_model=list[LedgerMemberOut])
def list_members(
    ledger_id: str,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[LedgerMemberOut]:
    ledger = _require_readable_ledger(
        db,
        current_user=current_user,
        ledger_external_id=ledger_id,
    )
    rows = db.execute(
        select(
            LedgerMember,
            User.email,
            UserProfile.display_name,
            UserProfile.avatar_file_id,
            UserProfile.avatar_version,
        )
        .join(User, User.id == LedgerMember.user_id)
        .outerjoin(UserProfile, UserProfile.user_id == LedgerMember.user_id)
        .where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.status == ACTIVE_MEMBER_STATUS,
        )
        .order_by(LedgerMember.joined_at.asc())
    ).all()
    return [
        LedgerMemberOut(
            user_id=member.user_id,
            user_email=email,
            user_display_name=display_name,
            user_avatar_url=_avatar_url(
                user_id=member.user_id,
                avatar_version=avatar_version,
            )
            if avatar_file_id
            else None,
            user_avatar_version=avatar_version,
            role=member.role,  # type: ignore[arg-type]
            status=member.status,  # type: ignore[arg-type]
            joined_at=member.joined_at,
            left_at=member.left_at,
        )
        for member, email, display_name, avatar_file_id, avatar_version in rows
    ]


@router.get("/invites", response_model=list[ShareInviteListItem])
def list_invites(
    ledger_id: str,
    _scopes: set[str] = Depends(_SHARE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ShareInviteListItem]:
    ledger, _ = _require_owner_membership(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
    )
    rows = db.scalars(
        select(LedgerInvite)
        .where(LedgerInvite.ledger_id == ledger.id)
        .order_by(LedgerInvite.created_at.desc())
    ).all()
    now = datetime.now(timezone.utc)
    return [
        ShareInviteListItem(
            invite_id=row.id,
            ledger_id=ledger.external_id,
            role=cast("Any", row.role),
            max_uses=row.max_uses,
            used_count=row.used_count,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            status=cast("Any", _invite_status(row, now=now)),
            created_at=row.created_at,
        )
        for row in rows
    ]
