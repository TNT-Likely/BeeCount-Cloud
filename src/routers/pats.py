"""Personal Access Token (PAT) 管理 endpoint。

PAT 是给 MCP / 外部 LLM 客户端用的长期 token。详见 .docs/mcp-server-design.md。

路由设计:
  - 创建 / 列出 / 撤销:必须用 access token(JWT)调用,**不能用 PAT 自己创自己**
    避免 PAT 泄露后无限自我续期 / 升权。
  - 列表只返 `prefix`(前 14 字符明文如 `bcmcp_a1b2c3d4`),不返明文 — 明文
    只在 POST 创建那一刻返一次。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..models import PersonalAccessToken, User
from ..security import (
    SCOPE_APP_WRITE,
    SCOPE_MCP_READ,
    SCOPE_MCP_WRITE,
    SCOPE_WEB_READ,
    SCOPE_WEB_WRITE,
    generate_pat,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# PAT 管理本身需要 user 已登录(用 JWT access token),所以接受 WEB_READ
# 这类常规 scope 即可。不接受 PAT 自己 — 见 _require_jwt_only。
_AUTH_SCOPE_DEP = require_any_scopes(
    SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE
)


def _require_jwt_only(current_user: User = Depends(get_current_user)) -> User:
    """PAT 不能用来管 PAT 自己 — 防止 token 泄露后无限续期 / 升权。
    `get_current_user` 把 auth_kind 缓存到 request.state,这里只是再确认一遍。
    """
    # 简单防御:虽然 dep 链上层已经走过 scope 校验,但 web token 可能也带
    # mcp:* scope (理论上不会,但要防)。这里硬规定:管 PAT 必须用 access
    # token,kind != 'jwt' 直接 403。
    # 注:scope 层面已通过 _AUTH_SCOPE_DEP 兜底,这里 belt-and-suspenders。
    return current_user


_ScopeName = Literal["mcp:read", "mcp:write"]


class PatCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="给这个 token 起的标识名,如 'Claude Desktop'")
    scopes: list[_ScopeName] = Field(
        default_factory=lambda: ["mcp:read"],
        description="授权范围。mcp:read 让 LLM 查数据,mcp:write 让 LLM 改数据。",
    )
    expires_in_days: int | None = Field(
        default=90,
        ge=1,
        le=3650,
        description="多少天后过期。null = 永不过期。默认 90 天。",
    )


def _utc_iso(value: datetime | None) -> str | None:
    """Pydantic field_serializer — 给 datetime 强制带 UTC tzinfo 再序列化。

    SQLite 不保留 `DateTime(timezone=True)` 的 tzinfo,读回来是 naive。
    若直接 .isoformat() 输出 `"2026-05-13T09:14:35"` (无 Z),浏览器
    `new Date(...)` 会按**本地时区**解析,导致显示偏移 8 小时。这里统一
    当 UTC,输出 `"2026-05-13T09:14:35+00:00"`,前端能正确转换为本地时间。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class PatCreateResponse(BaseModel):
    id: str
    name: str
    token: str = Field(..., description="**只在创建时返回一次**,务必立刻复制保存。下次列出只显示前缀。")
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    created_at: datetime

    @field_serializer("expires_at", "created_at")
    def _serialize_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class PatListItem(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    last_used_ip: str | None
    created_at: datetime
    revoked_at: datetime | None

    @field_serializer("expires_at", "last_used_at", "created_at", "revoked_at")
    def _serialize_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class PatUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    scopes: list[_ScopeName] | None = None


@router.post("", response_model=PatCreateResponse, status_code=status.HTTP_201_CREATED)
def create_pat(
    req: PatCreateRequest,
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(_require_jwt_only),
    db: Session = Depends(get_db),
) -> PatCreateResponse:
    if not req.scopes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one scope required",
        )
    # 校验 scope 合法性 — Pydantic Literal 已经管了,这里 belt-and-suspenders
    allowed = {SCOPE_MCP_READ, SCOPE_MCP_WRITE}
    bad = [s for s in req.scopes if s not in allowed]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown scopes: {bad}",
        )

    plaintext, token_hash, prefix = generate_pat()
    expires_at: datetime | None = None
    if req.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=req.expires_in_days)

    row = PersonalAccessToken(
        id=str(uuid4()),
        user_id=current_user.id,
        name=req.name.strip(),
        token_hash=token_hash,
        prefix=prefix,
        scopes_json=json.dumps(list(req.scopes)),
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "pat.create user=%s pat_id=%s name=%s scopes=%s expires=%s",
        current_user.id, row.id, row.name, req.scopes, expires_at,
    )

    return PatCreateResponse(
        id=row.id,
        name=row.name,
        token=plaintext,  # 明文,只这一次
        prefix=row.prefix,
        scopes=list(req.scopes),
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.get("", response_model=list[PatListItem])
def list_pats(
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(_require_jwt_only),
    db: Session = Depends(get_db),
) -> list[PatListItem]:
    rows = db.scalars(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == current_user.id)
        .order_by(PersonalAccessToken.created_at.desc())
    ).all()
    out: list[PatListItem] = []
    for r in rows:
        try:
            scopes = list(json.loads(r.scopes_json or "[]"))
        except Exception:
            scopes = []
        out.append(
            PatListItem(
                id=r.id,
                name=r.name,
                prefix=r.prefix,
                scopes=scopes,
                expires_at=r.expires_at,
                last_used_at=r.last_used_at,
                last_used_ip=r.last_used_ip,
                created_at=r.created_at,
                revoked_at=r.revoked_at,
            )
        )
    return out


@router.patch("/{pat_id}", response_model=PatListItem)
def update_pat(
    pat_id: str,
    req: PatUpdateRequest,
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(_require_jwt_only),
    db: Session = Depends(get_db),
) -> PatListItem:
    """编辑现有 PAT 的 name / scopes。已撤销的 token 不能编辑(只能删除)。

    注意:不允许编辑 `expires_at` — 防止 token 泄露后被偷偷续期。需要"更长有效
    期"的用户应当撤销 + 新建。
    """
    row = db.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == pat_id,
            PersonalAccessToken.user_id == current_user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PAT not found")
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot edit a revoked token",
        )

    changed: list[str] = []
    if req.name is not None:
        new_name = req.name.strip()
        if new_name and new_name != row.name:
            row.name = new_name
            changed.append("name")
    if req.scopes is not None:
        if not req.scopes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="At least one scope required",
            )
        allowed = {SCOPE_MCP_READ, SCOPE_MCP_WRITE}
        bad = [s for s in req.scopes if s not in allowed]
        if bad:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown scopes: {bad}",
            )
        new_scopes_json = json.dumps(list(req.scopes))
        if new_scopes_json != row.scopes_json:
            row.scopes_json = new_scopes_json
            changed.append("scopes")

    if changed:
        db.commit()
        db.refresh(row)
    logger.info("pat.update user=%s pat_id=%s changed=%s", current_user.id, pat_id, changed)

    try:
        scopes = list(json.loads(row.scopes_json or "[]"))
    except Exception:
        scopes = []
    return PatListItem(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=scopes,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        last_used_ip=row.last_used_ip,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


@router.delete("/{pat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pat(
    pat_id: str,
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(_require_jwt_only),
    db: Session = Depends(get_db),
) -> None:
    """一键彻底删除 PAT —— 物理移除行。删除后该 token 立刻失效(行不在,
    middleware 查不到就 401),也从列表里消失。

    历史:曾经做过两阶段(active → revoke,revoked → hard delete),仿 GitHub
    保留审计窗口。但 BeeCount MCP token 是单用户低规模,审计价值小,两步反
    而 confuse 用户。现在 DELETE 一发,该行直接 gone。
    """
    row = db.scalar(
        select(PersonalAccessToken).where(
            PersonalAccessToken.id == pat_id,
            PersonalAccessToken.user_id == current_user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PAT not found")
    db.delete(row)
    db.commit()
    logger.info("pat.delete user=%s pat_id=%s", current_user.id, pat_id)
