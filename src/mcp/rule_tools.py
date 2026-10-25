"""User-confirmed categorization, review queues and Cloud-owned recurring schedules."""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field
from sqlalchemy import select, update

from ..config import get_settings
from ..models import Ledger, MCPStateRecord, MCPWriteOperation, ReadTxProjection, User
from .capability_tools import Fields, require_ledger, run_write
from .query_context import date_boundary
from .schemas import BatchTransactionItem
from .tools import read_tools as rt
from .tools import write_tools as wt
from .transaction_service import dictionary_entry, prepare_transaction
from .workflow_tools import load_record, save_record


class ReviewItem(Fields):
    question: str = Field(min_length=1, max_length=4000)
    transaction_ids: list[str] = Field(default_factory=list, max_length=200)
    answer: str | None = Field(default=None, max_length=8000)
    status: Literal["open", "answered", "resolved", "dismissed"] = "open"


class ClassificationRule(Fields):
    name: str = Field(min_length=1, max_length=255)
    note_contains: str | None = Field(default=None, min_length=1, max_length=500)
    account_id: str | None = None
    tx_type: Literal["income", "expense"] = "expense"
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    category_id: str
    tags: list[str] | None = None
    except_sync_ids: list[str] = Field(default_factory=list, max_length=1000)
    enabled: bool = True
    confirmed: bool = False


class RecurringRule(Fields):
    name: str = Field(min_length=1, max_length=255)
    transaction: BatchTransactionItem
    frequency: Literal["daily", "weekly", "monthly", "yearly"]
    interval: int = Field(default=1, ge=1, le=365)
    starts_at: str
    time_zone: str
    ends_at: str | None = None
    enabled: bool = True
    allow_backfill: bool = False
    dst_policy: Literal["skip", "first", "second"] = "skip"


def save_review_item(
    user: User,
    ledger_id: str,
    data: ReviewItem,
    record_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Save a question, related transaction IDs, user answer and resolution state so another Agent can continue. Updates require the current revision."""
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        ids = set(
            db.scalars(
                select(ReadTxProjection.sync_id).where(
                    ReadTxProjection.ledger_id == ledger.id,
                    ReadTxProjection.sync_id.in_(data.transaction_ids),
                )
            ).all()
        )
        if ids != set(data.transaction_ids):
            raise ValueError("A related transaction was not found in this ledger")
        db.expunge(ledger)
    return save_record(
        user,
        ledger,
        kind="review_item",
        data=data.model_dump(mode="json"),
        record_id=record_id,
        expected_revision=expected_revision,
        status=data.status,
    )


def save_classification_rule(
    user: User,
    ledger_id: str,
    data: ClassificationRule,
    record_id: str | None = None,
    expected_revision: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Save a versioned categorization rule and exceptions. Confirmed rules require explicit user confirmation; inferred rules remain suggestions and are never applied automatically."""
    if data.confirmed and not confirm:
        return dict(status="confirmation_required")
    if not any(
        [
            data.note_contains,
            data.account_id,
            data.min_amount is not None,
            data.max_amount is not None,
        ]
    ):
        raise ValueError("A rule needs at least one matching condition")
    if (
        data.min_amount is not None
        and data.max_amount is not None
        and data.min_amount > data.max_amount
    ):
        raise ValueError("Invalid amount range")
    from ..models import UserAccountProjection, UserCategoryProjection

    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        dictionary_entry(
            db,
            UserCategoryProjection,
            ledger.user_id,
            identifier=data.category_id,
            kind=data.tx_type,
        )
        if data.account_id:
            dictionary_entry(db, UserAccountProjection, ledger.user_id, identifier=data.account_id)
        db.expunge(ledger)
    return save_record(
        user,
        ledger,
        kind="classification_rule",
        data=data.model_dump(mode="json"),
        record_id=record_id,
        expected_revision=expected_revision,
        status="active" if data.enabled else "paused",
    )


async def apply_classification_rule(
    user: User, rule_id: str, expected_revision: int, confirm: bool = False
) -> dict[str, Any]:
    """Save a preview of a confirmed rule's matching transactions and before/after values. Apply using execute_classification_preview; each target carries its original revision."""
    with wt.SessionLocal() as db:
        row, ledger = load_record(db, user, rule_id, kind="classification_rule", write=True)
        if row.revision != expected_revision:
            raise ValueError("REVISION_CONFLICT: rule changed")
        rule = ClassificationRule.model_validate(row.data_json)
        if not rule.confirmed or row.status != "active":
            raise ValueError("Rule must be active and user-confirmed")
        query = select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger.id, ReadTxProjection.tx_type == rule.tx_type
        )
        if rule.note_contains:
            escaped = (
                rule.note_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            query = query.where(ReadTxProjection.note.ilike("%" + escaped + "%", escape="\\"))
        if rule.account_id:
            query = query.where(ReadTxProjection.account_sync_id == rule.account_id)
        if rule.min_amount is not None:
            query = query.where(ReadTxProjection.amount >= rule.min_amount)
        if rule.max_amount is not None:
            query = query.where(ReadTxProjection.amount <= rule.max_amount)
        if rule.except_sync_ids:
            query = query.where(ReadTxProjection.sync_id.not_in(rule.except_sync_ids))
        rows = db.scalars(query.order_by(ReadTxProjection.sync_id).limit(201)).all()
        if len(rows) > 200:
            raise ValueError("Rule matches more than 200 transactions; narrow the conditions")
        for tx in rows:
            db.expunge(tx)
        db.expunge(ledger)
    steps, preview = [], []
    for tx in rows:
        patch = await prepare_transaction(
            user,
            ledger,
            dict(
                category_id=rule.category_id, tags=rule.tags, expected_revision=tx.source_change_id
            ),
            existing=tx,
        )
        steps.append(
            dict(
                method="PATCH",
                path=f"{get_settings().api_prefix}/write/ledgers/{ledger.external_id}/transactions/{tx.sync_id}",
                body=patch,
            )
        )
        preview.append(
            dict(
                sync_id=tx.sync_id,
                expected_revision=tx.source_change_id,
                before=rt._serialize_tx(tx, tx.category_name),
                after=patch,
            )
        )
    if not confirm:
        # A persisted preview freezes the exact target set and versions used for confirmation.
        saved = save_record(
            user,
            ledger,
            kind="classification_preview",
            status="ready",
            data=dict(
                rule_id=rule_id, rule_revision=expected_revision, steps=steps, preview=preview
            ),
        )
        return dict(
            status="confirmation_required",
            preview_id=saved["id"],
            items=preview,
            message="Use execute_classification_preview with this preview_id after user confirmation",
        )
    raise ValueError(
        "Apply the persisted preview with execute_classification_preview; do not recompute targets during confirmation"
    )


async def execute_classification_preview(
    user: User, preview_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Apply exactly the saved rule preview after user confirmation. Stale transaction revisions fail without overwriting user edits."""
    if not confirm:
        return dict(status="confirmation_required")
    with wt.SessionLocal() as db:
        row, ledger = load_record(db, user, preview_id, kind="classification_preview", write=True)
        data = row.data_json
        rule, _ = load_record(db, user, data["rule_id"], kind="classification_rule")
        if rule.revision != data["rule_revision"] or rule.status != "active":
            raise ValueError("REVISION_CONFLICT: rule changed after preview")
        db.expunge(ledger)
    return await run_write(
        user,
        ledger,
        kind="classify",
        request=dict(preview_id=preview_id),
        steps=data["steps"],
        idempotency_key="classify:" + preview_id,
    )


def occurrence(data, index):
    anchor = datetime.fromisoformat(data["anchor_local"]).replace(tzinfo=None)
    rule = data["rule"]
    interval = rule["interval"] * index
    if rule["frequency"] in {"daily", "weekly"}:
        local = anchor + timedelta(days=interval * (7 if rule["frequency"] == "weekly" else 1))
    else:
        months = interval * (12 if rule["frequency"] == "yearly" else 1)
        month_index = anchor.year * 12 + anchor.month - 1 + months
        year, month = divmod(month_index, 12)
        month += 1
        local = anchor.replace(
            year=year, month=month, day=min(anchor.day, calendar.monthrange(year, month)[1])
        )
    tz = ZoneInfo(rule["time_zone"])
    instants = sorted(
        {
            local.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
            for fold in (0, 1)
            if local.replace(tzinfo=tz, fold=fold)
            .astimezone(timezone.utc)
            .astimezone(tz)
            .replace(tzinfo=None)
            == local
        }
    )
    if not instants:
        return None
    if len(instants) == 2:
        if rule["dst_policy"] == "skip":
            return None
        return instants[-1] if rule["dst_policy"] == "second" else instants[0]
    return instants[0]


def next_occurrence(data, index, *, earliest=None):
    end = (
        date_boundary(data["rule"]["ends_at"], end=True, time_zone=data["rule"]["time_zone"])
        if data["rule"].get("ends_at")
        else None
    )
    for _ in range(50000):
        due = occurrence(data, index)
        if due and end and due >= end:
            return index, None
        if due and (earliest is None or due >= earliest):
            return index, due
        index += 1
    raise ValueError("Recurring schedule exceeds the supported search range")


async def manage_recurring_rule(
    user: User,
    action: Literal["create", "update", "pause", "resume", "delete"],
    ledger_id: str,
    data: RecurringRule | None = None,
    record_id: str | None = None,
    expected_revision: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview/create/edit/pause/resume/delete a Cloud-owned recurring rule. Month/year ends clamp to valid dates without drifting the anchor. Historical backfill is opt-in; generated ordinary transactions sync to App. Do not duplicate the same rule in App."""
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        previous = None
        if record_id:
            row, _ = load_record(db, user, record_id, kind="recurring_rule", write=True)
            if row.ledger_id != ledger.id or row.user_id != user.id:
                raise ValueError("Only the schedule creator can manage its execution identity")
            if row.revision != expected_revision:
                raise ValueError("REVISION_CONFLICT: recurring rule changed")
            previous = row.data_json
            if action == "update" and row.next_due_at is not None:
                occurrence_key = (
                    "recurring:"
                    + row.id
                    + ":"
                    + row.next_due_at.replace(tzinfo=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                )
                pending = db.scalar(
                    select(MCPWriteOperation).where(
                        MCPWriteOperation.user_id == row.user_id,
                        MCPWriteOperation.request_key == occurrence_key,
                    )
                )
                if pending:
                    return dict(
                        status="operation_pending",
                        operation_id=pending.id,
                        message="Resume the original occurrence with run_due_recurring_rules before changing its schedule payload; pause/delete remain available",
                    )
        elif action != "create":
            raise ValueError("record_id and expected_revision are required")
        db.expunge(ledger)
    if action in {"create", "update"}:
        if data is None:
            raise ValueError("data is required")
        tz = ZoneInfo(data.time_zone)
        anchor = date_boundary(data.starts_at, time_zone=data.time_zone).astimezone(tz)
        # Validate the full transaction contract now; occurrence timestamps are supplied by Cloud.
        await prepare_transaction(
            user, ledger, {**data.transaction.model_dump(), "happened_at": anchor.isoformat()}
        )
        payload = dict(
            rule=data.model_dump(mode="json"),
            anchor_local=anchor.isoformat(),
            execution_owner="cloud",
            executed_count=(previous or {}).get("executed_count", 0),
        )
        same_schedule = previous and all(
            previous["rule"][key] == payload["rule"][key]
            for key in ["frequency", "interval", "starts_at", "time_zone", "dst_policy"]
        )
        start_index = previous.get("next_index", 0) if same_schedule else 0
        earliest = None if data.allow_backfill or same_schedule else datetime.now(timezone.utc)
        index, due = next_occurrence(payload, start_index, earliest=earliest)
        payload["next_index"] = index
        state = "active" if data.enabled and due else ("completed" if due is None else "paused")
    else:
        payload = dict(previous)
        index, due = next_occurrence(payload, payload["next_index"])
        state = {
            "pause": "paused",
            "resume": "active" if due else "completed",
            "delete": "deleted",
        }[action]
    preview = []
    next_index = index
    for _ in range(5):
        next_index, next_due = next_occurrence(payload, next_index)
        if not next_due:
            break
        preview.append(next_due.astimezone(ZoneInfo(payload["rule"]["time_zone"])).isoformat())
        next_index += 1
    if not confirm:
        return dict(
            status="confirmation_required",
            next_occurrences=preview,
            execution_owner="cloud",
            backfill=payload["rule"]["allow_backfill"],
            dst_policy=payload["rule"]["dst_policy"],
        )
    result = save_record(
        user,
        ledger,
        kind="recurring_rule",
        data=payload,
        record_id=record_id,
        expected_revision=expected_revision,
        status=state,
        next_due_at=due,
    )
    return {**result, "next_occurrences": preview}


async def execute_due_rules(*, now=None, only_record_id=None, limit=20):
    """Scheduler entry. Every occurrence revalidates user/ledger role and uses a stable write key."""
    now = now or datetime.now(timezone.utc)
    with wt.SessionLocal() as db:
        query = (
            select(MCPStateRecord)
            .where(
                MCPStateRecord.kind == "recurring_rule",
                MCPStateRecord.status == "active",
                MCPStateRecord.next_due_at <= now,
            )
            .order_by(MCPStateRecord.next_due_at)
            .limit(limit)
        )
        if only_record_id:
            query = query.where(MCPStateRecord.id == only_record_id)
        rules = db.scalars(query).all()
        for row in rules:
            db.expunge(row)
    results = []
    for row in rules:
        try:
            with wt.SessionLocal() as db:
                user = db.get(User, row.user_id)
                ledger = db.get(Ledger, row.ledger_id)
                if not user or not user.is_enabled or not ledger:
                    continue
                try:
                    require_ledger(db, user, ledger.external_id, write=True)
                except ValueError:
                    results.append(dict(rule_id=row.id, status="permission_denied"))
                    continue
                db.expunge(user)
                db.expunge(ledger)
            data = dict(row.data_json)
            index, due = next_occurrence(data, data["next_index"])
            if due is None or due > now:
                continue
            raw = {**data["rule"]["transaction"], "happened_at": due.isoformat()}
            key = "recurring:" + row.id + ":" + due.strftime("%Y%m%dT%H%M%SZ")
            outcome = await wt.create_transactions(
                user, ledger_id=ledger.external_id, transactions=[raw], idempotency_key=key
            )
            results.append(dict(rule_id=row.id, due=due.isoformat(), operation=outcome))
            if outcome.get("status") != "created":
                continue
            next_index, next_due = next_occurrence(data, index + 1)
            data.update(
                next_index=next_index,
                executed_count=data.get("executed_count", 0) + 1,
                last_operation_id=outcome["operation_id"],
                last_due_at=due.isoformat(),
            )
            with wt.SessionLocal() as db:
                db.execute(
                    update(MCPStateRecord)
                    .where(
                        MCPStateRecord.id == row.id,
                        MCPStateRecord.revision == row.revision,
                        MCPStateRecord.status == "active",
                        MCPStateRecord.next_due_at == due,
                    )
                    .values(
                        data_json=data,
                        next_due_at=next_due,
                        revision=row.revision + 1,
                        status="active" if next_due else "completed",
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                db.commit()
        except Exception as exc:
            # A stale account/category must not starve unrelated users' schedules.
            # Report a safe code; transaction/provider exception text may contain source data.
            code = type(exc).__name__
            logging.getLogger(__name__).warning(
                "MCP recurring rule %s failed (%s)", row.id, code
            )
            results.append(dict(rule_id=row.id, status="failed", error_code=code))
    return results


async def run_due_recurring_rules(user: User, ledger_id: str) -> dict[str, Any]:
    """Process currently due Cloud schedules owned by this user in one ledger. Never generates future transactions; repeated runs are idempotent."""
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        ids = db.scalars(
            select(MCPStateRecord.id)
            .where(
                MCPStateRecord.ledger_id == ledger.id,
                MCPStateRecord.user_id == user.id,
                MCPStateRecord.kind == "recurring_rule",
                MCPStateRecord.status == "active",
            )
            .limit(100)
        ).all()
    outcomes = []
    for identifier in ids:
        outcomes.extend(await execute_due_rules(only_record_id=identifier))
    return dict(results=outcomes)


READ_FUNCTIONS = []
WRITE_FUNCTIONS = [
    save_review_item,
    save_classification_rule,
    apply_classification_rule,
    execute_classification_preview,
    manage_recurring_rule,
    run_due_recurring_rules,
]
