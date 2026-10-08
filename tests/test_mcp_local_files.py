"""客户端本地文件上传不让模型搬运 Base64，不读取未授权目录。"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import mcp.types as types
import pytest

spec = importlib.util.spec_from_file_location("local_files", Path(__file__).parents[1] / "scripts/mcp_local_files.py")
assert spec and spec.loader
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def test_schema_exposes_path_and_keeps_upstream_output():
    remote = types.Tool(name="upload_attachment", inputSchema={
        "type": "object", "properties": {"content_base64": {"type": "string"}, "file_name": {"type": "string"},
                                             "ledger_id": {"type": "string"}},
        "required": ["content_base64", "file_name"],
    }, outputSchema={"type": "object"})
    local = bridge.local_upload_tool(remote)
    assert local.inputSchema["required"] == ["file_path"]
    assert set(local.inputSchema["properties"]) == {"ledger_id", "file_path"}
    assert local.inputSchema["additionalProperties"] is False
    assert local.outputSchema == remote.outputSchema
    assert "content_base64" in remote.inputSchema["properties"]


def test_original_large_file_bytes_are_unchanged(tmp_path):
    data = b"synthetic-receipt" * 50000  # 大于截图中的 680KB，完全不做转码/压缩。
    path = tmp_path / "小票 with spaces.png"
    path.write_bytes(data)
    name, result = bridge.read_receipt(str(path), [tmp_path])
    assert name == path.name and result == data


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "oversized", "relative", "outside", "symlink-escape"])
def test_invalid_local_files_are_rejected(tmp_path, kind):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    p = allowed / "receipt.png"
    if kind in ("empty", "oversized"):
        p.write_bytes(b"" if kind == "empty" else b"x" * 5)
    elif kind == "directory":
        p.mkdir()
    elif kind in ("outside", "symlink-escape"):
        outside = tmp_path / "private.png"
        outside.write_bytes(b"not a receipt in the allowed directory")
        if kind == "outside":
            p = outside
        else:
            p.symlink_to(outside)
    elif kind == "relative":
        p = Path("receipt.png")
    with pytest.raises(ValueError):
        bridge.read_receipt(str(p), [allowed], max_bytes=4)


def test_non_regular_file_does_not_block(tmp_path):
    import os
    p = tmp_path / "receipt.png"
    os.mkfifo(p)
    with pytest.raises(ValueError, match="regular file"):
        bridge.read_receipt(str(p), [tmp_path])


def test_symlink_to_allowed_file_is_supported(tmp_path):
    p = tmp_path / "actual.png"
    p.write_bytes(b"original")
    link = tmp_path / "alias.png"
    link.symlink_to(p)
    assert bridge.read_receipt(str(link), [tmp_path]) == ("actual.png", b"original")


class Upstream:
    def __init__(self):
        self.calls = []
        self.result = types.CallToolResult(content=[], structuredContent={"file_id": "uploaded"})

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.result


def test_upload_forwards_bytes_and_never_forwards_local_path(tmp_path):
    p = tmp_path / "原始.png"
    data = b"unaltered original image bytes"
    p.write_bytes(data)
    upstream = Upstream()
    result = asyncio.run(bridge.forward_call(upstream, "upload_attachment", {
        "file_path": str(p), "ledger_id": "target",
    }, [tmp_path]))
    assert result is upstream.result
    name, args = upstream.calls[0]
    assert name == "upload_attachment" and args["ledger_id"] == "target"
    assert "file_path" not in args
    assert args["file_name"] == p.name and args["mime_type"] == "image/png"
    assert base64.b64decode(args["content_base64"]) == data
    assert result.structuredContent == {"file_id": "uploaded"}


@pytest.mark.parametrize("args", [{}, {"file_path": 123}, {"file_path": ""}, {"content_base64": "x"}, {"file_name": "x"}])
def test_bad_arguments_never_reach_cloud(tmp_path, args):
    upstream = Upstream()
    with pytest.raises(ValueError):
        asyncio.run(bridge.forward_call(upstream, "upload_attachment", args, [tmp_path]))
    assert upstream.calls == []


def test_other_tools_are_forwarded_unmodified(tmp_path):
    upstream = Upstream()
    args = {"amount": 42, "attachments": ["existing"]}
    result = asyncio.run(bridge.forward_call(upstream, "create_transaction", args, [tmp_path]))
    assert result is upstream.result and upstream.calls == [("create_transaction", args)]


def test_discovery_preserves_all_tools_and_pagination():
    tools = [types.Tool(name=name, inputSchema={"type": "object"})
             for name in ("list_ledgers", "upload_attachment", "create_transaction")]

    class Remote:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(tools=tools[:2] if cursor is None else tools[2:], nextCursor="next" if cursor is None else None)

    local = asyncio.run(bridge.discover_tools(Remote()))
    assert [t.name for t in local] == [t.name for t in tools]
    assert local[0] is tools[0] and local[2] is tools[2]
    assert "file_path" in local[1].inputSchema["properties"]
