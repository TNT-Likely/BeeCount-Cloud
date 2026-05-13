"""BeeCount Cloud MCP server — 注册所有 17 个 tool,导出 ASGI app。

设计:.docs/mcp-server-design.md。

挂载入口:`src.main` 里 `app.mount(f"{api_prefix}/mcp", mcp_server.app)`。
完整对外 URL:`/api/v1/mcp/sse`(SSE channel)+ `/api/v1/mcp/messages/` (POST 消息回信道)。

鉴权:`PATAuthMiddleware` 在 ASGI 层校验 `Authorization: Bearer bcmcp_…`,
注入 `scope['bc_mcp_user']` 和 `scope['bc_mcp_scopes']`,tool 函数从
`ctx.request_context.request` 拿。详见 `.auth`。

Tool 注册分两类:
  - read:`require_mcp_scope(ctx, mcp:read)` 后调 `read_tools.py` 同名函数,
    sync 函数用 `asyncio.to_thread` 包一下避免阻塞 event loop。
  - write:`require_mcp_scope(ctx, mcp:write)` 后调 `write_tools.py` 同名
    async 函数(内部用 in-process httpx 调 write router endpoint)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..database import SessionLocal
from ..models import MCPCallLog, User
from ..security import SCOPE_MCP_READ, SCOPE_MCP_WRITE
from .auth import (
    PATAuthMiddleware,
    get_mcp_call_meta_from_context,
    get_mcp_user_from_context,
    require_mcp_scope,
)
from .tools import read_tools, write_tools

logger = logging.getLogger(__name__)


# ============================================================================
# Call logging — 每个 tool call 落一行到 MCPCallLog,Web 设置页"调用历史"读
# ============================================================================

_ARG_SUMMARY_MAX_TOTAL = 200
_ARG_VALUE_MAX_LEN = 30
# 自由文本类 / 隐私敏感 / 大块数据,做 summary 时**整字段跳过**
_ARG_SKIP_FIELDS = {"note", "text"}


def _summarize_args(kwargs: dict[str, Any]) -> str | None:
    """脱敏摘要 — 保留 tool name 调试价值,不存自由文本。"""
    if not kwargs:
        return None
    parts: list[str] = []
    for k, v in kwargs.items():
        if v is None or k in _ARG_SKIP_FIELDS:
            continue
        if isinstance(v, str):
            shown = v if len(v) <= _ARG_VALUE_MAX_LEN else v[: _ARG_VALUE_MAX_LEN - 1] + "…"
        elif isinstance(v, (list, tuple)):
            shown = f"[{len(v)}]"
        elif isinstance(v, dict):
            shown = f"{{...{len(v)}}}"
        else:
            shown = repr(v)
        parts.append(f"{k}={shown}")
    if not parts:
        return None
    s = ", ".join(parts)
    return s if len(s) <= _ARG_SUMMARY_MAX_TOTAL else s[: _ARG_SUMMARY_MAX_TOTAL - 1] + "…"


def _write_call_log(
    *,
    user_id: str,
    pat_id: str | None,
    pat_prefix: str | None,
    pat_name: str | None,
    tool_name: str,
    status: str,
    error: BaseException | None,
    args_summary: str | None,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    """同步落库 — INSERT 单行,毫秒级,在 thread 里跑不阻塞 event loop。
    失败静默(日志告警即可,不阻塞 tool 主流程)。
    """
    try:
        with SessionLocal() as db:
            err_msg: str | None = None
            if error is not None:
                detail = f"{error.__class__.__name__}: {error}"
                err_msg = detail[:500]
            db.add(
                MCPCallLog(
                    user_id=user_id,
                    pat_id=pat_id,
                    pat_prefix=pat_prefix,
                    pat_name=pat_name,
                    tool_name=tool_name,
                    status=status,
                    error_message=err_msg,
                    args_summary=args_summary,
                    duration_ms=duration_ms,
                    client_ip=client_ip,
                    called_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
    except Exception:
        logger.exception("mcp: failed to write call log for tool=%s", tool_name)


async def _logged_call(
    ctx: Context,
    *,
    name: str,
    scope: str,
    kwargs: dict[str, Any],
    body: Callable[[User], Awaitable[Any]],
) -> Any:
    """所有 tool 共用的封装 — scope check + 计时 + 审计落库。

    body 是个接收 user 返回 result 的 coroutine factory。我们在 body 之前
    做 scope 校验,之后无论成功失败都打 log。
    """
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, scope)
    meta = get_mcp_call_meta_from_context(ctx)
    summary = _summarize_args(kwargs)
    start = time.perf_counter()
    err: BaseException | None = None
    try:
        return await body(user)
    except BaseException as exc:  # noqa: BLE001 — 兜底打 log,然后 re-raise
        err = exc
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        # 放到 thread 跑 — DB 写不阻塞 LLM 拿结果
        asyncio.create_task(
            asyncio.to_thread(
                _write_call_log,
                user_id=user.id,
                pat_id=meta.get("pat_id"),
                pat_prefix=meta.get("pat_prefix"),
                pat_name=meta.get("pat_name"),
                tool_name=name,
                status="error" if err is not None else "ok",
                error=err,
                args_summary=summary,
                duration_ms=duration_ms,
                client_ip=meta.get("client_ip"),
            )
        )

# FastMCP 默认 host=127.0.0.1 时会自动开 DNS rebinding 保护,allowed_hosts
# 限定 `127.0.0.1:* / localhost:* / [::1]:*`。问题是我们的 server 实际是
# 挂在 BeeCount-Cloud 的 FastAPI 后面(反代 / 自定义域名 / docker 内网,
# Host header 是任意值),保护一开必报 421/500。这层校验跟我们的 PAT
# Bearer + CORS 是重叠的,关掉,把 host/origin 校验留给上游反代。
mcp = FastMCP(
    "BeeCount Cloud",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ),
)


# ============================================================================
# Read tools — 11 个,mcp:read scope
# ============================================================================


@mcp.tool()
async def list_ledgers(ctx: Context) -> list[dict[str, Any]]:
    """List all ledgers for the authenticated BeeCount user.

    Returns each ledger's id (external_id), name, currency, and created_at.
    Use the returned id when calling other tools that take ledger_id.
    """
    return await _logged_call(
        ctx, name="list_ledgers", scope=SCOPE_MCP_READ, kwargs={},
        body=lambda user: asyncio.to_thread(read_tools.list_ledgers, user),
    )


@mcp.tool()
async def get_active_ledger(ctx: Context) -> dict[str, Any] | None:
    """Get the user's primary/default ledger.

    Use this when the user doesn't specify which ledger they're talking about.
    Returns null if the user has no ledgers.
    """
    return await _logged_call(
        ctx, name="get_active_ledger", scope=SCOPE_MCP_READ, kwargs={},
        body=lambda user: asyncio.to_thread(read_tools.get_active_ledger, user),
    )


@mcp.tool()
async def list_transactions(
    ctx: Context,
    ledger_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    account: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    q: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Query transactions with rich filters.

    Args:
        ledger_id: Optional. If omitted, uses the active ledger.
        date_from, date_to: ISO dates (YYYY-MM-DD) or full ISO datetimes.
        category: Exact category name match.
        account: Exact account name match (matches account/from_account/to_account).
        min_amount, max_amount: Filter by absolute amount.
        q: Substring match against note.
        limit: Max items returned (1..200, default 50).
    """
    kw = dict(
        ledger_id=ledger_id, date_from=date_from, date_to=date_to,
        category=category, account=account, min_amount=min_amount,
        max_amount=max_amount, q=q, limit=limit,
    )
    return await _logged_call(
        ctx, name="list_transactions", scope=SCOPE_MCP_READ, kwargs=kw,
        body=lambda user: asyncio.to_thread(read_tools.list_transactions, user, **kw),
    )


@mcp.tool()
async def get_transaction(ctx: Context, sync_id: str) -> dict[str, Any] | None:
    """Get a single transaction by its sync_id (cross-ledger lookup)."""
    return await _logged_call(
        ctx, name="get_transaction", scope=SCOPE_MCP_READ, kwargs={"sync_id": sync_id},
        body=lambda user: asyncio.to_thread(read_tools.get_transaction, user, sync_id),
    )


@mcp.tool()
async def list_categories(
    ctx: Context, kind: str | None = None
) -> list[dict[str, Any]]:
    """List user's categories. kind is one of: expense, income, transfer."""
    return await _logged_call(
        ctx, name="list_categories", scope=SCOPE_MCP_READ, kwargs={"kind": kind},
        body=lambda user: asyncio.to_thread(read_tools.list_categories, user, kind=kind),
    )


@mcp.tool()
async def list_accounts(
    ctx: Context, account_type: str | None = None
) -> list[dict[str, Any]]:
    """List user's accounts. account_type filters by type (bank_card, credit_card, cash, ...)."""
    return await _logged_call(
        ctx, name="list_accounts", scope=SCOPE_MCP_READ, kwargs={"account_type": account_type},
        body=lambda user: asyncio.to_thread(read_tools.list_accounts, user, account_type=account_type),
    )


@mcp.tool()
async def list_tags(ctx: Context) -> list[dict[str, Any]]:
    """List all of the user's tags."""
    return await _logged_call(
        ctx, name="list_tags", scope=SCOPE_MCP_READ, kwargs={},
        body=lambda user: asyncio.to_thread(read_tools.list_tags, user),
    )


@mcp.tool()
async def list_budgets(
    ctx: Context, ledger_id: str | None = None
) -> list[dict[str, Any]]:
    """List budgets for a ledger with current-month spent/remaining/percent_used."""
    return await _logged_call(
        ctx, name="list_budgets", scope=SCOPE_MCP_READ, kwargs={"ledger_id": ledger_id},
        body=lambda user: asyncio.to_thread(read_tools.list_budgets, user, ledger_id=ledger_id),
    )


@mcp.tool()
async def get_ledger_stats(
    ctx: Context, ledger_id: str | None = None
) -> dict[str, Any] | None:
    """Get summary stats for a ledger (transaction/category/account/tag/budget counts)."""
    return await _logged_call(
        ctx, name="get_ledger_stats", scope=SCOPE_MCP_READ, kwargs={"ledger_id": ledger_id},
        body=lambda user: asyncio.to_thread(read_tools.get_ledger_stats, user, ledger_id=ledger_id),
    )


@mcp.tool()
async def get_analytics_summary(
    ctx: Context,
    scope: str = "month",
    period: str | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """Income/expense/balance plus top-10 spending categories.

    Args:
        scope: 'month' | 'year' | 'all'.
        period: For month: 'YYYY-MM'. For year: 'YYYY'. Defaults to current.
        ledger_id: Optional, uses active ledger if omitted.
    """
    kw = {"scope": scope, "period": period, "ledger_id": ledger_id}
    return await _logged_call(
        ctx, name="get_analytics_summary", scope=SCOPE_MCP_READ, kwargs=kw,
        body=lambda user: asyncio.to_thread(read_tools.get_analytics_summary, user, **kw),
    )


@mcp.tool()
async def search(ctx: Context, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text fuzzy search across transaction notes, category names, account names."""
    return await _logged_call(
        ctx, name="search", scope=SCOPE_MCP_READ, kwargs={"q": q, "limit": limit},
        body=lambda user: asyncio.to_thread(read_tools.search, user, q=q, limit=limit),
    )


# ============================================================================
# Write tools — 6 个,mcp:write scope
# ============================================================================


@mcp.tool()
async def create_transaction(
    ctx: Context,
    amount: float,
    tx_type: str = "expense",
    category: str | None = None,
    account: str | None = None,
    happened_at: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """Create a new transaction.

    Args:
        amount: Positive number; type captured separately via tx_type.
        tx_type: 'expense' (default), 'income', or 'transfer'.
        category: Existing category name (server rejects unknown names).
        account: Existing account name. For transfers this is the from-account.
        happened_at: ISO date or datetime. Defaults to now.
        note: Optional memo.
        tags: Optional list of tag names.
        ledger_id: Optional; uses active ledger if omitted.
    """
    kw = dict(
        amount=amount, tx_type=tx_type, category=category, account=account,
        happened_at=happened_at, note=note, tags=tags, ledger_id=ledger_id,
    )
    return await _logged_call(
        ctx, name="create_transaction", scope=SCOPE_MCP_WRITE, kwargs=kw,
        body=lambda user: write_tools.create_transaction(user, **kw),
    )


@mcp.tool()
async def update_transaction(
    ctx: Context,
    sync_id: str,
    amount: float | None = None,
    tx_type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    happened_at: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Patch an existing transaction. Only the fields you pass are changed."""
    kw = dict(
        sync_id=sync_id, amount=amount, tx_type=tx_type, category=category,
        account=account, happened_at=happened_at, note=note, tags=tags,
    )
    return await _logged_call(
        ctx, name="update_transaction", scope=SCOPE_MCP_WRITE, kwargs=kw,
        body=lambda user: write_tools.update_transaction(user, **kw),
    )


@mcp.tool()
async def delete_transaction(
    ctx: Context, sync_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Delete a transaction.

    **Destructive — two-step confirmation required.** Calling with confirm=False
    returns a `confirmation_required` placeholder; you must then prompt the user,
    and only call again with confirm=true after they explicitly agree.
    """
    kw = {"sync_id": sync_id, "confirm": confirm}
    return await _logged_call(
        ctx, name="delete_transaction", scope=SCOPE_MCP_WRITE, kwargs=kw,
        body=lambda user: write_tools.delete_transaction(user, **kw),
    )


@mcp.tool()
async def create_category(
    ctx: Context,
    name: str,
    kind: str = "expense",
    parent_name: str | None = None,
    icon: str | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """Create a new category. Usually unnecessary — prefer existing categories."""
    kw = dict(name=name, kind=kind, parent_name=parent_name, icon=icon, ledger_id=ledger_id)
    return await _logged_call(
        ctx, name="create_category", scope=SCOPE_MCP_WRITE, kwargs=kw,
        body=lambda user: write_tools.create_category(user, **kw),
    )


@mcp.tool()
async def update_budget(ctx: Context, budget_id: str, amount: float) -> dict[str, Any]:
    """Update a budget's amount."""
    kw = {"budget_id": budget_id, "amount": amount}
    return await _logged_call(
        ctx, name="update_budget", scope=SCOPE_MCP_WRITE, kwargs=kw,
        body=lambda user: write_tools.update_budget(user, **kw),
    )


@mcp.tool()
async def parse_and_create_from_text(
    ctx: Context, text: str, ledger_id: str | None = None
) -> dict[str, Any]:
    """Have BeeCount AI parse free-form natural-language text into a transaction.

    Useful when the user gives a sentence like "上午星巴克花了 38" and you want
    BeeCount's own AI prompt + ledger context to do the heavy lifting. Requires
    the user to have configured an AI chat provider in their profile.
    """
    kw = {"text": text, "ledger_id": ledger_id}
    return await _logged_call(
        ctx, name="parse_and_create_from_text", scope=SCOPE_MCP_WRITE, kwargs=kw,
        body=lambda user: write_tools.parse_and_create_from_text(user, **kw),
    )


# ============================================================================
# ASGI mount — wrap FastMCP's SSE app with PAT auth middleware
# ============================================================================


def _build_app():
    """Build the Starlette ASGI app to mount under `/api/v1/mcp`.

    FastMCP exposes `sse_app()` (Starlette) which provides:
      - GET  /sse        — SSE connection
      - POST /messages/  — client → server messages

    We wrap it with `PATAuthMiddleware` so every connection (including the
    initial SSE handshake) is gated on a valid `Authorization: Bearer bcmcp_…`.
    """
    sse_app = mcp.sse_app()
    return PATAuthMiddleware(sse_app)


# 模块级 ASGI app:`src.main` 直接 `app.mount(prefix, mcp_server.app)`。
app = _build_app()
# reload trigger
