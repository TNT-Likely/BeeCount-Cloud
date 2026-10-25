"""Business regression tests through production routers with isolated synthetic storage."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import select
from test_mcp_attachments import ledger as ledger
from test_mcp_attachments import run

from src.mcp import capability_tools as capabilities
from src.mcp import server
from src.mcp.tools import read_tools as rt
from src.mcp.tools import write_tools as wt
from src.models import Ledger, LedgerMember


def account(context, name, currency="CNY"):
    result = run(
        capabilities.manage_account(
            context.user,
            action="create",
            ledger_id=context.ledger_id,
            data=capabilities.AccountFields(name=name, currency=currency, initial_balance=1000),
        )
    )
    assert result["status"] == "completed", result
    return result["sync_ids"][0]


def test_management_idempotency_key_cannot_replay_another_tool(ledger):
    args = dict(
        action="create", ledger_id=ledger.ledger_id, idempotency_key="same-management-request"
    )
    created = run(
        capabilities.manage_account(
            ledger.user, data=capabilities.AccountFields(name="Same name"), **args
        )
    )
    replayed = run(
        capabilities.manage_account(
            ledger.user, data=capabilities.AccountFields(name="Same name"), **args
        )
    )
    assert created["status"] == "completed"
    assert replayed["operation_id"] == created["operation_id"]
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        run(
            capabilities.manage_tag(
                ledger.user, data=capabilities.TagFields(name="Same name"), **args
            )
        )
    assert not any(item["name"] == "Same name" for item in rt.list_tags(ledger.user))


def test_same_timestamp_pages_are_complete_and_revision_bound(ledger):
    for size in [200, 60]:
        result = run(
            wt.create_transactions(
                ledger.user,
                ledger_id=ledger.ledger_id,
                transactions=[
                    dict(amount=i + 1, happened_at="2026-01-01T00:00:00+08:00") for i in range(size)
                ],
            )
        )
        assert result["created_count"] == size, result
    cursor, ids = None, []
    while True:
        page = rt.list_transactions(
            ledger.user, ledger_id=ledger.ledger_id, limit=37, cursor=cursor
        )
        ids += [row["sync_id"] for row in page["items"]]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
    assert len(ids) == len(set(ids)) == 260
    first = rt.list_transactions(ledger.user, ledger_id=ledger.ledger_id, limit=5)
    with pytest.raises(ValueError, match="CURSOR_INVALIDATED"):
        rt.list_transactions(
            ledger.user,
            ledger_id=ledger.ledger_id,
            limit=5,
            cursor=first["next_cursor"],
            min_amount=1,
        )
    run(wt.create_transaction(ledger.user, ledger_id=ledger.ledger_id, amount=9))
    with pytest.raises(ValueError, match="CURSOR_INVALIDATED"):
        rt.list_transactions(ledger.user, ledger_id=ledger.ledger_id, cursor=first["next_cursor"])
    with pytest.raises(ValueError, match="Invalid continuation"):
        rt.list_transactions(ledger.user, ledger_id=ledger.ledger_id, cursor="tampered")


def test_lost_response_replays_original_write_and_rejects_key_reuse(ledger, monkeypatch):
    original = wt._self_call
    lost = False

    async def lose_response(method, path, user, **kwargs):
        nonlocal lost
        result = await original(method, path, user, **kwargs)
        if path.endswith("/transactions") and method == "POST" and not lost:
            lost = True
            raise RuntimeError("synthetic response loss after commit")
        return result

    monkeypatch.setattr(wt, "_self_call", lose_response)
    args = dict(ledger_id=ledger.ledger_id, amount=7, idempotency_key="stable-lost-response")
    first = run(wt.create_transaction(ledger.user, **args))
    assert first["status"] == "failed"
    assert rt.list_transactions(ledger.user)["total"] == 1
    second = run(wt.create_transaction(ledger.user, **args))
    assert second["status"] == "created", second
    assert rt.list_transactions(ledger.user)["total"] == 1
    assert first["operation_id"] == second["operation_id"]
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        run(wt.create_transaction(ledger.user, **{**args, "amount": 8}))


def test_partial_batch_retries_only_failed_chunk(ledger, monkeypatch):
    original = wt._self_call
    calls = 0

    async def fail_second(method, path, user, **kwargs):
        nonlocal calls
        if path.endswith("/transactions/batch"):
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic second chunk failure")
        return await original(method, path, user, **kwargs)

    monkeypatch.setattr(wt, "_self_call", fail_second)
    result = run(
        wt.create_transactions(
            ledger.user,
            ledger_id=ledger.ledger_id,
            idempotency_key="partial-51",
            transactions=[dict(amount=1) for _ in range(51)],
        )
    )
    assert result["status"] == "partial"
    assert result["created_count"] == 50
    resumed = run(capabilities.retry_operation(ledger.user, result["operation_id"]))
    assert resumed["status"] == "completed", resumed
    assert len(resumed["sync_ids"]) == 51
    assert calls == 3
    assert rt.list_transactions(ledger.user)["total"] == 51


def test_complete_transfer_balances_and_optimistic_revision(ledger):
    source, destination = account(ledger, "Source"), account(ledger, "Destination")
    result = run(
        wt.create_transaction(
            ledger.user,
            ledger_id=ledger.ledger_id,
            amount=200,
            tx_type="transfer",
            from_account_id=source,
            to_account_id=destination,
        )
    )
    assert result["status"] == "created", result
    tx = rt.get_transaction(ledger.user, result["sync_id"])
    assert (tx["from_account_id"], tx["to_account_id"]) == (source, destination)
    values = capabilities.get_account_balances(ledger.user)
    by_id = {row["account_id"]: row for row in values["accounts"]}
    assert by_id[source]["balance"] == 800
    assert by_id[destination]["balance"] == 1200
    summary = rt.get_analytics_summary(ledger.user, scope="all")
    assert summary["expense"] == summary["income"] == 0
    run(
        wt.update_transaction(
            ledger.user, sync_id=tx["sync_id"], note="first edit", expected_revision=tx["revision"]
        )
    )
    with pytest.raises(RuntimeError, match="REVISION_CONFLICT"):
        run(
            wt.update_transaction(
                ledger.user,
                sync_id=tx["sync_id"],
                note="stale edit",
                expected_revision=tx["revision"],
            )
        )
    assert rt.get_transaction(ledger.user, tx["sync_id"])["note"] == "first edit"
    with pytest.raises(ValueError, match="both"):
        run(
            wt.create_transaction(
                ledger.user, amount=10, tx_type="transfer", from_account_id=source
            )
        )
    with pytest.raises(ValueError, match="different"):
        run(
            wt.create_transaction(
                ledger.user,
                amount=10,
                tx_type="transfer",
                from_account_id=source,
                to_account_id=source,
            )
        )


def test_batch_flags_and_attachments_are_preserved(ledger):
    import base64

    file_id = run(
        wt.upload_attachment(
            ledger.user,
            ledger_id=ledger.ledger_id,
            file_name="synthetic.txt",
            content_base64=base64.b64encode(b"receipt").decode(),
        )
    )["file_id"]
    result = run(
        wt.create_transactions(
            ledger.user,
            ledger_id=ledger.ledger_id,
            transactions=[
                dict(
                    amount=11,
                    exclude_from_stats=True,
                    exclude_from_budget=True,
                    attachments=[file_id],
                )
            ],
        )
    )
    tx = rt.get_transaction(ledger.user, result["sync_ids"][0])
    assert tx["exclude_from_stats"] is True
    assert tx["exclude_from_budget"] is True
    assert tx["attachments"][0]["cloudFileId"] == file_id
    assert rt.get_analytics_summary(ledger.user, scope="all")["expense"] == 0


def test_member_can_read_shared_ledger_and_use_owner_accounts(ledger):
    owner_account = account(ledger, "Owner cash")
    member, _, _ = ledger.register("member@example.com")
    with ledger.sessions() as db:
        row = db.scalar(select(Ledger).where(Ledger.external_id == ledger.ledger_id))
        db.add(LedgerMember(ledger_id=row.id, user_id=member.id, role="editor"))
        db.commit()
    assert ledger.ledger_id in {row["id"] for row in rt.list_ledgers(member)}
    result = run(
        wt.create_transaction(
            member, ledger_id=ledger.ledger_id, amount=20, account_id=owner_account
        )
    )
    assert result["status"] == "created", result
    assert rt.get_transaction(member, result["sync_id"])["account_id"] == owner_account
    with pytest.raises(ValueError, match="role"):
        run(
            capabilities.manage_account(
                member,
                action="create",
                ledger_id=ledger.ledger_id,
                data=capabilities.AccountFields(name="unauthorized"),
            )
        )


def test_new_tools_have_typed_arguments_and_no_user_identity_parameter():
    for function in [*capabilities.READ_FUNCTIONS, *capabilities.WRITE_FUNCTIONS]:
        tool = server.mcp._tool_manager._tools[function.__name__]
        assert "user" not in tool.parameters.get("properties", {})
        assert "ctx" not in tool.parameters.get("properties", {})
        assert "kwargs" not in tool.parameters.get("properties", {})
        assert tool.annotations.readOnlyHint is (function in capabilities.READ_FUNCTIONS)
    definition = server.mcp._tool_manager._tools["manage_account"].parameters
    assert definition["$defs"]["AccountFields"]["additionalProperties"] is False


def test_crud_ledger_dictionary_budget_and_replay(ledger):
    created = run(
        capabilities.manage_ledger(
            ledger.user,
            action="create",
            ledger_id="synthetic-second-ledger",
            data=capabilities.LedgerFields(ledger_name="Second"),
            idempotency_key="ledger-create-stable",
        )
    )
    repeated = run(
        capabilities.manage_ledger(
            ledger.user,
            action="create",
            ledger_id="synthetic-second-ledger",
            data=capabilities.LedgerFields(ledger_name="Second"),
            idempotency_key="ledger-create-stable",
        )
    )
    assert created["entity_id"] == repeated["entity_id"]
    assert repeated["idempotency_replayed"] is True
    changed = run(
        capabilities.manage_ledger(
            ledger.user,
            action="update",
            ledger_id=ledger.ledger_id,
            data=capabilities.LedgerFields(ledger_name="Renamed", month_start_day=15),
        )
    )
    assert changed["status"] == "completed", changed
    created_tag = run(
        capabilities.manage_tag(
            ledger.user,
            action="create",
            ledger_id=ledger.ledger_id,
            data=capabilities.TagFields(name="Synthetic tag"),
        )
    )
    tag_id = created_tag["sync_ids"][0]
    changed_tag = run(
        capabilities.manage_tag(
            ledger.user,
            action="update",
            ledger_id=ledger.ledger_id,
            tag_id=tag_id,
            data=capabilities.TagFields(name="Renamed tag"),
        )
    )
    assert changed_tag["status"] == "completed"
    assert any(
        item["id"] == tag_id and item["name"] == "Renamed tag" for item in rt.list_tags(ledger.user)
    )
    budget = run(
        capabilities.manage_budget(
            ledger.user,
            action="create",
            ledger_id=ledger.ledger_id,
            data=capabilities.BudgetFields(type="total", amount=100),
        )
    )
    assert budget["status"] == "completed", budget
    assert rt.list_budgets(ledger.user, ledger_id=ledger.ledger_id)[0]["amount"] == 100
    no_delete = run(
        capabilities.manage_tag(
            ledger.user, action="delete", ledger_id=ledger.ledger_id, tag_id=tag_id
        )
    )
    assert no_delete["status"] == "confirmation_required"
    deleted = run(
        capabilities.manage_tag(
            ledger.user, action="delete", ledger_id=ledger.ledger_id, tag_id=tag_id, confirm=True
        )
    )
    assert deleted["status"] == "completed"


def test_typed_adapter_authenticates_identity_and_enforces_readonly_pat(ledger):
    from types import SimpleNamespace

    context = SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(
                scope={
                    "bc_mcp_user": ledger.user,
                    "bc_mcp_scopes": {"mcp:read"},
                }
            )
        )
    )
    context_tool = server.mcp._tool_manager._tools["get_bookkeeping_context"]
    result = run(context_tool.run({"ledger_id": ledger.ledger_id}, context=context))
    assert result["selected_ledger_id"] == ledger.ledger_id
    assert result["pat_scopes"] == ["mcp:read"]
    with pytest.raises(Exception, match="mcp:write"):
        run(
            server.mcp._tool_manager._tools["manage_account"].run(
                {
                    "ledger_id": ledger.ledger_id,
                    "action": "create",
                    "data": {"name": "Forbidden"},
                },
                context=context,
            )
        )
    assert not any(item["name"] == "Forbidden" for item in rt.list_accounts(ledger.user))
    for value in [True, float("inf"), float("nan"), -1, 0]:
        with pytest.raises(ToolError):
            run(
                server.mcp._tool_manager._tools["create_transaction"].run(
                    {
                        "ledger_id": ledger.ledger_id,
                        "amount": value,
                    },
                    context=context,
                )
            )
