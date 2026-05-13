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
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..security import SCOPE_MCP_READ, SCOPE_MCP_WRITE
from .auth import PATAuthMiddleware, get_mcp_user_from_context, require_mcp_scope
from .tools import read_tools, write_tools

logger = logging.getLogger(__name__)

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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.list_ledgers, user)


@mcp.tool()
async def get_active_ledger(ctx: Context) -> dict[str, Any] | None:
    """Get the user's primary/default ledger.

    Use this when the user doesn't specify which ledger they're talking about.
    Returns null if the user has no ledgers.
    """
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.get_active_ledger, user)


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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(
        read_tools.list_transactions,
        user,
        ledger_id=ledger_id,
        date_from=date_from,
        date_to=date_to,
        category=category,
        account=account,
        min_amount=min_amount,
        max_amount=max_amount,
        q=q,
        limit=limit,
    )


@mcp.tool()
async def get_transaction(ctx: Context, sync_id: str) -> dict[str, Any] | None:
    """Get a single transaction by its sync_id (cross-ledger lookup)."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.get_transaction, user, sync_id)


@mcp.tool()
async def list_categories(
    ctx: Context, kind: str | None = None
) -> list[dict[str, Any]]:
    """List user's categories. kind is one of: expense, income, transfer."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.list_categories, user, kind=kind)


@mcp.tool()
async def list_accounts(
    ctx: Context, account_type: str | None = None
) -> list[dict[str, Any]]:
    """List user's accounts. account_type filters by type (bank_card, credit_card, cash, ...)."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.list_accounts, user, account_type=account_type)


@mcp.tool()
async def list_tags(ctx: Context) -> list[dict[str, Any]]:
    """List all of the user's tags."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.list_tags, user)


@mcp.tool()
async def list_budgets(
    ctx: Context, ledger_id: str | None = None
) -> list[dict[str, Any]]:
    """List budgets for a ledger with current-month spent/remaining/percent_used."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.list_budgets, user, ledger_id=ledger_id)


@mcp.tool()
async def get_ledger_stats(
    ctx: Context, ledger_id: str | None = None
) -> dict[str, Any] | None:
    """Get summary stats for a ledger (transaction/category/account/tag/budget counts)."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.get_ledger_stats, user, ledger_id=ledger_id)


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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(
        read_tools.get_analytics_summary,
        user,
        scope=scope,
        period=period,
        ledger_id=ledger_id,
    )


@mcp.tool()
async def search(ctx: Context, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text fuzzy search across transaction notes, category names, account names."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_READ)
    return await asyncio.to_thread(read_tools.search, user, q=q, limit=limit)


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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_WRITE)
    return await write_tools.create_transaction(
        user,
        amount=amount,
        tx_type=tx_type,
        category=category,
        account=account,
        happened_at=happened_at,
        note=note,
        tags=tags,
        ledger_id=ledger_id,
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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_WRITE)
    return await write_tools.update_transaction(
        user,
        sync_id=sync_id,
        amount=amount,
        tx_type=tx_type,
        category=category,
        account=account,
        happened_at=happened_at,
        note=note,
        tags=tags,
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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_WRITE)
    return await write_tools.delete_transaction(user, sync_id=sync_id, confirm=confirm)


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
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_WRITE)
    return await write_tools.create_category(
        user,
        name=name,
        kind=kind,
        parent_name=parent_name,
        icon=icon,
        ledger_id=ledger_id,
    )


@mcp.tool()
async def update_budget(ctx: Context, budget_id: str, amount: float) -> dict[str, Any]:
    """Update a budget's amount."""
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_WRITE)
    return await write_tools.update_budget(user, budget_id=budget_id, amount=amount)


@mcp.tool()
async def parse_and_create_from_text(
    ctx: Context, text: str, ledger_id: str | None = None
) -> dict[str, Any]:
    """Have BeeCount AI parse free-form natural-language text into a transaction.

    Useful when the user gives a sentence like "上午星巴克花了 38" and you want
    BeeCount's own AI prompt + ledger context to do the heavy lifting. Requires
    the user to have configured an AI chat provider in their profile.
    """
    user = get_mcp_user_from_context(ctx)
    require_mcp_scope(ctx, SCOPE_MCP_WRITE)
    return await write_tools.parse_and_create_from_text(
        user, text=text, ledger_id=ledger_id
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
