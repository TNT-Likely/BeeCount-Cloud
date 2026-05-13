"""MCP HTTP+SSE 请求的 PAT 鉴权 + user context 注入。

跟 FastAPI 普通 dep 机制不同 — MCP 的 sse_app 是独立 Starlette ASGI 应用,
不走 FastAPI dependency tree。所以 PAT 校验放在 ASGI middleware,把 user
注入到 `request.scope['bc_mcp_user']`,tool 实现从 Context 拿。

这跟 deps.py 里 `_resolve_pat` 用同一份 token 校验逻辑,只是 transport 不同。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..database import SessionLocal
from ..models import PersonalAccessToken, User
from ..security import looks_like_pat, verify_pat_hash

logger = logging.getLogger(__name__)


class PATAuthMiddleware:
    """校验 `Authorization: Bearer <PAT>`,把 user + scopes 塞进 scope。

    校验失败:401 JSON。校验通过:
      scope['bc_mcp_user'] = User
      scope['bc_mcp_scopes'] = set[str]   (从 PAT.scopes_json 取)
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" and scope["type"] != "websocket":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = self._extract_bearer(request)
        if token is None:
            await self._reject(send, 401, "Missing Authorization header")
            return
        if not looks_like_pat(token):
            await self._reject(send, 403, "MCP endpoint requires a PAT")
            return

        try:
            user, scopes = _resolve_pat_sync(token, request)
        except _AuthError as exc:
            await self._reject(send, exc.code, exc.detail)
            return

        scope["bc_mcp_user"] = user
        scope["bc_mcp_scopes"] = scopes
        await self.app(scope, receive, send)

    @staticmethod
    def _extract_bearer(request: Request) -> str | None:
        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("bearer "):
            return None
        return header[7:].strip() or None

    @staticmethod
    async def _reject(send: Send, code: int, detail: str) -> None:
        resp = JSONResponse({"error": {"code": code, "message": detail}}, status_code=code)
        await resp(scope={"type": "http"}, receive=None, send=send)  # type: ignore[arg-type]


class _AuthError(Exception):
    def __init__(self, code: int, detail: str) -> None:
        self.code = code
        self.detail = detail


def _resolve_pat_sync(token: str, request: Request) -> tuple[User, set[str]]:
    """跟 deps._resolve_pat 同逻辑,但用同步 Session(MCP middleware 在 ASGI
    层,不走 FastAPI Depends)。"""
    import hashlib
    import hmac

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        row = db.scalar(
            select(PersonalAccessToken).where(PersonalAccessToken.token_hash == token_hash)
        )
        if row is None:
            raise _AuthError(401, "Invalid token")
        if not verify_pat_hash(token, row.token_hash):
            raise _AuthError(401, "Invalid token")
        if row.revoked_at is not None:
            raise _AuthError(401, "Token revoked")
        if row.expires_at is not None:
            # SQLite 不保留 DateTime(timezone=True) 的 tzinfo,读回来是 naive;
            # Postgres 读回来是 aware。统一当 UTC 处理,避免 TZ-aware vs naive
            # 比较抛 TypeError(过去这里直接 500)。
            exp = row.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                raise _AuthError(401, "Token expired")

        user = db.scalar(select(User).where(User.id == row.user_id))
        if user is None or not user.is_enabled:
            raise _AuthError(403, "User disabled")

        try:
            scopes = set(json.loads(row.scopes_json or "[]"))
        except Exception:
            scopes = set()

        # bump last_used
        client_ip = request.client.host if request.client else None
        row.last_used_at = datetime.now(timezone.utc)
        row.last_used_ip = client_ip
        db.commit()

        # commit 后 SQLAlchemy 默认 expire 所有对象属性,下次访问会触发 DB
        # 重新加载。我们随后 expunge user 让它脱离 session —— 但脱离的同时
        # 属性已被 expire,后续读 user.id / user.email 会触发 lazy load,
        # 但 session 已关 → DetachedInstanceError。所以 expunge 前必须先
        # refresh,把 attribute 重新填回内存。
        db.refresh(user)
        db.expunge(user)
        return user, scopes


def get_mcp_user_from_context(ctx: Any) -> User:
    """从 FastMCP Context 拿 user(中间件之前注入到 ASGI scope)。

    用法:
        @mcp.tool()
        async def list_ledgers(ctx: Context) -> ...:
            user = get_mcp_user_from_context(ctx)
            ...
    """
    request = ctx.request_context.request
    if request is None:
        raise RuntimeError("MCP tool called outside HTTP request context")
    user = request.scope.get("bc_mcp_user")
    if not isinstance(user, User):
        raise RuntimeError("PATAuthMiddleware did not populate bc_mcp_user")
    return user


def get_mcp_scopes_from_context(ctx: Any) -> set[str]:
    request = ctx.request_context.request
    if request is None:
        return set()
    scopes = request.scope.get("bc_mcp_scopes")
    return scopes if isinstance(scopes, set) else set()


def require_mcp_scope(ctx: Any, required: str) -> None:
    """tool 函数内部 scope 检查。读 tools 要 mcp:read,写 tools 要 mcp:write。"""
    scopes = get_mcp_scopes_from_context(ctx)
    if required not in scopes:
        raise PermissionError(f"PAT missing required scope: {required}")
