#!/usr/bin/env python3
"""在客户端运行的 stdio MCP：用本地文件路径上传原图，其余工具转发到 Cloud。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import mimetypes
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import mcp.types as types
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from mcp import ClientSession

MAX_BYTES = 64 * 1024 * 1024


def read_receipt(file_path: str, allowed_dirs: list[Path], max_bytes: int = MAX_BYTES) -> tuple[str, bytes]:
    """只读取明确授权目录内的普通文件；不缩放、转码或修改图片。"""
    path = Path(file_path).expanduser()
    if not path.is_absolute():
        raise ValueError("file_path must be an absolute local path")
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Local receipt file does not exist or is unreadable") from exc
    if not any(path.is_relative_to(root) for root in allowed_dirs):
        raise ValueError("File is outside the allowed receipt directories; configure --allow-dir")
    # 不让 FIFO 等特殊文件挂住客户端；打开后再核对文件类型与大小。
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Receipt must be a regular file")
            if not 0 < info.st_size <= max_bytes:
                raise ValueError("Receipt must be non-empty and at most 64 MiB; Cloud may use a lower limit")
            data = file.read(max_bytes + 1)
    except OSError as exc:
        raise ValueError("Local receipt file is unreadable") from exc
    if not data or len(data) > max_bytes:
        raise ValueError("Receipt changed size while reading; retry with a stable file")
    return path.name, data


def local_upload_tool(tool: types.Tool) -> types.Tool:
    """保持同名上传工具，把大块内联输入改成客户端的文件路径。"""
    schema = dict(tool.inputSchema)
    props = dict(schema.get("properties", {}))
    props.pop("content_base64", None)
    props.pop("file_name", None)
    props["file_path"] = {
        "type": "string", "minLength": 1,
        "description": "Absolute path to the original receipt on this computer, inside a configured --allow-dir.",
    }
    schema.update(properties=props, required=[
        *[key for key in schema.get("required", []) if key not in ("content_base64", "file_name")],
        "file_path",
    ], additionalProperties=False)
    return tool.model_copy(update={
        "inputSchema": schema,
        "description": "Upload an original local receipt file to the target BeeCount ledger. "
        "Pass file_path; this client reads and uploads the unchanged bytes. "
        "Do not read, split, encode, resize or compress the image yourself. "
        "Returns file_id/sha256/size; pass file IDs to create_transaction or update_transaction. "
        "Requires mcp:write. All other ledger selection and dedup rules are enforced by Cloud.",
    })


async def discover_tools(upstream: ClientSession) -> list[types.Tool]:
    tools: list[types.Tool] = []
    cursor = None
    seen = set()
    while True:
        page = await upstream.list_tools(cursor=cursor)
        tools.extend(local_upload_tool(t) if t.name == "upload_attachment" else t for t in page.tools)
        cursor = page.nextCursor
        if not cursor:
            return tools
        if cursor in seen:
            raise RuntimeError("Cloud returned an invalid tool pagination cursor")
        seen.add(cursor)


async def forward_call(
    upstream: ClientSession, name: str, arguments: dict[str, Any], allowed_dirs: list[Path],
) -> types.CallToolResult:
    payload = dict(arguments)
    if name == "upload_attachment":
        # 不提供 Base64 模式，确保本地客户端不会诱导模型搬运图片内容。
        if "content_base64" in payload or "file_name" in payload:
            raise ValueError("Use file_path for this local upload tool")
        file_path = payload.pop("file_path", None)
        if not isinstance(file_path, str) or not file_path.strip():
            raise ValueError("file_path is required")
        name_on_disk, data = await asyncio.to_thread(read_receipt, file_path, allowed_dirs)
        encoded = await asyncio.to_thread(base64.b64encode, data)
        payload.update(file_name=name_on_disk, content_base64=encoded.decode("ascii"))
        if not payload.get("mime_type"):
            payload["mime_type"] = mimetypes.guess_type(name_on_disk)[0] or "application/octet-stream"
    return await upstream.call_tool(name, payload)


def make_server(upstream: ClientSession, allowed_dirs: list[Path]) -> Server:
    server = Server("beecount-local-files")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return await discover_tools(upstream)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        try:
            return await forward_call(upstream, name, arguments, allowed_dirs)
        except ValueError as exc:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=str(exc))])
        except Exception:
            # 不把网络上下文、请求参数、凭证或图片字节写进 MCP 结果/日志。
            return types.CallToolResult(isError=True, content=[types.TextContent(
                type="text", text="Cloud request failed; check the endpoint, PAT and server availability",
            )])

    return server


async def serve(endpoint: str, token: str, allowed_dirs: list[Path]) -> None:
    async with streamablehttp_client(endpoint, headers={"Authorization": f"Bearer {token}"}) as (reader, writer, _):
        async with ClientSession(reader, writer) as upstream:
            await upstream.initialize()
            server = make_server(upstream, allowed_dirs)
            async with stdio_server() as (local_reader, local_writer):
                await server.run(local_reader, local_writer, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="Cloud URL ending in /api/v1/mcp")
    parser.add_argument("--allow-dir", action="append", required=True, type=Path,
                        help="Allow local receipt reads in this directory; repeat to allow more directories")
    args = parser.parse_args()
    parsed = urlparse(args.endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        parser.error("endpoint must be an HTTP(S) URL without embedded credentials")
    token = os.environ.get("BEECOUNT_MCP_PAT")
    if not token:
        parser.error("Set BEECOUNT_MCP_PAT to a PAT with mcp:write")
    roots = [path.expanduser().resolve() for path in args.allow_dir]
    if not all(path.is_dir() for path in roots):
        parser.error("Every --allow-dir must be an existing directory")
    try:
        asyncio.run(serve(args.endpoint, token, roots))
    except Exception:
        parser.exit(1, "MCP connection failed; check the endpoint, PAT and server availability\n")


if __name__ == "__main__":
    main()
