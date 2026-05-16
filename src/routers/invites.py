"""共享账本邀请 endpoint。

5 个 endpoint:
  - POST   /ledgers/{ext}/invites           (owner) 创建邀请码
  - GET    /ledgers/{ext}/invites           (owner) 列出未失效未使用的邀请
  - DELETE /ledgers/{ext}/invites/{code}    (owner) 撤销
  - POST   /invites/{code}/preview          (任意已登录) 看详情
  - POST   /invites/{code}/accept           (任意已登录) 接受 → 加入

邀请码:32 字符表(排 O/0/I/1) × 6 位 = 30^6 ≈ 7 亿熵,够防爆破。
默认 24h 失效,1-168h 可调。一次性使用 — 接受后 used_at 写入,再来 404。
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import (
    ROLE_EDITOR,
    ROLE_OWNER,
    get_member_role,
    require_accessible_ledger_by_external_id,
)
from ..models import Ledger, LedgerInvite, LedgerMember, User, UserProfile
from ..security import (
    SCOPE_APP_WRITE,
    SCOPE_WEB_READ,
    SCOPE_WEB_WRITE,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# 32 字符邀请码字符集 —— 排 O/0/I/1 防混淆。secrets.choice 安全。
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 6

# Phase 1 同一 ledger 同时 active 邀请上限,防滥用 + UI 列表渲染开销
_MAX_ACTIVE_INVITES_PER_LEDGER = 10
# 单账本成员数上限(含 owner)。Phase 1 = 5。
_MEMBER_LIMIT = 5

# Phase 1 只允许 editor 作为邀请目标角色;viewer 远期。
_AllowedRole = Literal["editor"]

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
    """SQLite 不保留 tzinfo,序列化时强制 UTC 标记,前端能正确转本地时间。"""
    aware = _to_utc(value)
    return aware.isoformat() if aware is not None else None


def _generate_invite_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def _share_url_for(code: str) -> str:
    """构造可分享的短链(微信/邮件可直接发)。

    域名走 settings.invite_share_origin 配置,未配置时回退到 BeeCount 官网默认。
    """
    settings = get_settings()
    origin = getattr(settings, "invite_share_origin", None) or "https://count.beejz.com"
    return f"{origin.rstrip('/')}/invite/{code}"


def _display_name(db: Session, user: User) -> str:
    """优先 user_profiles.display_name,缺失时用 email 前缀。"""
    name = db.scalar(
        select(UserProfile.display_name).where(UserProfile.user_id == user.id)
    )
    if name:
        return name
    email = user.email or ""
    return email.split("@", 1)[0] or "Unknown"


def _normalize_code(code: str) -> str:
    """大写 + 去掉空格/横线 — caller 输入 'abc 123' 或 'abc-123' 都接受。"""
    cleaned = "".join(ch for ch in (code or "") if not ch.isspace() and ch != "-")
    return cleaned.upper()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class InviteCreateRequest(BaseModel):
    role: _AllowedRole = Field(default="editor", description="目标角色;Phase 1 仅 editor")
    expires_in_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description="有效期(小时),最长 7 天",
    )


class InviteCreateResponse(BaseModel):
    code: str
    formatted_code: str  # "ABC 123" 易读格式
    target_role: str
    expires_at: datetime
    share_url: str
    created_at: datetime

    @field_serializer("expires_at", "created_at")
    def _ser_dt(self, v: datetime) -> str:
        return _utc_iso(v) or ""


class InviteListItem(BaseModel):
    code: str
    formatted_code: str
    target_role: str
    expires_at: datetime
    created_at: datetime
    invited_by_user_id: str
    share_url: str

    @field_serializer("expires_at", "created_at")
    def _ser_dt(self, v: datetime) -> str:
        return _utc_iso(v) or ""


class InvitePreviewResponse(BaseModel):
    code: str
    ledger_external_id: str
    ledger_name: str | None
    ledger_currency: str
    invited_by_display: str
    target_role: str
    expires_at: datetime

    @field_serializer("expires_at")
    def _ser_dt(self, v: datetime) -> str:
        return _utc_iso(v) or ""


class InviteAcceptResponse(BaseModel):
    ledger_external_id: str
    ledger_name: str | None
    ledger_currency: str
    role: str
    member_count: int


def _format_code(code: str) -> str:
    """显示用 "ABC 123" 中间加空格易读。"""
    if len(code) == 6:
        return f"{code[:3]} {code[3:]}"
    return code


def _active_invite_filter(stmt, now: datetime):
    """链式 chain:仅未使用 + 未过期的 invite。"""
    return stmt.where(
        LedgerInvite.used_at.is_(None),
        LedgerInvite.expires_at > now,
    )


# ---------------------------------------------------------------------------
# Owner-only: create / list / revoke
# ---------------------------------------------------------------------------

@router.post(
    "/ledgers/{ledger_external_id}/invites",
    response_model=InviteCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invite(
    ledger_external_id: str,
    req: InviteCreateRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteCreateResponse:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles={ROLE_OWNER},
    )

    now = _utcnow()
    # 防滥用:同一 ledger 同时 active 邀请上限
    active_count = db.scalar(
        select(func.count(LedgerInvite.code)).where(
            LedgerInvite.ledger_id == ledger.id,
            LedgerInvite.used_at.is_(None),
            LedgerInvite.expires_at > now,
        )
    )
    if active_count is not None and active_count >= _MAX_ACTIVE_INVITES_PER_LEDGER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Too many active invites for this ledger",
        )

    expires_at = now + timedelta(hours=req.expires_in_hours)
    code = _generate_invite_code()
    # 重试 3 次防极小概率碰撞
    for _ in range(3):
        exists = db.scalar(select(LedgerInvite).where(LedgerInvite.code == code))
        if exists is None:
            break
        code = _generate_invite_code()
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to allocate unique invite code; please retry",
        )

    invite = LedgerInvite(
        code=code,
        ledger_id=ledger.id,
        invited_by=current_user.id,
        target_role=req.role,
        expires_at=expires_at,
        used_at=None,
        used_by=None,
        created_at=now,
    )
    db.add(invite)
    db.commit()

    logger.info(
        "invite.create ledger=%s code=%s role=%s expires=%s user=%s",
        ledger_external_id, code, req.role, expires_at.isoformat(), current_user.id,
    )

    return InviteCreateResponse(
        code=code,
        formatted_code=_format_code(code),
        target_role=req.role,
        expires_at=expires_at,
        share_url=_share_url_for(code),
        created_at=now,
    )


@router.get(
    "/ledgers/{ledger_external_id}/invites",
    response_model=list[InviteListItem],
)
def list_invites(
    ledger_external_id: str,
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InviteListItem]:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles={ROLE_OWNER},
    )

    now = _utcnow()
    rows = db.scalars(
        _active_invite_filter(
            select(LedgerInvite).where(LedgerInvite.ledger_id == ledger.id),
            now,
        ).order_by(LedgerInvite.created_at.desc())
    ).all()

    return [
        InviteListItem(
            code=r.code,
            formatted_code=_format_code(r.code),
            target_role=r.target_role,
            expires_at=r.expires_at,
            created_at=r.created_at,
            invited_by_user_id=r.invited_by,
            share_url=_share_url_for(r.code),
        )
        for r in rows
    ]


@router.delete(
    "/ledgers/{ledger_external_id}/invites/{code}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_invite(
    ledger_external_id: str,
    code: str,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles={ROLE_OWNER},
    )

    normalized = _normalize_code(code)
    invite = db.scalar(
        select(LedgerInvite).where(
            LedgerInvite.code == normalized,
            LedgerInvite.ledger_id == ledger.id,
        )
    )
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.used_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invite already used")

    # 撤销 = expires_at 设为 now,后续 active filter 自动排除。
    # 不物理 DELETE 是为了保留审计 / 防"撤销后又生成同码"的边界情况
    invite.expires_at = _utcnow()
    db.commit()

    logger.info(
        "invite.revoke ledger=%s code=%s user=%s",
        ledger_external_id, normalized, current_user.id,
    )


# ---------------------------------------------------------------------------
# Public: preview / accept (any authenticated user)
# ---------------------------------------------------------------------------

@router.post(
    "/invites/{code}/preview",
    response_model=InvitePreviewResponse,
)
def preview_invite(
    code: str,
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvitePreviewResponse:
    normalized = _normalize_code(code)
    now = _utcnow()
    invite = db.scalar(
        _active_invite_filter(
            select(LedgerInvite).where(LedgerInvite.code == normalized),
            now,
        )
    )
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invite",
        )

    ledger = db.scalar(select(Ledger).where(Ledger.id == invite.ledger_id))
    if ledger is None:
        # 边界:邀请创建后账本被删 → 邀请失效
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invite",
        )

    inviter = db.scalar(select(User).where(User.id == invite.invited_by))
    invited_by_display = _display_name(db, inviter) if inviter is not None else "Unknown"

    return InvitePreviewResponse(
        code=normalized,
        ledger_external_id=ledger.external_id,
        ledger_name=ledger.name,
        ledger_currency=ledger.currency or "CNY",
        invited_by_display=invited_by_display,
        target_role=invite.target_role,
        expires_at=invite.expires_at,
    )


@router.post(
    "/invites/{code}/accept",
    response_model=InviteAcceptResponse,
)
async def accept_invite(
    code: str,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteAcceptResponse:
    normalized = _normalize_code(code)
    now = _utcnow()

    # with_for_update 锁行 → 防两人同时接受同一码导致重复加入。
    # SQLite 上 with_for_update 退化为单事务排他锁(BEGIN IMMEDIATE)
    # ,效果一致。
    invite = db.scalar(
        _active_invite_filter(
            select(LedgerInvite).where(LedgerInvite.code == normalized).with_for_update(),
            now,
        )
    )
    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invite",
        )

    ledger = db.scalar(select(Ledger).where(Ledger.id == invite.ledger_id))
    if ledger is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invite",
        )

    # 已是成员 → 409,UI 友好提示"你已加入"
    existing_role = get_member_role(db, user_id=current_user.id, ledger_id=ledger.id)
    if existing_role is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already a member of this ledger",
        )

    # 防止接受自己邀请的邀请 — 不太可能(owner 已经是 member),但兜底
    if invite.invited_by == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot accept your own invite",
        )

    # 成员数上限
    current_member_count = db.scalar(
        select(func.count(LedgerMember.user_id)).where(
            LedgerMember.ledger_id == ledger.id
        )
    )
    if (current_member_count or 0) >= _MEMBER_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ledger has reached the {_MEMBER_LIMIT}-member limit",
        )

    # 校验目标角色合法 — Phase 1 只允许 editor;owner 必须靠 transfer 切换
    if invite.target_role not in {ROLE_EDITOR}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unsupported invite role: {invite.target_role}",
        )

    # 标记 invite 已用 + 写 ledger_members 行
    invite.used_at = now
    invite.used_by = current_user.id
    member = LedgerMember(
        ledger_id=ledger.id,
        user_id=current_user.id,
        role=invite.target_role,
        invited_by=invite.invited_by,
        joined_at=now,
    )
    db.add(member)
    db.commit()

    member_count = db.scalar(
        select(func.count(LedgerMember.user_id)).where(
            LedgerMember.ledger_id == ledger.id
        )
    ) or 0

    logger.info(
        "invite.accept ledger=%s code=%s role=%s new_member=%s inviter=%s",
        ledger.external_id, normalized, invite.target_role, current_user.id, invite.invited_by,
    )

    # WS 通知现有成员有人加入。新成员自己一般是从 join 页主动触发,UI 走 200
    # 响应直接刷新就行;但 broadcast_to_ledger 会一并给新成员推一份,客户端去重
    # 即可(客户端通常按 ledger_id 拉一次 stats 不会重复处理)。
    new_member_display = _display_name(db, current_user)
    from ..websocket_manager import broadcast_to_ledger
    await broadcast_to_ledger(
        db=db,
        ws_manager=request.app.state.ws_manager,
        ledger_id=ledger.id,
        payload={
            "type": "member_change",
            "ledgerId": ledger.external_id,
            "changeType": "joined",
            "userId": current_user.id,
            "displayName": new_member_display,
            "role": invite.target_role,
        },
    )

    return InviteAcceptResponse(
        ledger_external_id=ledger.external_id,
        ledger_name=ledger.name,
        ledger_currency=ledger.currency or "CNY",
        role=invite.target_role,
        member_count=int(member_count),
    )


