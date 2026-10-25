"""Typed capability adapters: Context is injected, user identity never comes from arguments."""

from __future__ import annotations

import asyncio
import inspect
from functools import wraps

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from ..security import SCOPE_MCP_READ, SCOPE_MCP_WRITE
from .auth import get_mcp_scopes_from_context


def adapter(fn, readonly, public_sig, logged_call):
    @wraps(fn)
    async def wrapped(ctx: Context, **kwargs):
        async def body(user):
            if inspect.iscoroutinefunction(fn):
                result = await fn(user, **kwargs)
            else:
                result = await asyncio.to_thread(fn, user, **kwargs)
            if fn.__name__ == "get_bookkeeping_context":
                result["pat_scopes"] = sorted(get_mcp_scopes_from_context(ctx))
            return result

        return await logged_call(
            ctx,
            name=fn.__name__,
            scope=SCOPE_MCP_READ if readonly else SCOPE_MCP_WRITE,
            kwargs=kwargs,
            body=body,
        )

    wrapped.__signature__ = public_sig
    wrapped.__annotations__ = {
        param.name: param.annotation for param in public_sig.parameters.values()
    }
    wrapped.__annotations__["return"] = public_sig.return_annotation
    return wrapped


def register_functions(mcp, logged_call, read_functions, write_functions):
    for function in [*read_functions, *write_functions]:
        read_only = function in read_functions
        signature = inspect.signature(function, eval_str=True)
        params = list(signature.parameters.values())
        params[0] = params[0].replace(name="ctx", annotation=Context)
        public_signature = signature.replace(parameters=params)

        wrapped = adapter(function, read_only, public_signature, logged_call)
        mcp.add_tool(
            wrapped,
            name=function.__name__,
            annotations=ToolAnnotations(
                readOnlyHint=read_only and function.__name__ != "export_transactions",
                destructiveHint=not read_only,
                idempotentHint=(read_only and function.__name__ != "export_transactions")
                or function.__name__ == "retry_operation",
                openWorldHint=function.__name__ in {"parse_receipt", "preview_text_transactions"},
            ),
        )
