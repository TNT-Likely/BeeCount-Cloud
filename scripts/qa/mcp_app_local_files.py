#!/usr/bin/env python3
"""Extend the owned App QA snapshot with a real >680 KiB stdio receipt roundtrip."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import random
import shutil
import socket
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from mcp.client.stdio import stdio_client
from PIL import Image

from mcp import ClientSession, StdioServerParameters


def load(root_path):
    root = Path(root_path).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if root.name != manifest["run_id"] or (root / ".qa-owner").read_text() != root.name:
        raise ValueError("QA ownership mismatch")
    if not manifest["cloud_origin"].startswith("http://127.0.0.1:"):
        raise ValueError("Owned loopback origin required")
    return root, manifest


def build(args):
    root, manifest = load(args.run)
    spec = importlib.util.spec_from_file_location(
        "owned_app_qa", Path(args.app_repo) / "scripts/qa/isolated_app_cloud.py"
    )
    qa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qa)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}/receipt?run_id={manifest['run_id']}"
    snapshot = qa.snapshot

    def patched_snapshot(owned_root, owned_manifest):
        source = snapshot(owned_root, owned_manifest)
        target = source / "integration_test/mcp_receipt_attachments_live_test.dart"
        value = target.read_text()
        original = "    final c = await upload(imageC);"
        if value.count(original) != 1:
            raise ValueError("App QA harness changed; inspect before adapting")
        replacement = """    print('QA_STAGE_READY_LOCAL_STDIO');
    final localReceipt = (await tester.runAsync<Map<String, dynamic>>(() async {
      for (var attempt = 0; attempt < 240; attempt++) {
        final response = await http.get(Uri.parse(ENDPOINT));
        final value = jsonDecode(response.body) as Map<String, dynamic>;
        if (value['ready'] == true) return value;
        await Future<void>.delayed(const Duration(milliseconds: 500));
      }
      throw StateError('Owned local stdio fixture timed out');
    }))!;
    final c = Map<String, dynamic>.from(localReceipt['upload'] as Map);
""".replace("ENDPOINT", json.dumps(endpoint))
        value = value.replace(original, replacement)
        value = value.replace(
            "    final imageC = await receipt('Replacement C', const Color(0xffbee9d3));\n", ""
        )
        anchor = "    expect((await verify()).single['sha256'], c['sha256']);"
        value = value.replace(
            anchor,
            anchor
            + """
    final originalBytes = (await verify()).single;
    expect(originalBytes['size'], localReceipt['size_bytes']);
    expect(originalBytes['size'] as int, greaterThan(680 * 1024));
    expect(originalBytes['sha256'], localReceipt['sha256']);
    binding.reportData!['local_stdio_original'] = {
      'size_bytes': originalBytes['size'], 'sha256': originalBytes['sha256'],
      'cloudFileId': c['file_id']
    };
    pass('M10', 'Local stdio original receipt matches client, Cloud and App bytes/SHA without resizing');""",
        )
        target.write_text(value)
        owned_manifest.update(
            local_stdio_fixture_port=port,
            mcp_harness_sha256=hashlib.sha256(value.encode()).hexdigest(),
            mcp_harness_overlay="Owned QA test only; production App source unchanged",
        )
        qa.write_json(root / "manifest.json", owned_manifest)
        return source

    qa.snapshot = patched_snapshot
    qa.build(root, manifest, args.flutter)


async def serve(args):
    root, manifest = load(args.run)
    state = {"ready": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != "/receipt?run_id=" + manifest["run_id"]:
                self.send_error(404)
                return
            raw = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", manifest["local_stdio_fixture_port"]), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    credentials = json.loads((root / "credentials.json").read_text())
    database = root / "cloud-data/beecount.db"
    ledger_id = None
    for _ in range(600):
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            found = db.execute(
                "SELECT l.external_id FROM ledgers l JOIN users u ON u.id=l.user_id "
                "WHERE u.email=? AND l.name LIKE ?",
                (credentials["email"], "QA MCP小票 %"),
            ).fetchone()
        if found:
            ledger_id = found[0]
            break
        await asyncio.sleep(1)
    if not ledger_id:
        raise ValueError("App did not create its owned synthetic ledger")
    async with httpx.AsyncClient(base_url=manifest["cloud_origin"], timeout=120) as http:
        identity = (await http.get("/__qa__/identity")).json()
        if (
            identity["run_id"] != manifest["run_id"]
            or identity["cloud_sha"] != manifest["cloud_sha"]
        ):
            raise ValueError("QA server identity mismatch")
        auth = await http.post(
            "/api/v1/auth/login",
            json=dict(
                email=credentials["email"],
                password=credentials["password"],
                client_type="web",
                device_name="Owned stdio receipt QA",
                platform="web",
            ),
        )
        auth.raise_for_status()
        headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
        pat = await http.post(
            "/api/v1/profile/pats",
            headers=headers,
            json=dict(
                name="Owned local-file QA", scopes=["mcp:read", "mcp:write"], expires_in_days=1
            ),
        )
        pat.raise_for_status()
    fixtures = root / "local-file-fixtures"
    fixtures.mkdir(mode=0o700, exist_ok=True)
    picture = fixtures / "original-receipt.png"
    Image.frombytes("RGB", (600, 600), random.Random(20261025).randbytes(600 * 600 * 3)).save(
        picture, compress_level=0
    )
    original = picture.read_bytes()
    original_sha = hashlib.sha256(original).hexdigest()
    assert len(original) > 680 * 1024
    env = {"PATH": os.environ["PATH"], "BEECOUNT_MCP_PAT": pat.json()["token"]}
    command = StdioServerParameters(
        command=sys.executable,
        args=[
            str(root / "cloud-source/scripts/mcp_local_files.py"),
            "--endpoint",
            manifest["cloud_origin"] + "/api/v1/mcp",
            "--allow-dir",
            str(fixtures),
        ],
        env=env,
    )
    async with stdio_client(command) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            catalog = await session.list_tools()
            upload_tool = next(tool for tool in catalog.tools if tool.name == "upload_attachment")
            assert (
                "file_path" in upload_tool.inputSchema["properties"]
                and "content_base64" not in upload_tool.inputSchema["properties"]
            )
            uploaded = await session.call_tool(
                "upload_attachment", dict(file_path=str(picture), ledger_id=ledger_id)
            )
            assert not uploaded.isError
            info = uploaded.structuredContent or json.loads(uploaded.content[0].text)
            resource = await session.read_resource("beecount://attachments/" + info["file_id"])
            assert base64.b64decode(resource.contents[0].blob) == original
            assert len((await session.list_resource_templates()).resourceTemplates) == 2
            assert len((await session.list_prompts()).prompts) == 2
            empty = fixtures / "empty.png"
            empty.write_bytes(b"")
            outside = root / "outside-receipt.png"
            outside.write_bytes(b"outside")
            link = fixtures / "outside-link.png"
            link.symlink_to(outside)
            for bad in [empty, outside, link]:
                denied = await session.call_tool(
                    "upload_attachment", dict(file_path=str(bad), ledger_id=ledger_id)
                )
                assert denied.isError
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        storage_path, size, digest = db.execute(
            "SELECT storage_path,size_bytes,sha256 FROM attachment_files WHERE id=?",
            (info["file_id"],),
        ).fetchone()
    stored = Path(storage_path).resolve()
    assert stored.is_relative_to((root / "cloud-data/attachments").resolve())
    assert stored.read_bytes() == original and size == len(original) and digest == original_sha
    state.update(ready=True, upload=info, size_bytes=size, sha256=digest)
    evidence = dict(
        status="PASS",
        client_size_bytes=len(original),
        cloud_size_bytes=size,
        client_sha256=original_sha,
        cloud_sha256=digest,
        file_id=info["file_id"],
        rejected=["empty", "outside_allowed_directory", "symlink_escape"],
        resources_forwarded=True,
        prompts_forwarded=True,
    )
    (root / "evidence/local-stdio-protocol.json").write_text(json.dumps(evidence, indent=2))
    # Stay available until the App stage has consumed the fixture and written acceptance evidence.
    for _ in range(600):
        if (root / "evidence/acceptance.json").exists():
            break
        await asyncio.sleep(1)
    server.shutdown()
    print(json.dumps(dict(status="PASS", size_bytes=size, sha256=digest)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "serve"])
    parser.add_argument("--run", required=True)
    parser.add_argument("--app-repo")
    parser.add_argument("--flutter", default=shutil.which("flutter"))
    args = parser.parse_args()
    if args.action == "build":
        if not args.app_repo:
            parser.error("build requires --app-repo")
        build(args)
    else:
        asyncio.run(serve(args))
