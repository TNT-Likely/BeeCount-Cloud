"""MCP tool 调用历史的查询 endpoint。

数据来源:`mcp/server.py` 在每个 tool call 完成后异步写入的 `MCPCallLog` 表。
设计文档:`docs/MCP.md` 调用历史章节。

只读 endpoint,只能看自己用户名下的记录。撤销过的 PAT 历史也保留(pat_id
SET NULL 但 pat_prefix 还能识别)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..models import MCPCallLog, PersonalAccessToken, User
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

router = APIRouter()

_AUTH_SCOPE_DEP = require_any_scopes(
    SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE
)


def _utc_iso(value: datetime | None) -> str | None:
    """跟 pats.py 同一套 UTC 标记化序列化 — SQLite 读回 naive,前端按本地解析会偏。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class MCPCallItem(BaseModel):
    id: int
    tool_name: str
    status: str
    error_message: str | None
    args_summary: str | None
    duration_ms: int
    pat_id: str | None
    pat_prefix: str | None
    # client_label:**有效**的客户端名 —
    #   1. 优先 LEFT JOIN 拿 PAT 当前 name(改名后 UI 立即同步)
    #   2. PAT 已删 → 降级到日志当时缓存的 pat_name
    #   3. 都没有 → 降到 prefix
    # 前端只渲染这个字段,不再自己做降级逻辑
    client_label: str | None
    # PAT 是否还存在 — false 时前端可以加个"(已删除)"角标
    client_active: bool
    client_ip: str | None
    called_at: datetime

    @field_serializer("called_at")
    def _ser_dt(self, v: datetime | None) -> str | None:
        return _utc_iso(v)


class MCPCallListResponse(BaseModel):
    total: int
    items: list[MCPCallItem]


@router.get("", response_model=MCPCallListResponse)
def list_mcp_calls(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tool_name: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern=r"^(ok|error)$"),
    pat_id: str | None = Query(default=None),
    _scopes: set[str] = Depends(_AUTH_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MCPCallListResponse:
    """分页列出当前用户的 MCP 调用历史(按 called_at 倒序)。

    支持按 tool_name / status / pat_id 过滤。total 是符合 filter 后的总数,
    用于前端展示分页控件。
    """
    # LEFT JOIN PAT 拿当前 name —— PAT 还在则用 live 值(支持改名后历史 UI 同步),
    # 已被删除 (`pat_id IS NULL` 或 token 行被删) 则回退到日志当时缓存的
    # `pat_name`。一次查询完成,不需要 N+1。
    base = (
        select(MCPCallLog, PersonalAccessToken.name)
        .outerjoin(
            PersonalAccessToken,
            MCPCallLog.pat_id == PersonalAccessToken.id,
        )
        .where(MCPCallLog.user_id == current_user.id)
    )
    if tool_name:
        base = base.where(MCPCallLog.tool_name == tool_name)
    if status:
        base = base.where(MCPCallLog.status == status)
    if pat_id:
        base = base.where(MCPCallLog.pat_id == pat_id)

    count_q = select(func.count()).select_from(
        select(MCPCallLog.id)
        .where(MCPCallLog.user_id == current_user.id)
        .where(
            *[c for c in [
                MCPCallLog.tool_name == tool_name if tool_name else None,
                MCPCallLog.status == status if status else None,
                MCPCallLog.pat_id == pat_id if pat_id else None,
            ] if c is not None]
        )
        .subquery()
    )
    total = int(db.scalar(count_q) or 0)
    rows = db.execute(
        base.order_by(MCPCallLog.called_at.desc()).limit(limit).offset(offset)
    ).all()
    items: list[MCPCallItem] = []
    for r, current_name in rows:
        # 优先用 JOIN 拿到的当前名;否则降级到 denormalized;最后到 prefix
        client_active = current_name is not None
        label = current_name or r.pat_name or r.pat_prefix
        items.append(
            MCPCallItem(
                id=r.id,
                tool_name=r.tool_name,
                status=r.status,
                error_message=r.error_message,
                args_summary=r.args_summary,
                duration_ms=r.duration_ms,
                pat_id=r.pat_id,
                pat_prefix=r.pat_prefix,
                client_label=label,
                client_active=client_active,
                client_ip=r.client_ip,
                called_at=r.called_at,
            )
        )
    return MCPCallListResponse(total=total, items=items)
