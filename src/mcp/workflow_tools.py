"""Versioned import, provenance and reconciliation workflows backed by Cloud storage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..models import (
    AuditLog,
    Ledger,
    MCPSourceReference,
    MCPStateRecord,
    ReadTxProjection,
    SyncChange,
    User,
)
from .capability_tools import Fields, get_account_balances, require_ledger, run_write
from .query_context import date_boundary, revision
from .schemas import BatchTransactionItem
from .tools import read_tools as rt
from .tools import write_tools as wt
from .transaction_service import prepare_transaction


class SourceInput(Fields):
    source_system: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=255)
    order_id: str | None = Field(default=None, max_length=255)
    file_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    row_number: int | None = Field(default=None, ge=1)


class ImportCandidate(Fields):
    transaction: BatchTransactionItem
    sources: list[SourceInput] = Field(default_factory=list, max_length=20)
    action: Literal["create", "link", "skip"] = "create"
    existing_sync_id: str | None = None
    allow_similar: bool = False


def record_out(row, *, include_data=True):
    return dict(
        id=row.id,
        ledger_internal_id=row.ledger_id,
        kind=row.kind,
        revision=row.revision,
        status=row.status,
        data=row.data_json if include_data else None,
        next_due_at=row.next_due_at.replace(tzinfo=timezone.utc).isoformat()
        if row.next_due_at
        else None,
        created_at=row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        updated_at=row.updated_at.replace(tzinfo=timezone.utc).isoformat(),
    )


def load_record(db, user, record_id, *, kind=None, write=False):
    row = db.get(MCPStateRecord, record_id)
    if row is None or (kind and row.kind != kind):
        raise ValueError("Record not found")
    ledger = db.get(Ledger, row.ledger_id)
    if not ledger:
        raise ValueError("Record not found")
    require_ledger(db, user, ledger.external_id, write=write)
    return row, ledger


def save_record(
    user,
    ledger,
    *,
    kind,
    data,
    record_id=None,
    expected_revision=None,
    status="open",
    next_due_at=None,
):
    with wt.SessionLocal() as db:
        require_ledger(db, user, ledger.external_id, write=True)
        before = None
        if record_id:
            row, _ = load_record(db, user, record_id, kind=kind, write=True)
            if row.ledger_id != ledger.id or expected_revision is None:
                raise ValueError("Updates require the matching ledger and expected_revision")
            before = record_out(row)
            if kind == "import_plan" and (
                row.user_id != user.id or row.status in {"executing", "completed", "undone"}
            ):
                raise ValueError(
                    "An executing/completed import plan cannot be edited; create a new preview"
                )
            changed = db.execute(
                update(MCPStateRecord)
                .where(MCPStateRecord.id == record_id, MCPStateRecord.revision == expected_revision)
                .values(
                    data_json=data,
                    status=status,
                    revision=expected_revision + 1,
                    updated_at=datetime.now(timezone.utc),
                    next_due_at=next_due_at,
                )
            )
            if not changed.rowcount:
                raise ValueError("REVISION_CONFLICT: workflow record changed")
            db.expire_all()
            row = db.get(MCPStateRecord, record_id)
        else:
            row = MCPStateRecord(
                id=str(uuid4()),
                user_id=user.id,
                ledger_id=ledger.id,
                kind=kind,
                data_json=data,
                revision=1,
                status=status,
                next_due_at=next_due_at,
            )
            db.add(row)
            db.flush()
        out = record_out(row)
        db.add(
            AuditLog(
                user_id=user.id,
                ledger_id=ledger.id,
                action="mcp_workflow_" + kind,
                metadata_json=dict(record_id=row.id, before=before, after=out),
            )
        )
        db.commit()
        return out


def list_workflow_records(
    user: User,
    ledger_id: str,
    kind: Literal[
        "import_plan",
        "checkpoint",
        "review_item",
        "classification_rule",
        "recurring_rule",
        "export",
    ],
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Read persistent workflow progress across computers/Agents. Results are ledger scoped and include optimistic revisions; paginate with offset."""
    with rt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id)
        query = select(MCPStateRecord).where(
            MCPStateRecord.ledger_id == ledger.id, MCPStateRecord.kind == kind
        )
        if status:
            query = query.where(MCPStateRecord.status == status)
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(
            query.order_by(MCPStateRecord.created_at.desc(), MCPStateRecord.id)
            .offset(max(0, offset))
            .limit(max(1, min(limit, 100)))
        ).all()
        return dict(items=[record_out(row) for row in rows], total=total)


def get_workflow_record(user: User, record_id: str) -> dict[str, Any]:
    """Read a saved import/reconciliation/rule/review record by ID, after checking current ledger membership."""
    with rt.SessionLocal() as db:
        row, ledger = load_record(db, user, record_id)
        return {**record_out(row), "ledger_id": ledger.external_id}


def _source_out(row):
    return dict(
        id=row.id,
        sync_id=row.tx_sync_id,
        source_system=row.source_system,
        source_id=row.source_id,
        metadata=row.metadata_json,
    )


def _link_source(db, user, ledger, sync_id, source, import_plan_id=None):
    tx = db.get(ReadTxProjection, (ledger.id, sync_id))
    if not tx:
        raise ValueError("Source target transaction not found")
    existing = db.scalar(
        select(MCPSourceReference).where(
            MCPSourceReference.ledger_id == ledger.id,
            MCPSourceReference.source_system == source.source_system,
            MCPSourceReference.source_id == source.source_id,
        )
    )
    if existing:
        metadata = existing.metadata_json or {}
        if (
            import_plan_id
            and metadata.get("_reserved")
            and metadata.get("import_plan_id") == import_plan_id
        ):
            existing.tx_sync_id = sync_id
            existing.metadata_json = {
                key: value for key, value in metadata.items() if key != "_reserved"
            }
            db.flush()
            return _source_out(existing)
        if existing.tx_sync_id != sync_id:
            raise ValueError(
                "SOURCE_CONFLICT: external identity already belongs to another transaction"
            )
        return _source_out(existing)
    row = MCPSourceReference(
        id=str(uuid4()),
        ledger_id=ledger.id,
        tx_sync_id=sync_id,
        source_system=source.source_system,
        source_id=source.source_id,
        metadata_json=source.model_dump(exclude={"source_system", "source_id"}, exclude_none=True),
        created_by_user_id=user.id,
    )
    db.add(row)
    db.flush()
    db.add(
        AuditLog(
            user_id=user.id,
            ledger_id=ledger.id,
            action="mcp_source_link",
            metadata_json=dict(reference_id=row.id, sync_id=sync_id),
        )
    )
    return _source_out(row)


def link_transaction_source(
    user: User, ledger_id: str, sync_id: str, source: SourceInput
) -> dict[str, Any]:
    """Attach another source receipt/order/bank entry to an existing transaction. A source identity can refer to only one transaction within a ledger."""
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        try:
            result = _link_source(db, user, ledger, sync_id, source)
            db.commit()
            return result
        except IntegrityError as exc:
            db.rollback()
            raise ValueError(
                "SOURCE_CONFLICT: source was linked concurrently; read it before retrying"
            ) from exc


def list_transaction_sources(user: User, ledger_id: str, sync_id: str) -> list[dict[str, Any]]:
    """Read all source identities proving a transaction, including source-file hashes/row numbers."""
    with rt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id)
        return [
            _source_out(row)
            for row in db.scalars(
                select(MCPSourceReference).where(
                    MCPSourceReference.ledger_id == ledger.id,
                    MCPSourceReference.tx_sync_id == sync_id,
                )
            ).all()
        ]


async def preview_import(
    user: User,
    ledger_id: str,
    candidates: list[ImportCandidate],
    time_zone: str | None = None,
    plan_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Persist a versioned dry-run of 1..200 imports. Exact source IDs identify existing entries; same-day/same-amount matches are only review candidates. No transactions are written."""
    if not 1 <= len(candidates) <= 200:
        raise ValueError(
            "Import previews accept 1..200 candidates; split larger source files into plans"
        )
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        base_revision = revision(db, [ledger.id], ledger.user_id)
        db.expunge(ledger)
    preview, errors, seen_sources = [], [], set()
    balance_effects = {}
    for index, candidate in enumerate(candidates):
        item = candidate.model_dump(mode="json")
        item.update(
            index=index, status="skip" if candidate.action == "skip" else "ready", similar=[]
        )
        if candidate.action == "skip":
            preview.append(item)
            continue
        with wt.SessionLocal() as db:
            known = []
            for source in candidate.sources:
                identity = (source.source_system, source.source_id)
                if identity in seen_sources:
                    errors.append(dict(index=index, code="DUPLICATE_SOURCE_IN_PLAN"))
                seen_sources.add(identity)
                ref = db.scalar(
                    select(MCPSourceReference).where(
                        MCPSourceReference.ledger_id == ledger.id,
                        MCPSourceReference.source_system == source.source_system,
                        MCPSourceReference.source_id == source.source_id,
                    )
                )
                if ref:
                    known.append(ref.tx_sync_id)
            if known:
                if len(set(known)) != 1 or (
                    candidate.existing_sync_id and candidate.existing_sync_id != known[0]
                ):
                    errors.append(dict(index=index, code="SOURCE_CONFLICT"))
                elif db.get(ReadTxProjection, (ledger.id, known[0])) is None:
                    errors.append(dict(index=index, code="SOURCE_TARGET_DELETED"))
                else:
                    item.update(status="existing", existing_sync_id=known[0])
            if candidate.action == "link":
                if (
                    not candidate.existing_sync_id
                    or db.get(ReadTxProjection, (ledger.id, candidate.existing_sync_id)) is None
                ):
                    errors.append(dict(index=index, code="LINK_TARGET_NOT_FOUND"))
                else:
                    item.update(status="link", existing_sync_id=candidate.existing_sync_id)
        try:
            body = await prepare_transaction(
                user, ledger, candidate.transaction.model_dump(), time_zone=time_zone
            )
            item["normalized"] = body
            if item["status"] == "ready":
                stamp = datetime.fromisoformat(body["happened_at"])
                with wt.SessionLocal() as db:
                    matches = db.scalars(
                        select(ReadTxProjection)
                        .where(
                            ReadTxProjection.ledger_id == ledger.id,
                            ReadTxProjection.tx_type == body["tx_type"],
                            ReadTxProjection.amount == body["amount"],
                            ReadTxProjection.happened_at >= stamp - timedelta(days=1),
                            ReadTxProjection.happened_at <= stamp + timedelta(days=1),
                        )
                        .limit(10)
                    ).all()
                    item["similar"] = [rt._serialize_tx(row, row.category_name) for row in matches]
                if item["similar"] and not candidate.allow_similar:
                    item["status"] = "review_required"
                amount = body["amount"]
                effects = (
                    [(body.get("from_account_id"), -amount), (body.get("to_account_id"), amount)]
                    if body["tx_type"] == "transfer"
                    else [
                        (body.get("account_id"), amount if body["tx_type"] == "income" else -amount)
                    ]
                )
                for account_id, delta in effects:
                    if account_id:
                        balance_effects[account_id] = balance_effects.get(account_id, 0) + delta
        except ValueError as exc:
            item["status"] = "invalid"
            errors.append(dict(index=index, code="INVALID_TRANSACTION", message=str(exc)))
        preview.append(item)
    with wt.SessionLocal() as db:
        if revision(db, [ledger.id], ledger.user_id) != base_revision:
            raise ValueError("REVISION_CONFLICT: data changed during preview; retry preview")
    ready = not errors and all(item["status"] != "review_required" for item in preview)
    data = dict(
        base_revision=base_revision,
        items=preview,
        errors=errors,
        balance_effects=balance_effects,
        time_zone=time_zone,
        ready=ready,
    )
    result = save_record(
        user,
        ledger,
        kind="import_plan",
        data=data,
        record_id=plan_id,
        expected_revision=expected_revision,
        status="ready" if ready else "needs_review",
    )
    return result


async def execute_import_plan(
    user: User, plan_id: str, expected_revision: int, confirm: bool = False
) -> dict[str, Any]:
    """Execute the exact saved import preview after user confirmation. Restart/response-loss retries reuse the original operation; source links are persisted idempotently."""
    if not confirm:
        return dict(status="confirmation_required", plan_id=plan_id)
    with wt.SessionLocal() as db:
        row, ledger = load_record(db, user, plan_id, kind="import_plan", write=True)
        if row.user_id != user.id:
            raise ValueError("Only the preview creator can execute its import operation")
        if row.revision != expected_revision:
            raise ValueError("REVISION_CONFLICT: import plan version changed")
        data = dict(row.data_json)
        if not data.get("ready"):
            raise ValueError("Import plan contains unresolved errors or similar transactions")
        if row.status == "undone":
            raise ValueError("Import plan was undone; create a new preview")
        from .operations import find_operation

        operation = find_operation(
            wt.SessionLocal,
            user.id,
            "import:" + plan_id,
            dict(plan_id=plan_id, plan_revision=expected_revision),
            kind="import",
            ledger_id=ledger.id,
        )
        if not operation and revision(db, [ledger.id], ledger.user_id) != data["base_revision"]:
            raise ValueError("REVISION_CONFLICT: ledger changed after preview; refresh the plan")
        # Reserve source identities before any transaction writes. Unique constraints
        # prevent two plans from importing the same external identity concurrently.
        try:
            for item in data["items"]:
                if item["status"] == "skip":
                    continue
                for raw_source in item["sources"]:
                    source = SourceInput.model_validate(raw_source)
                    reference = db.scalar(
                        select(MCPSourceReference).where(
                            MCPSourceReference.ledger_id == ledger.id,
                            MCPSourceReference.source_system == source.source_system,
                            MCPSourceReference.source_id == source.source_id,
                        )
                    )
                    if reference:
                        same_reservation = (reference.metadata_json or {}).get(
                            "import_plan_id"
                        ) == plan_id
                        same_target = item.get("existing_sync_id") == reference.tx_sync_id
                        if not same_reservation and not same_target:
                            raise ValueError(
                                "SOURCE_CONFLICT: external identity changed after preview"
                            )
                    elif item["status"] == "ready":
                        db.add(
                            MCPSourceReference(
                                id=str(uuid4()),
                                ledger_id=ledger.id,
                                tx_sync_id=f"pending:{plan_id}:{item['index']}",
                                source_system=source.source_system,
                                source_id=source.source_id,
                                metadata_json={
                                    **source.model_dump(
                                        exclude={"source_system", "source_id"}, exclude_none=True
                                    ),
                                    "import_plan_id": plan_id,
                                    "_reserved": True,
                                },
                                created_by_user_id=user.id,
                            )
                        )
                    else:
                        _link_source(db, user, ledger, item["existing_sync_id"], source)
            row.status = "executing"
            row.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(ledger)
        except IntegrityError as exc:
            db.rollback()
            raise ValueError(
                "SOURCE_CONFLICT: another import reserved this source; refresh preview"
            ) from exc
        db.expunge(ledger)
    creates = [item for item in data["items"] if item["status"] == "ready"]
    path = f"{get_settings().api_prefix}/write/ledgers/{ledger.external_id}/transactions/batch"
    bodies = [
        {k: v for k, v in item["normalized"].items() if k != "base_change_id"} for item in creates
    ]
    steps = [
        dict(
            method="POST",
            path=path,
            body=dict(base_change_id=0, auto_ai_tag=False, transactions=bodies[start : start + 50]),
        )
        for start in range(0, len(bodies), 50)
    ]
    outcome = await run_write(
        user,
        ledger,
        kind="import",
        request=dict(plan_id=plan_id, plan_revision=expected_revision),
        steps=steps,
        idempotency_key="import:" + plan_id,
    )
    if outcome["status"] != "completed":
        return outcome
    created_map = dict(zip((item["index"] for item in creates), outcome["sync_ids"], strict=True))
    created_revisions = {}
    with wt.SessionLocal() as db:
        row, _ = load_record(db, user, plan_id, kind="import_plan", write=True)
        if row.revision != expected_revision:
            raise ValueError(
                "REVISION_CONFLICT: plan changed while executing; operation is retained"
            )
        for item in data["items"]:
            sync_id = created_map.get(item["index"]) or item.get("existing_sync_id")
            if not sync_id or item["status"] == "skip":
                continue
            for raw in item["sources"]:
                _link_source(
                    db,
                    user,
                    ledger,
                    sync_id,
                    SourceInput.model_validate(raw),
                    import_plan_id=plan_id,
                )
            if item["index"] in created_map:
                created_revisions[sync_id] = int(
                    db.scalar(
                        select(func.min(SyncChange.change_id)).where(
                            SyncChange.ledger_id == ledger.id,
                            SyncChange.entity_type == "transaction",
                            SyncChange.entity_sync_id == sync_id,
                            SyncChange.action == "upsert",
                        )
                    )
                    or 0
                )
        row.data_json = {
            **data,
            "operation_id": outcome["operation_id"],
            "created_revisions": created_revisions,
        }
        row.status = "completed"
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
    return {
        **outcome,
        "plan_id": plan_id,
        "plan_revision": expected_revision,
        "sources_linked": True,
    }


async def undo_import_plan(
    user: User, plan_id: str, expected_revision: int, confirm: bool = False
) -> dict[str, Any]:
    """Remove only transactions created by an import. Revision checks preserve later user edits; linked pre-existing entries are never removed. Confirmation required."""
    if not confirm:
        return dict(status="confirmation_required")
    with wt.SessionLocal() as db:
        row, ledger = load_record(db, user, plan_id, kind="import_plan", write=True)
        if row.user_id != user.id:
            raise ValueError("Only the import creator can undo its operation")
        if row.revision != expected_revision or row.status not in {"completed", "undone"}:
            raise ValueError("Plan version/status does not permit undo")
        data = row.data_json
        if row.status == "undone":
            return dict(status="undone", plan_id=plan_id)
        conflicts, steps = [], []
        for sync_id, expected in data.get("created_revisions", {}).items():
            tx = db.get(ReadTxProjection, (ledger.id, sync_id))
            if tx is None:
                continue
            if tx.source_change_id != expected:
                conflicts.append(sync_id)
            steps.append(
                dict(
                    method="DELETE",
                    path=f"{get_settings().api_prefix}/write/ledgers/{ledger.external_id}/transactions/{sync_id}",
                    body=dict(base_change_id=0, expected_revision=expected),
                )
            )
        if conflicts:
            return dict(status="conflict", modified_sync_ids=conflicts, deleted_count=0)
        db.expunge(ledger)
    outcome = await run_write(
        user,
        ledger,
        kind="undo_import",
        request=dict(plan_id=plan_id, revision=expected_revision),
        steps=steps,
        idempotency_key="undo:" + plan_id,
    )
    if outcome["status"] == "completed":
        with wt.SessionLocal() as db:
            row, _ = load_record(db, user, plan_id, kind="import_plan", write=True)
            row.status = "undone"
            row.updated_at = datetime.now(timezone.utc)
            db.execute(
                delete(MCPSourceReference).where(
                    MCPSourceReference.ledger_id == ledger.id,
                    MCPSourceReference.tx_sync_id.in_(list(data.get("created_revisions", {}))),
                )
            )
            db.commit()
    return {**outcome, "plan_id": plan_id}


def reconcile_account(
    user: User,
    ledger_id: str,
    account_id: str,
    at: str,
    actual_balance: float,
    time_zone: str | None = None,
) -> dict[str, Any]:
    """Compare an actual statement balance with opening balance plus all accessible account movements through a date/time. Report missing-account candidates; never silently create adjustments."""
    import math

    if not math.isfinite(actual_balance):
        raise ValueError("actual_balance must be finite")
    with rt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id)
        owner = ledger.user_id
        owner_ledgers = [led for led in rt.live_ledgers(db, user.id) if led.user_id == owner]
        cutoff = date_boundary(at, end=True, time_zone=time_zone)
        # Accounts are user-global; the selected ledger only establishes owner/authorization.
        balances = get_account_balances(user, at=at, time_zone=time_zone, scope="visible")
        matches = [
            item
            for item in balances["accounts"]
            if item["account_id"] == account_id and item["owner_user_id"] == owner
        ]
        if len(matches) != 1:
            raise ValueError("Account not found")
        account = matches[0]
        unassigned = db.scalars(
            select(ReadTxProjection)
            .where(
                ReadTxProjection.ledger_id.in_([led.id for led in owner_ledgers]),
                ReadTxProjection.happened_at < cutoff,
                ReadTxProjection.tx_type.in_(["income", "expense"]),
                ReadTxProjection.account_sync_id.is_(None),
            )
            .order_by(ReadTxProjection.happened_at.desc())
            .limit(20)
        ).all()
        return dict(
            account=account,
            actual_balance=actual_balance,
            calculated_balance=account["balance"],
            difference=actual_balance - account["balance"],
            reconciled=abs(actual_balance - account["balance"]) < 0.005,
            at=at,
            cutoff_exclusive=cutoff.isoformat(),
            time_zone=str(time_zone or "Cloud default"),
            coverage=dict(
                ledger_ids=[led.external_id for led in owner_ledgers],
                revision=revision(db, [led.id for led in owner_ledgers], owner),
                opening_balance_effective_date="legacy opening balance has no effective date",
            ),
            unassigned_candidates=[rt._serialize_tx(tx, tx.category_name) for tx in unassigned],
        )


def save_reconciliation_checkpoint(
    user: User,
    ledger_id: str,
    account_id: str,
    at: str,
    actual_balance: float,
    source_system: str,
    coverage_from: str | None = None,
    time_zone: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Save a user-confirmed account/source coverage checkpoint, including the statement difference and dataset revision, for another Agent to continue."""
    if not confirm:
        return dict(status="confirmation_required")
    result = reconcile_account(user, ledger_id, account_id, at, actual_balance, time_zone)
    if coverage_from and date_boundary(coverage_from, time_zone=time_zone) >= date_boundary(
        at, end=True, time_zone=time_zone
    ):
        raise ValueError("coverage_from must precede the checkpoint end")
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        db.expunge(ledger)
    return save_record(
        user,
        ledger,
        kind="checkpoint",
        data={
            **result,
            "source_system": source_system,
            "coverage_from": coverage_from,
            "confirmed_by_user_id": user.id,
        },
        status="confirmed",
    )


def unlink_transaction_source(
    user: User, ledger_id: str, reference_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Remove an incorrect external-source association after user confirmation. The transaction is preserved; reserved active-import sources cannot be removed."""
    if not confirm:
        return {"status": "confirmation_required"}
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        reference = db.get(MCPSourceReference, reference_id)
        if not reference or reference.ledger_id != ledger.id:
            raise ValueError("Source reference not found")
        if (reference.metadata_json or {}).get("_reserved"):
            raise ValueError("Source belongs to an unfinished import; resume that operation first")
        db.add(
            AuditLog(
                user_id=user.id,
                ledger_id=ledger.id,
                action="mcp_source_unlink",
                metadata_json={"reference_id": reference_id, "before": _source_out(reference)},
            )
        )
        db.delete(reference)
        db.commit()
        return {"status": "unlinked", "reference_id": reference_id}


READ_FUNCTIONS = [
    list_workflow_records,
    get_workflow_record,
    list_transaction_sources,
    reconcile_account,
]
WRITE_FUNCTIONS = [
    link_transaction_source,
    unlink_transaction_source,
    preview_import,
    execute_import_plan,
    undo_import_plan,
    save_reconciliation_checkpoint,
]
