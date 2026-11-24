"""User-confirmed categorization, review queues and Cloud-owned recurring schedules."""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field
from sqlalchemy import and_, or_, select, update

from ..config import get_settings
from ..models import (
    Ledger,
    MCPStateRecord,
    MCPWriteOperation,
    ReadTxProjection,
    User,
    UserAccountProjection,
)
from ..recurring_handoff import local_periods, receipt, receipt_id, required_uploads
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
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    day_of_week: int | None = Field(default=None, ge=1, le=7)
    month_of_year: int | None = Field(default=None, ge=1, le=12)
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
        weekly = rule["frequency"] == "weekly"
        offset = (
            ((rule.get("day_of_week") or anchor.isoweekday()) - anchor.isoweekday()) % 7
            if weekly
            else 0
        )
        local = anchor + timedelta(days=interval * (7 if weekly else 1) + offset)
    else:
        yearly = rule["frequency"] == "yearly"
        month = (rule.get("month_of_year") or anchor.month) if yearly else anchor.month
        day = rule.get("day_of_month") or anchor.day
        first = anchor.replace(
            month=month, day=min(day, calendar.monthrange(anchor.year, month)[1])
        )
        step = 12 if yearly else 1
        months = interval * step + (step if first < anchor else 0)
        month_index = anchor.year * 12 + month - 1 + months
        year, month = divmod(month_index, 12)
        if year > 9999:
            raise OverflowError("End of calendar")
        month += 1
        local = anchor.replace(
            year=year, month=month, day=min(day, calendar.monthrange(year, month)[1])
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
        try:
            due = occurrence(data, index)
        except OverflowError:
            return index, None
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
    return await change_recurring_rule(
        user, action, ledger_id, data, record_id, expected_revision, confirm
    )


async def change_recurring_rule(
    user,
    action,
    ledger_id,
    data=None,
    record_id=None,
    expected_revision=None,
    confirm=False,
    *,
    client_id=None,
    consumed_through=None,
    migration=False,
    distinct=False,
    local_transactions=(),
    reviewed_mapping=None,
):
    """One rule store and scheduler for App, Web and MCP."""
    if (action == "create") != (record_id is None):
        raise ValueError("Create requires a new rule; other actions require a record_id")
    create_id = None
    fingerprint = None
    with wt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id, write=True)
        handoff_key = receipt_id(ledger.id, user.id, client_id) if migration and client_id else None
        committed = receipt(db, handoff_key)
        if committed:
            return committed
        previous = None
        if action == "create" and client_id and data is not None:
            create_id = str(
                uuid5(NAMESPACE_URL, f"beecount:recurring:{ledger.id}:{user.id}:{client_id}")
            )
            fingerprint = hashlib.sha256(
                json.dumps(
                    [data.model_dump(mode="json"), consumed_through],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            prior = db.get(MCPStateRecord, create_id)
            if prior is not None and confirm:
                return replay_creation(prior, fingerprint)
        if record_id:
            row, _ = load_record(db, user, record_id, kind="recurring_rule", write=True)
            if row.ledger_id != ledger.id or row.user_id != user.id:
                raise ValueError("Only the schedule creator can manage its execution identity")
            if row.revision != expected_revision:
                raise ValueError("REVISION_CONFLICT: recurring rule changed")
            if row.status == "deleted":
                raise ValueError("Record not found")
            if row.status == "executing":
                return dict(status="operation_pending")
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
        try:
            tz = ZoneInfo(data.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Invalid IANA time zone") from exc
        anchor = date_boundary(data.starts_at, time_zone=data.time_zone).astimezone(tz)
        if (
            data.ends_at
            and date_boundary(data.ends_at, end=True, time_zone=data.time_zone) <= anchor
        ):
            raise ValueError("End must follow start")
        # Validate the full transaction contract now; occurrence timestamps are supplied by Cloud.
        body = await prepare_transaction(
            user, ledger, {**data.transaction.model_dump(), "happened_at": anchor.isoformat()}
        )
        check_recurring_accounts(ledger, body)
        payload = dict(
            **(previous or {}),
        )
        payload.update(
            rule=data.model_dump(mode="json"),
            anchor_local=anchor.isoformat(),
            execution_owner="cloud",
            executed_count=(previous or {}).get("executed_count", 0),
        )
        if fingerprint:
            payload["creation_hash"] = fingerprint
        same_schedule = previous and all(
            previous["rule"].get(key) == payload["rule"].get(key)
            for key in [
                "frequency",
                "interval",
                "starts_at",
                "time_zone",
                "dst_policy",
                "day_of_month",
                "month_of_year",
                "day_of_week",
            ]
        )
        start_index = previous.get("next_index", 0) if same_schedule else 0
        earliest = None if data.allow_backfill or same_schedule else datetime.now(timezone.utc)
        if earliest is not None and len(data.starts_at) == 10:
            earliest = earliest.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        if consumed_through:
            earliest = max(
                anchor, date_boundary(consumed_through, end=True, time_zone=data.time_zone)
            )
        if previous and previous.get("last_due_at") and not same_schedule:
            last = datetime.fromisoformat(previous["last_due_at"]).astimezone(tz)
            boundary = last + timedelta(microseconds=1)
            if previous["rule"]["frequency"] == data.frequency:
                midnight = last.replace(hour=0, minute=0, second=0, microsecond=0)
                if data.frequency == "monthly":
                    boundary = (midnight.replace(day=1) + timedelta(days=32)).replace(day=1)
                elif data.frequency == "yearly":
                    boundary = midnight.replace(year=last.year + 1, month=1, day=1)
                elif data.frequency == "weekly":
                    boundary = midnight + timedelta(days=8 - last.isoweekday())
                else:
                    boundary = midnight + timedelta(days=1)
            earliest = max(earliest or boundary, boundary)
        index, due = next_occurrence(payload, start_index, earliest=earliest)
        payload["next_index"] = index
        state = "active" if data.enabled and due else ("completed" if due is None else "paused")
    else:
        payload = dict(previous)
        earliest = (
            datetime.now(timezone.utc) if action == "resume" and row.status == "paused" else None
        )
        index, due = next_occurrence(payload, payload["next_index"], earliest=earliest)
        payload["next_index"] = index
        payload["rule"] = {**payload["rule"], "enabled": action == "resume"}
        state = {
            "pause": "paused",
            "resume": "active" if due else "completed",
            "delete": "deleted",
        }[action]
    mapping = {}

    def mapping_hash(values):
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    if handoff_key:
        with wt.SessionLocal() as db:
            mapping = local_periods(
                db, ledger, user, record_id or create_id, payload["rule"], local_transactions
            )
        if confirm and local_transactions and reviewed_mapping != mapping_hash(mapping):
            raise ValueError("REVISION_CONFLICT: review local transactions again")

    def finish_handoff(db, row):
        existing = receipt(db, handoff_key)
        if existing:
            return existing
        accepted = local_periods(
            db, ledger, user, row.id, payload["rule"], local_transactions, commit=True
        )
        uploads = required_uploads(db, ledger.id, accepted)
        if accepted != mapping:
            raise ValueError("REVISION_CONFLICT: another device handed off this period")
        db.add(
            MCPStateRecord(
                id=handoff_key,
                ledger_id=ledger.id,
                user_id=user.id,
                kind="recurring_handoff",
                status="completed",
                data_json={
                    "rule_id": row.id,
                    "transactions": accepted,
                    "required_uploads": uploads,
                },
            )
        )
        return {
            "handoff_confirmed": True,
            "local_transactions": accepted,
            "required_uploads": uploads,
        }

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
            local_transactions=mapping,
            reviewed_mapping=mapping_hash(mapping) if handoff_key else None,
            execution_owner="cloud",
            backfill=payload["rule"]["allow_backfill"],
            dst_policy=payload["rule"]["dst_policy"],
        )

    def check_create(db):
        # Serialize concurrent creates across devices on SQLite and PostgreSQL.
        prior = db.get(MCPStateRecord, create_id)
        if prior is not None:
            replay_creation(prior, fingerprint)
            return prior
        if migration and not distinct:
            candidates = db.scalars(
                select(MCPStateRecord).where(
                    MCPStateRecord.ledger_id == ledger.id,
                    MCPStateRecord.kind == "recurring_rule",
                    MCPStateRecord.status != "deleted",
                )
            )
            for candidate in candidates:
                other = candidate.data_json["rule"]
                if (
                    other["transaction"]["amount"] == data.transaction.amount
                    and other["transaction"].get("tx_type", "expense") == data.transaction.tx_type
                    and other["frequency"] == data.frequency
                ):
                    raise ValueError("SIMILAR_RULE: review existing rules before creating another")
        return None

    result = save_record(
        user,
        ledger,
        kind="recurring_rule",
        create_id=create_id,
        check_create=check_create if create_id else None,
        after_save=finish_handoff if handoff_key else None,
        data=payload,
        record_id=record_id,
        expected_revision=expected_revision,
        status=state,
        next_due_at=due,
    )
    return {**result, "next_occurrences": preview}


def replay_creation(row, fingerprint):
    from .workflow_tools import record_out

    if row.data_json.get("creation_hash") != fingerprint:
        raise ValueError("REVISION_CONFLICT: creation key already used")
    return record_out(row)


def check_recurring_accounts(ledger, transaction):
    ids = [transaction.get(k) for k in ("account_id", "from_account_id", "to_account_id")]
    ids = [v for v in ids if v]
    if ids:
        with wt.SessionLocal() as db:
            hidden = db.scalar(
                select(UserAccountProjection.sync_id).where(
                    UserAccountProjection.user_id == ledger.user_id,
                    UserAccountProjection.sync_id.in_(ids),
                    UserAccountProjection.hidden.is_(True),
                )
            )
            if hidden:
                raise ValueError("A recurring account is hidden")


async def execute_due_rules(*, now=None, only_record_id=None, limit=20):
    """Scheduler entry. Every occurrence revalidates user/ledger role and uses a stable write key."""
    now = now or datetime.now(timezone.utc)
    lease_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    claimable = or_(
        MCPStateRecord.status == "active",
        and_(MCPStateRecord.status == "executing", MCPStateRecord.updated_at < lease_cutoff),
    )
    with wt.SessionLocal() as db:
        query = (
            select(MCPStateRecord)
            .where(
                MCPStateRecord.kind == "recurring_rule",
                claimable,
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
            # A committed claim excludes edits while the ordinary transaction is written.
            # After a worker crash the next holder resumes the same immutable operation key.
            claim_time = datetime.now(timezone.utc)
            with wt.SessionLocal() as db:
                claim = db.execute(
                    update(MCPStateRecord)
                    .where(
                        MCPStateRecord.id == row.id,
                        MCPStateRecord.revision == row.revision,
                        MCPStateRecord.next_due_at == due,
                        claimable,
                    )
                    .values(status="executing", updated_at=claim_time)
                )
                db.commit()
                if not claim.rowcount:
                    continue
            raw = {**data["rule"]["transaction"], "happened_at": due.isoformat()}
            key = "recurring:" + row.id + ":" + due.strftime("%Y%m%dT%H%M%SZ")
            try:
                with wt.SessionLocal() as db:
                    started = db.scalar(
                        select(MCPWriteOperation.id).where(
                            MCPWriteOperation.user_id == user.id,
                            MCPWriteOperation.request_key == key,
                        )
                    )
                if not started:
                    check_recurring_accounts(ledger, await prepare_transaction(user, ledger, raw))
                outcome = await wt.create_transactions(
                    user, ledger_id=ledger.external_id, transactions=[raw], idempotency_key=key
                )
            except ValueError:
                # Invalid resource before an operation exists: allow the creator to fix the rule.
                with wt.SessionLocal() as db:
                    if not db.scalar(
                        select(MCPWriteOperation.id).where(
                            MCPWriteOperation.user_id == user.id,
                            MCPWriteOperation.request_key == key,
                        )
                    ):
                        db.execute(
                            update(MCPStateRecord)
                            .where(
                                MCPStateRecord.id == row.id,
                                MCPStateRecord.updated_at == claim_time,
                            )
                            .values(status="active", updated_at=datetime.now(timezone.utc))
                        )
                        db.commit()
                raise
            results.append(dict(rule_id=row.id, due=due.isoformat(), operation=outcome))
            if outcome.get("status") != "created":
                with wt.SessionLocal() as db:
                    db.execute(
                        update(MCPStateRecord)
                        .where(
                            MCPStateRecord.id == row.id,
                            MCPStateRecord.status == "executing",
                            MCPStateRecord.updated_at == claim_time,
                        )
                        .values(status="active", updated_at=datetime.now(timezone.utc))
                    )
                    db.commit()
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
                        MCPStateRecord.updated_at == claim_time,
                        MCPStateRecord.status == "executing",
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
            logging.getLogger(__name__).warning("MCP recurring rule %s failed (%s)", row.id, code)
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
                MCPStateRecord.status.in_(["active", "executing"]),
                MCPStateRecord.next_due_at <= datetime.now(timezone.utc),
            )
            .order_by(MCPStateRecord.next_due_at)
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
