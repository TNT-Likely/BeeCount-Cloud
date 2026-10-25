from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from test_mcp_attachments import ledger as ledger
from test_mcp_attachments import run
from test_mcp_complete import account

from src.mcp import (
    analysis_tools as analysis,
)
from src.mcp import (
    capability_tools as capabilities,
)
from src.mcp import (
    file_tools as files,
)
from src.mcp import rule_tools as rules
from src.mcp import workflow_tools as workflows
from src.mcp.schemas import BatchTransactionItem
from src.mcp.tools import read_tools as rt
from src.mcp.tools import write_tools as wt
from src.models import MCPStateRecord


def candidate(amount=10, source="source-1", **kwargs):
    return workflows.ImportCandidate(
        transaction=BatchTransactionItem(
            amount=amount, happened_at="2026-01-02T12:00:00+08:00", **kwargs
        ),
        sources=[workflows.SourceInput(source_system="synthetic-bank", source_id=source)],
    )


def test_import_preview_execution_source_links_and_safe_undo(ledger):
    plan = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    assert plan["status"] == "ready", plan
    assert rt.list_transactions(ledger.user)["total"] == 0
    outcome = run(
        workflows.execute_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True)
    )
    assert outcome["status"] == "completed", outcome
    sid = outcome["sync_ids"][0]
    assert (
        workflows.list_transaction_sources(ledger.user, ledger.ledger_id, sid)[0]["source_id"]
        == "source-1"
    )
    repeated = run(
        workflows.execute_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True)
    )
    assert repeated["operation_id"] == outcome["operation_id"]
    assert rt.list_transactions(ledger.user)["total"] == 1
    run(wt.update_transaction(ledger.user, sync_id=sid, note="later manual edit"))
    undo = run(workflows.undo_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True))
    assert undo["status"] == "conflict"
    assert rt.get_transaction(ledger.user, sid)["note"] == "later manual edit"
    with pytest.raises(ValueError, match="cannot be edited"):
        run(
            workflows.preview_import(
                ledger.user,
                ledger.ledger_id,
                [candidate(20)],
                plan_id=plan["id"],
                expected_revision=plan["revision"],
            )
        )


def test_import_undo_removes_only_created_entries_and_sources(ledger):
    plan = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    result = run(
        workflows.execute_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True)
    )
    sid = result["sync_ids"][0]
    undone = run(
        workflows.undo_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True)
    )
    assert undone["status"] == "completed", undone
    assert rt.get_transaction(ledger.user, sid) is None
    assert workflows.list_transaction_sources(ledger.user, ledger.ledger_id, sid) == []
    changes = capabilities.list_changes(ledger.user, ledger.ledger_id)
    assert any(item["sync_id"] == sid and item["action"] == "delete" for item in changes["items"])


def test_import_similarity_needs_review_and_exact_source_is_reused(ledger):
    original = run(
        wt.create_transaction(ledger.user, amount=10, happened_at="2026-01-02T12:00:00+08:00")
    )
    plan = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    assert plan["status"] == "needs_review"
    assert plan["data"]["items"][0]["status"] == "review_required"
    with pytest.raises(ValueError, match="unresolved"):
        run(workflows.execute_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True))
    workflows.link_transaction_source(
        ledger.user,
        ledger.ledger_id,
        original["sync_id"],
        workflows.SourceInput(source_system="synthetic-bank", source_id="source-1"),
    )
    repeated = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    assert repeated["data"]["items"][0]["status"] == "existing"
    outcome = run(
        workflows.execute_import_plan(
            ledger.user, repeated["id"], repeated["revision"], confirm=True
        )
    )
    assert outcome["status"] == "completed"
    assert outcome["sync_ids"] == []
    assert rt.list_transactions(ledger.user)["total"] == 1


def test_import_stale_preview_rejected_before_writes(ledger):
    plan = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    run(wt.create_transaction(ledger.user, amount=99))
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        run(workflows.execute_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True))
    assert rt.list_transactions(ledger.user)["total"] == 1


def test_record_revisions_reconciliation_and_user_isolation(ledger):
    aid = account(ledger, "Checkpoint cash")
    run(
        wt.create_transaction(
            ledger.user, amount=20, account_id=aid, happened_at="2026-01-01T12:00:00Z"
        )
    )
    result = workflows.reconcile_account(
        ledger.user, ledger.ledger_id, aid, "2026-01-02", 980, "UTC"
    )
    assert result["reconciled"] is True
    assert result["difference"] == 0
    saved = workflows.save_reconciliation_checkpoint(
        ledger.user,
        ledger.ledger_id,
        aid,
        "2026-01-02",
        980,
        "synthetic-bank",
        coverage_from="2026-01-01",
        time_zone="UTC",
        confirm=True,
    )
    assert saved["kind"] == "checkpoint"
    review = rules.save_review_item(
        ledger.user, ledger.ledger_id, rules.ReviewItem(question="Which receipt?")
    )
    changed = rules.save_review_item(
        ledger.user,
        ledger.ledger_id,
        rules.ReviewItem(question="Which receipt?", answer="Confirmed by user", status="answered"),
        record_id=review["id"],
        expected_revision=review["revision"],
    )
    assert changed["revision"] == 2
    with pytest.raises(ValueError, match="REVISION_CONFLICT"):
        rules.save_review_item(
            ledger.user,
            ledger.ledger_id,
            rules.ReviewItem(question="stale"),
            record_id=review["id"],
            expected_revision=review["revision"],
        )
    other, _, _ = ledger.register("stranger@example.com")
    with pytest.raises(ValueError, match="Ledger not found"):
        workflows.get_workflow_record(other, review["id"])


def test_csv_file_preview_export_original_bytes_and_permissions(ledger):
    category = run(
        capabilities.manage_category(
            ledger.user,
            action="create",
            ledger_id=ledger.ledger_id,
            data=capabilities.CategoryFields(name="Meals", kind="expense"),
        )
    )
    assert category["status"] == "completed"
    raw = b"Type,Amount,Time,Category,Note\nexpense,12,2026-01-02T12:00:00+08:00,Meals,synthetic\n"
    uploaded = run(
        wt.upload_attachment(
            ledger.user,
            ledger_id=ledger.ledger_id,
            file_name="synthetic.csv",
            mime_type="text/csv",
            content_base64=base64.b64encode(raw).decode(),
        )
    )
    metadata, read_raw = files.attachment_bytes(ledger.user, uploaded["file_id"])
    assert read_raw == raw
    preview = run(
        files.preview_import_file(
            ledger.user,
            uploaded["file_id"],
            mapping=files.ImportMapping(
                tx_type="Type",
                amount="Amount",
                happened_at="Time",
                category_name="Category",
                note="Note",
            ),
        )
    )
    assert preview["status"] == "ready", preview
    plan = preview["plan"]
    result = run(
        workflows.execute_import_plan(ledger.user, plan["id"], plan["revision"], confirm=True)
    )
    assert result["status"] == "completed"
    exported = files.export_transactions(ledger.user, ledger.ledger_id)
    assert exported["row_count"] == 1
    with ledger.sessions() as db:
        row = db.get(MCPStateRecord, exported["export_id"])
        assert "synthetic" in row.data_json["csv"]
        assert "currency_code" in row.data_json["csv"]
    other, _, _ = ledger.register("no-files@example.com")
    with pytest.raises(ValueError, match="Ledger not found"):
        files.attachment_bytes(other, uploaded["file_id"])


def test_composable_analysis_uses_local_dates_flags_and_comparison(ledger):
    run(
        wt.create_transactions(
            ledger.user,
            transactions=[
                dict(amount=10, happened_at="2025-12-31T20:00:00Z"),
                dict(amount=20, happened_at="2026-01-01T02:00:00Z"),
                dict(amount=99, happened_at="2026-01-01T02:00:00Z", exclude_from_stats=True),
            ],
        )
    )
    result = analysis.analyze_transactions(
        ledger.user,
        ledger.ledger_id,
        filters=analysis.TransactionFilter(date_from="2026-01-01", date_to="2026-01-01"),
        group_by="day",
        time_zone="Asia/Shanghai",
        comparison=analysis.TransactionFilter(date_from="2025-12-31", date_to="2025-12-31"),
    )
    assert result["totals"]["expense"] == 30
    assert result["totals"]["transaction_count"] == 2
    assert result["groups"][0]["key"] == "2026-01-01"
    assert result["comparison"]["totals"]["expense"] == 0


def test_month_end_recurrence_catchup_pause_and_retry_are_idempotent(ledger):
    schedule = rules.RecurringRule(
        name="Synthetic subscription",
        transaction=BatchTransactionItem(amount=7),
        frequency="monthly",
        starts_at="2026-01-31T09:00:00",
        time_zone="Asia/Shanghai",
        allow_backfill=True,
    )
    saved = run(
        rules.manage_recurring_rule(
            ledger.user, action="create", ledger_id=ledger.ledger_id, data=schedule, confirm=True
        )
    )
    assert saved["next_occurrences"][:3] == [
        "2026-01-31T09:00:00+08:00",
        "2026-02-28T09:00:00+08:00",
        "2026-03-31T09:00:00+08:00",
    ]
    due = datetime(2026, 2, 28, 2, tzinfo=timezone.utc)
    first = run(rules.execute_due_rules(now=due))
    second = run(rules.execute_due_rules(now=due))
    assert first[0]["operation"]["created_count"] == second[0]["operation"]["created_count"] == 1
    assert run(rules.execute_due_rules(now=due)) == []
    assert rt.list_transactions(ledger.user)["total"] == 2
    current = workflows.get_workflow_record(ledger.user, saved["id"])
    assert current["data"]["executed_count"] == 2
    run(
        rules.manage_recurring_rule(
            ledger.user,
            action="pause",
            ledger_id=ledger.ledger_id,
            record_id=saved["id"],
            expected_revision=current["revision"],
            confirm=True,
        )
    )
    assert run(rules.execute_due_rules(now=datetime(2026, 4, 1, tzinfo=timezone.utc))) == []


def test_invalid_recurring_rule_does_not_block_later_rules(ledger):
    deleted_account = account(ledger, "Account removed after scheduling")
    saved = []
    for day, transaction in [
        (1, BatchTransactionItem(amount=7, account_id=deleted_account)),
        (2, BatchTransactionItem(amount=9)),
    ]:
        saved.append(
            run(
                rules.manage_recurring_rule(
                    ledger.user,
                    action="create",
                    ledger_id=ledger.ledger_id,
                    data=rules.RecurringRule(
                        name=f"Independent schedule {day}",
                        transaction=transaction,
                        frequency="daily",
                        starts_at=f"2026-01-0{day}T00:00:00Z",
                        time_zone="UTC",
                        allow_backfill=True,
                    ),
                    confirm=True,
                )
            )
        )
    removed = run(
        capabilities.manage_account(
            ledger.user,
            action="delete",
            ledger_id=ledger.ledger_id,
            account_id=deleted_account,
            confirm=True,
        )
    )
    assert removed["status"] == "completed"
    outcomes = run(rules.execute_due_rules(now=datetime(2026, 1, 2, 1, tzinfo=timezone.utc)))
    by_rule = {item["rule_id"]: item for item in outcomes}
    assert by_rule[saved[0]["id"]]["status"] == "failed"
    assert by_rule[saved[0]["id"]]["error_code"] == "ValueError"
    assert by_rule[saved[1]["id"]]["operation"]["status"] == "created"
    assert rt.list_transactions(ledger.user)["total"] == 1
    assert rt.list_transactions(ledger.user)["items"][0]["amount"] == 9


def test_recurring_dst_gap_is_skipped_and_ambiguous_policy_is_explicit():
    data = dict(
        anchor_local="2026-03-07T02:30:00-05:00",
        rule=dict(frequency="daily", interval=1, time_zone="America/New_York", dst_policy="skip"),
    )
    assert rules.occurrence(data, 1) is None
    assert rules.occurrence(data, 2).hour == 6
    data = dict(
        anchor_local="2026-10-31T01:30:00-04:00",
        rule=dict(frequency="daily", interval=1, time_zone="America/New_York", dst_policy="first"),
    )
    first = rules.occurrence(data, 1)
    data["rule"]["dst_policy"] = "second"
    assert (rules.occurrence(data, 1) - first).total_seconds() == 3600


def test_classification_preview_freezes_versions_and_protects_user_edits(ledger):
    created_category = run(
        capabilities.manage_category(
            ledger.user,
            action="create",
            ledger_id=ledger.ledger_id,
            data=capabilities.CategoryFields(name="Synthetic coffee", kind="expense"),
        )
    )
    cid = created_category["sync_ids"][0]
    tx = run(
        wt.create_transaction(
            ledger.user, ledger_id=ledger.ledger_id, amount=9, note="Coffee receipt"
        )
    )
    saved = rules.save_classification_rule(
        ledger.user,
        ledger.ledger_id,
        rules.ClassificationRule(
            name="Coffee mapping", note_contains="Coffee", category_id=cid, confirmed=True
        ),
        confirm=True,
    )
    preview = run(rules.apply_classification_rule(ledger.user, saved["id"], saved["revision"]))
    assert preview["items"][0]["before"]["category_id"] is None
    run(wt.update_transaction(ledger.user, sync_id=tx["sync_id"], note="Manual correction"))
    attempted = run(
        rules.execute_classification_preview(ledger.user, preview["preview_id"], confirm=True)
    )
    assert attempted["status"] == "failed"
    assert attempted["error"]["code"] == "REVISION_CONFLICT"
    assert attempted["error"]["retryable"] is False
    assert rt.get_transaction(ledger.user, tx["sync_id"])["category_id"] is None


def test_reserved_sources_block_a_competing_import_plan(ledger, monkeypatch):
    first = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    second = run(workflows.preview_import(ledger.user, ledger.ledger_id, [candidate()]))
    original = wt._self_call

    async def unavailable(method, path, user, **kwargs):
        if path.endswith("/transactions/batch"):
            raise RuntimeError("synthetic service failure before writes")
        return await original(method, path, user, **kwargs)

    monkeypatch.setattr(wt, "_self_call", unavailable)
    failed = run(
        workflows.execute_import_plan(ledger.user, first["id"], first["revision"], confirm=True)
    )
    assert failed["status"] == "failed"
    with pytest.raises(ValueError, match="SOURCE_CONFLICT"):
        run(
            workflows.execute_import_plan(
                ledger.user, second["id"], second["revision"], confirm=True
            )
        )
    monkeypatch.setattr(wt, "_self_call", original)
    resumed = run(
        workflows.execute_import_plan(ledger.user, first["id"], first["revision"], confirm=True)
    )
    assert resumed["status"] == "completed"
    assert rt.list_transactions(ledger.user)["total"] == 1


def test_net_worth_keeps_currencies_separate_and_reports_missing_rates(ledger):
    account(ledger, "RMB cash", currency="CNY")
    account(ledger, "Dollar cash", currency="USD")
    history = capabilities.get_net_worth_history(ledger.user, base_currency="CNY")
    assert history["multi_currency"] is True
    assert history["missing_conversion_currencies"] == ["USD"]
    assert history["series"] == []
    assert history["series_by_currency"][-1]["by_currency"]["CNY"]["net_worth"] == 1000
    assert history["series_by_currency"][-1]["by_currency"]["USD"]["net_worth"] == 1000


def test_net_worth_credit_debt_and_overpayment_use_signed_balances(ledger):
    created = run(
        capabilities.manage_account(
            ledger.user,
            action="create",
            ledger_id=ledger.ledger_id,
            data=capabilities.AccountFields(
                name="Synthetic credit",
                account_type="credit_card",
                currency="CNY",
                initial_balance=-100,
            ),
        )
    )
    aid = created["sync_ids"][0]
    before = capabilities.get_net_worth_history(ledger.user)
    assert before["series"][-1]["liabilities"] == 100
    assert before["series"][-1]["net_worth"] == -100
    run(
        wt.create_transaction(
            ledger.user, ledger_id=ledger.ledger_id, amount=150, tx_type="income", account_id=aid
        )
    )
    after = capabilities.get_net_worth_history(ledger.user)
    assert after["series"][-1]["liabilities"] == 0
    assert after["series"][-1]["assets"] == 50


def test_shared_dictionary_reads_and_ai_context_use_ledger_owner(ledger):
    from src.models import Ledger, LedgerMember
    from src.routers.ai.parse_tx_image import _load_ledger_context

    aid = account(ledger, "Shared owner cash")
    member, _, _ = ledger.register("shared-dictionary@example.com")
    with ledger.sessions() as db:
        led = db.scalar(select(Ledger).where(Ledger.external_id == ledger.ledger_id))
        db.add(LedgerMember(ledger_id=led.id, user_id=member.id, role="editor"))
        db.commit()
        _, accounts, _ = _load_ledger_context(db, ledger.ledger_id, member.id)
    assert any(name == "Shared owner cash" for name, _ in accounts)
    assert any(item["id"] == aid for item in rt.list_accounts(member, ledger_id=ledger.ledger_id))
    assert not any(
        item["id"] == aid for item in capabilities.get_account_balances(member)["accounts"]
    )
    shared_history = capabilities.get_net_worth_history(member, ledger_id=ledger.ledger_id)
    assert shared_history["series"][-1]["net_worth"] == 1000
    assert shared_history["coverage"]["partial"] is True


def test_foreign_transfer_amounts_are_never_added_as_base_currency(ledger):
    source = account(ledger, "USD source", currency="USD")
    destination = account(ledger, "USD destination", currency="USD")
    result = run(
        wt.create_transaction(
            ledger.user,
            ledger_id=ledger.ledger_id,
            amount=5,
            tx_type="transfer",
            from_account_id=source,
            to_account_id=destination,
        )
    )
    assert result["status"] == "created"
    report = analysis.analyze_transactions(ledger.user, ledger.ledger_id)
    assert report["totals"]["transfer_by_currency"] == {"USD": 5}
    assert report["totals"]["transfer"] == 0
    assert report["totals"]["expense"] == 0


def test_recurring_lost_response_cannot_be_overwritten_by_rule_edit(ledger, monkeypatch):
    schedule = rules.RecurringRule(
        name="Lost response rule",
        transaction=BatchTransactionItem(amount=7),
        frequency="daily",
        starts_at="2026-01-01T00:00:00Z",
        time_zone="UTC",
        allow_backfill=True,
    )
    saved = run(
        rules.manage_recurring_rule(
            ledger.user, action="create", ledger_id=ledger.ledger_id, data=schedule, confirm=True
        )
    )
    original = wt._self_call

    async def lose_response(method, path, user, **kwargs):
        result = await original(method, path, user, **kwargs)
        if path.endswith("/transactions/batch"):
            raise RuntimeError("synthetic lost response after commit")
        return result

    monkeypatch.setattr(wt, "_self_call", lose_response)
    now = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
    failed = run(rules.execute_due_rules(now=now))
    assert failed[0]["operation"]["status"] == "failed"
    edited = schedule.model_copy(update={"transaction": BatchTransactionItem(amount=9)})
    blocked = run(
        rules.manage_recurring_rule(
            ledger.user,
            action="update",
            ledger_id=ledger.ledger_id,
            record_id=saved["id"],
            expected_revision=saved["revision"],
            data=edited,
            confirm=True,
        )
    )
    assert blocked["status"] == "operation_pending"
    monkeypatch.setattr(wt, "_self_call", original)
    resumed = run(rules.execute_due_rules(now=now))
    assert resumed[0]["operation"]["status"] == "created"
    assert rt.list_transactions(ledger.user)["total"] == 1
    assert rt.list_transactions(ledger.user)["items"][0]["amount"] == 7
