#!/usr/bin/env python3
"""在客户端读取本地小票，通过 Streamable HTTP MCP 上传；不输出 PAT。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path

from mcp.client.streamable_http import streamablehttp_client

from mcp import ClientSession


async def upload_files(endpoint: str, token: str, ledger_id: str, paths: list[Path]) -> dict:
    # 上传前验证全部文件，避免第二个路径错误时第一个已经上传。
    files = []
    for path in paths:
        if not path.is_file():
            raise ValueError("Every receipt path must be an existing file")
        if not 0 < path.stat().st_size <= 64 * 1024 * 1024:
            raise ValueError("Receipt must be non-empty and at most 64 MiB; Cloud may use a lower limit")
        files.append((path.name, path.read_bytes()))
    uploaded = []
    async with streamablehttp_client(endpoint, headers={"Authorization": f"Bearer {token}"}) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            for name, data in files:
                result = await session.call_tool("upload_attachment", {
                    "ledger_id": ledger_id, "file_name": name,
                    "mime_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                    "content_base64": base64.b64encode(data).decode("ascii"),
                })
                if result.isError:
                    raise RuntimeError("Cloud rejected the upload; check MCP call history")
                value = result.structuredContent
                if value is None:
                    value = json.loads(next(c.text for c in result.content if c.type == "text"))
                if not value.get("file_id"):
                    raise RuntimeError("Cloud did not return a file_id; verify the target ledger")
                uploaded.append(value)
    return {"ledger_id": ledger_id, "attachments": list(dict.fromkeys(v["file_id"] for v in uploaded)), "files": uploaded}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="Cloud URL ending in /api/v1/mcp")
    parser.add_argument("--ledger-id", required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    token = os.environ.get("BEECOUNT_MCP_PAT")
    if not token:
        parser.error("Set BEECOUNT_MCP_PAT to a PAT with mcp:write")
    try:
        result = asyncio.run(upload_files(args.endpoint, token, args.ledger_id, args.files))
    except (ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
