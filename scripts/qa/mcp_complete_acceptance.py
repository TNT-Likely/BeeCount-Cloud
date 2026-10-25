#!/usr/bin/env python3
"""Live MCP acceptance; refuses endpoints without this run's loopback QA identity."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp.client.streamable_http import streamablehttp_client

from mcp import ClientSession


def read_result(result):
    if result.isError:
        raise AssertionError("MCP tool returned an error; inspect private server logs")
    if result.structuredContent is not None:
        data = result.structuredContent
        return data["result"] if set(data) == {"result"} else data
    return json.loads(next(item.text for item in result.content if item.type == "text"))


async def acceptance(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if (root / ".qa-owner").read_text() != manifest["run_id"] or root.name != manifest["run_id"]:
        raise ValueError("QA ownership mismatch")
    origin = manifest["cloud_origin"]
    if urlparse(origin).hostname != "127.0.0.1":
        raise ValueError("Only the owned loopback QA server is allowed")
    async with httpx.AsyncClient(base_url=origin, timeout=120) as http:
        identity = (await http.get("/__qa__/identity")).json()
        if (
            identity.get("run_id") != manifest["run_id"]
            or identity.get("cloud_sha") != manifest["cloud_sha"]
            or not identity.get("database_is_isolated")
        ):
            raise ValueError("QA server identity mismatch")
        password = secrets.token_urlsafe(32)
        email = f"mcp-complete-{secrets.token_hex(5)}@qa.example.com"
        registered = await http.post(
            "/api/v1/auth/register",
            json=dict(
                email=email,
                password=password,
                client_type="web",
                device_name="MCP complete QA",
                platform="web",
            ),
        )
        registered.raise_for_status()
        auth = registered.json()
        headers = {
            "Authorization": "Bearer " + auth["access_token"],
            "X-Device-ID": auth["device_id"],
        }
        lid = "qa-mcp-complete-" + secrets.token_hex(5)
        response = await http.post(
            "/api/v1/write/ledgers",
            headers=headers,
            json=dict(ledger_id=lid, ledger_name="QA MCP 完整能力", currency="CNY"),
        )
        response.raise_for_status()
        tokens = {}
        for name, scopes in [("write", ["mcp:read", "mcp:write"]), ("read", ["mcp:read"])]:
            response = await http.post(
                "/api/v1/profile/pats",
                headers=headers,
                json=dict(name="QA MCP complete " + name, scopes=scopes, expires_in_days=1),
            )
            response.raise_for_status()
            tokens[name] = response.json()["token"]
        private = root / "mcp-complete-credentials.json"
        private.write_text(
            json.dumps(dict(email=email, password=password, ledger_id=lid, **tokens))
        )
        private.chmod(0o600)
        evidence = dict(run_id=manifest["run_id"], cloud_sha=manifest["cloud_sha"], cases=[])

        def passed(name, **facts):
            evidence["cases"].append(dict(id=name, status="PASS", **facts))

        async with streamablehttp_client(
            origin + "/api/v1/mcp", headers={"Authorization": "Bearer " + tokens["write"]}
        ) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                catalog = await session.list_tools()
                assert len(catalog.tools) == 55
                assert all(
                    "user" not in item.inputSchema.get("properties", {}) for item in catalog.tools
                )
                passed(
                    "live-handshake-and-schema",
                    tool_count=len(catalog.tools),
                    protocol=initialized.protocolVersion,
                )

                async def call(name, **kwargs):
                    return read_result(await session.call_tool(name, kwargs))

                context = await call("get_bookkeeping_context", ledger_id=lid)
                assert context["selected_ledger_id"] == lid
                assert set(context["pat_scopes"]) == {"mcp:read", "mcp:write"}
                passed("live-context-and-scopes")
                account_ids = []
                for name in ["QA MCP Cash A", "QA MCP Cash B"]:
                    result = await call(
                        "manage_account",
                        action="create",
                        ledger_id=lid,
                        data=dict(name=name, currency="CNY", initial_balance=1000),
                    )
                    assert result["status"] == "completed"
                    account_ids.append(result["sync_ids"][0])
                transfer_args = dict(
                    ledger_id=lid,
                    tx_type="transfer",
                    amount=200,
                    from_account_id=account_ids[0],
                    to_account_id=account_ids[1],
                    idempotency_key="qa-complete-transfer",
                )
                transfer = await call("create_transaction", **transfer_args)
                replay = await call("create_transaction", **transfer_args)
                assert transfer["sync_id"] == replay["sync_id"]
                balances = await call("get_account_balances")
                by_id = {item["account_id"]: item for item in balances["accounts"]}
                assert (
                    by_id[account_ids[0]]["balance"] == 800
                    and by_id[account_ids[1]]["balance"] == 1200
                )
                passed("live-complete-transfer-and-idempotency", transaction_count=1)
                bulk = await call(
                    "create_transactions",
                    ledger_id=lid,
                    idempotency_key="qa-complete-bulk",
                    transactions=[
                        dict(amount=1, happened_at="2026-01-01T12:00:00+08:00") for _ in range(51)
                    ],
                )
                assert bulk["created_count"] == 51
                cursor, identifiers = None, set()
                while True:
                    page = await call(
                        "list_transactions",
                        ledger_id=lid,
                        limit=13,
                        **({"cursor": cursor} if cursor else {}),
                    )
                    identifiers.update(item["sync_id"] for item in page["items"])
                    if not page["has_more"]:
                        break
                    cursor = page["next_cursor"]
                assert len(identifiers) == 52
                passed("live-batch-and-cursor", transaction_count=len(identifiers))
                original = await call("get_transaction", ledger_id=lid, sync_id=transfer["sync_id"])
                await call(
                    "update_transaction",
                    ledger_id=lid,
                    sync_id=transfer["sync_id"],
                    note="QA MCP edited",
                    expected_revision=original["revision"],
                )
                conflict = await session.call_tool(
                    "update_transaction",
                    dict(
                        ledger_id=lid,
                        sync_id=transfer["sync_id"],
                        note="stale",
                        expected_revision=original["revision"],
                    ),
                )
                assert conflict.isError
                passed("live-optimistic-concurrency")
                raw = b"original synthetic attachment bytes\n"
                uploaded = await call(
                    "upload_attachment",
                    ledger_id=lid,
                    file_name="qa-original.txt",
                    mime_type="text/plain",
                    content_base64=base64.b64encode(raw).decode(),
                )
                resource = await session.read_resource(
                    "beecount://attachments/" + uploaded["file_id"]
                )
                assert base64.b64decode(resource.contents[0].blob) == raw
                passed("live-original-attachment-resource", size_bytes=len(raw))
                plan = await call(
                    "preview_import",
                    ledger_id=lid,
                    candidates=[
                        dict(
                            transaction=dict(
                                amount=17.25,
                                happened_at="2026-02-03T10:00:00+08:00",
                                account_id=account_ids[0],
                            ),
                            sources=[dict(source_system="qa-source", source_id="order-unique-1")],
                        )
                    ],
                )
                assert plan["status"] == "ready"
                imported = await call(
                    "execute_import_plan",
                    plan_id=plan["id"],
                    expected_revision=plan["revision"],
                    confirm=True,
                )
                assert imported["status"] == "completed"
                refs = await call(
                    "list_transaction_sources", ledger_id=lid, sync_id=imported["sync_ids"][0]
                )
                assert refs[0]["source_id"] == "order-unique-1"
                passed("live-import-preview-and-provenance")
                exported = await call("export_transactions", ledger_id=lid)
                csv_resource = await session.read_resource(exported["resource_uri"])
                assert (
                    exported["row_count"] == 53 and "native_amount" in csv_resource.contents[0].text
                )
                passed("live-csv-export-resource", row_count=exported["row_count"])
                analysis = await call(
                    "analyze_transactions",
                    ledger_id=lid,
                    group_by="month",
                    time_zone="Asia/Shanghai",
                )
                assert analysis["totals"]["expense"] == 68.25 and analysis["totals"]["income"] == 0
                checkpoint = await call(
                    "save_reconciliation_checkpoint",
                    ledger_id=lid,
                    account_id=account_ids[0],
                    at=datetime.now(timezone.utc).isoformat(),
                    actual_balance=782.75,
                    source_system="qa-source",
                    confirm=True,
                )
                assert checkpoint["data"]["reconciled"] is True
                passed(
                    "live-analysis-and-reconciliation",
                    expense=68.25,
                    difference=checkpoint["data"]["difference"],
                )
                review = await call(
                    "save_review_item", ledger_id=lid, data=dict(question="QA synthetic review")
                )
                assert (await call("get_workflow_record", record_id=review["id"]))["data"][
                    "question"
                ] == "QA synthetic review"
                passed("live-agent-handoff-state")
                due = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat()
                scheduled = await call(
                    "manage_recurring_rule",
                    action="create",
                    ledger_id=lid,
                    data=dict(
                        name="QA due once",
                        transaction=dict(amount=2, note="QA scheduler occurrence"),
                        frequency="daily",
                        starts_at=due,
                        time_zone="Asia/Shanghai",
                        allow_backfill=True,
                    ),
                    confirm=True,
                )
                # Exercise the actual background scheduler, not its Python helper.
                for _ in range(45):
                    current = await call("get_workflow_record", record_id=scheduled["id"])
                    if current["data"].get("executed_count") == 1:
                        break
                    await asyncio.sleep(1)
                assert current["data"].get("executed_count") == 1
                await call("run_due_recurring_rules", ledger_id=lid)
                after = await call("get_workflow_record", record_id=scheduled["id"])
                assert after["data"]["executed_count"] == 1
                await call(
                    "manage_recurring_rule",
                    action="pause",
                    ledger_id=lid,
                    record_id=scheduled["id"],
                    expected_revision=after["revision"],
                    confirm=True,
                )
                passed("live-background-scheduler-idempotency", generated_count=1)
                prompts = await session.list_prompts()
                assert {"monthly_review", "import_statement"} <= {
                    prompt.name for prompt in prompts.prompts
                }
                passed("live-prompts-and-resource-discovery")
        async with streamablehttp_client(
            origin + "/api/v1/mcp", headers={"Authorization": "Bearer " + tokens["read"]}
        ) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                denied = await session.call_tool(
                    "manage_account",
                    dict(action="create", ledger_id=lid, data=dict(name="Forbidden")),
                )
                assert denied.isError
                result = read_result(
                    await session.call_tool("get_bookkeeping_context", dict(ledger_id=lid))
                )
                assert result["pat_scopes"] == ["mcp:read"]
                passed("live-readonly-pat-denies-writes")
        evidence["status"] = "PASS"
        output = root / "evidence/mcp-complete-protocol.json"
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                dict(status="PASS", cases=len(evidence["cases"]), report=str(output)),
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    asyncio.run(acceptance(args.run))
