"""Reserve the canonical transaction for each local period during a Cloud handoff."""

from datetime import date, datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .models import (
    MCPSourceReference,
    MCPStateRecord,
    MCPWriteOperation,
    ReadTxProjection,
    SyncChange,
)

RECURRING_SOURCE_PREFIX = "beecount.recurring."


def last_imported_dates(db, ledger_id):
    """Original local posting days survive transaction edits and deletion."""
    rows = db.execute(
        select(
            MCPSourceReference.source_system,
            func.max(MCPSourceReference.metadata_json["date"].as_string()),
        )
        .where(
            MCPSourceReference.ledger_id == ledger_id,
            MCPSourceReference.source_system.startswith(RECURRING_SOURCE_PREFIX),
        )
        .group_by(MCPSourceReference.source_system)
    )
    return {source.removeprefix(RECURRING_SOURCE_PREFIX): day for source, day in rows if day}


def receipt_id(ledger_id, user_id, client_id):
    return str(uuid5(NAMESPACE_URL, f"beecount:handoff:{ledger_id}:{user_id}:{client_id}"))


def period_key(day, frequency):
    if frequency == "yearly":
        return str(day.year)
    if frequency == "monthly":
        return day.isoformat()[:7]
    if frequency == "weekly":
        year, week, _ = day.isocalendar()
        return f"{year}-W{week:02}"
    return day.isoformat()


def source_rules(db, ledger_id, occurrences):
    """Only a shared transaction identity can prove two device copies have the same source."""
    ids = list({item["sync_id"] for item in occurrences})
    matches = set()
    for start in range(0, len(ids), 400):
        sources = db.scalars(
            select(MCPSourceReference.source_system)
            .where(
                MCPSourceReference.ledger_id == ledger_id,
                MCPSourceReference.tx_sync_id.in_(ids[start : start + 400]),
                MCPSourceReference.source_system.startswith(RECURRING_SOURCE_PREFIX),
            )
            .distinct()
        )
        matches.update(source.removeprefix(RECURRING_SOURCE_PREFIX) for source in sources)
    return matches


def migration_signature(rule):
    tx = rule["transaction"]
    return {
        "name": rule["name"].strip().casefold(),
        "type": tx.get("tx_type", "expense"),
        "amount": tx["amount"],
        "frequency": rule["frequency"],
    }


def similar_schedule(incoming, existing):
    if incoming["type"] != existing["type"]:
        return False
    named_match = (
        incoming["name"] not in {"", "expense", "income", "transfer"}
        and incoming["name"] == existing["name"]
    )
    return named_match or (
        incoming["amount"] == existing["amount"] and incoming["frequency"] == existing["frequency"]
    )


def local_periods(db, ledger, user, rule_id, config, items, *, commit=False):
    """Two installations can hold different sync IDs for the same pre-Cloud period."""
    if not items:
        return {}
    source = RECURRING_SOURCE_PREFIX + rule_id

    def period(day):
        return period_key(day, config["frequency"])

    canonical = {
        r.source_id: r.tx_sync_id
        for r in db.scalars(
            select(MCPSourceReference).where(
                MCPSourceReference.ledger_id == ledger.id,
                MCPSourceReference.source_system == source,
            )
        )
    }
    # Pre-existing MCP schedules already have durable occurrence receipts, including deleted transactions.
    prefix = "recurring:" + rule_id + ":"
    for operation in db.scalars(
        select(MCPWriteOperation).where(
            MCPWriteOperation.ledger_id == ledger.id,
            MCPWriteOperation.request_key.startswith(prefix),
            MCPWriteOperation.status == "completed",
        )
    ):
        instant = datetime.strptime(operation.request_key[len(prefix) :], "%Y%m%dT%H%M%SZ")
        key = period(
            instant.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(config["time_zone"])).date()
        )
        for result in operation.results_json or []:
            ids = result.get("created_sync_ids") or [result.get("entity_id")]
            if ids and ids[0]:
                canonical[key] = ids[0]
    mappings = {}
    for item in items:
        sid, day = item["sync_id"], date.fromisoformat(item["date"])
        key = period(day)
        if key not in canonical:
            canonical[key] = sid
            if commit:
                db.add(
                    MCPSourceReference(
                        ledger_id=ledger.id,
                        source_system=source,
                        source_id=key,
                        tx_sync_id=sid,
                        created_by_user_id=user.id,
                        metadata_json={"date": day.isoformat()},
                    )
                )
        if canonical[key] != sid and db.scalar(
            select(ReadTxProjection.sync_id).where(
                ReadTxProjection.ledger_id == ledger.id, ReadTxProjection.sync_id == sid
            )
        ):
            raise ValueError(
                "Already synchronized copies for the same period must be reviewed in the ledger"
            )
        mappings[sid] = canonical[key]
    return mappings


def handoff_upload_state(db, ledger_id, mappings):
    """Read current admission on every retry, including deletions after a lost response."""
    candidates = sorted({sid for sid, canonical in mappings.items() if sid == canonical})
    uploads, deleted = [], []
    for offset in range(0, len(candidates), 400):
        page = candidates[offset : offset + 400]
        present = set(
            db.scalars(
                select(ReadTxProjection.sync_id).where(
                    ReadTxProjection.ledger_id == ledger_id, ReadTxProjection.sync_id.in_(page)
                )
            )
        )
        missing = set(page) - present
        latest = (
            dict(
                db.execute(
                    select(SyncChange.entity_sync_id, SyncChange.action)
                    .where(
                        SyncChange.ledger_id == ledger_id,
                        SyncChange.entity_type == "transaction",
                        SyncChange.entity_sync_id.in_(missing),
                    )
                    .order_by(SyncChange.change_id)
                ).all()
            )
            if missing
            else {}
        )
        deleted.extend(sid for sid in page if latest.get(sid) == "delete")
        uploads.extend(sid for sid in page if sid in missing and sid not in latest)
    return {"required_uploads": uploads, "deleted_transactions": deleted}


def receipt(db, key):
    prior = db.get(MCPStateRecord, key) if key else None
    if prior and prior.kind == "recurring_handoff":
        mappings = prior.data_json["transactions"]
        return {
            "id": prior.data_json["rule_id"],
            "handoff_confirmed": True,
            "local_transactions": mappings,
            **handoff_upload_state(db, prior.ledger_id, mappings),
        }
    return None


def check_mobile_push(db, ledger_ids, changes, protocol):
    """Old clients lack origin metadata. Keep an incompatible batch unacknowledged."""
    import json

    from fastapi import HTTPException

    from .models import Ledger

    financial = {
        c.ledger_id for c in changes if c.entity_type in {"transaction", "ledger_snapshot"}
    }
    if not financial:
        return
    protected = dict(
        db.execute(
            select(Ledger.external_id, Ledger.id).where(
                Ledger.id.in_(ledger_ids),
                Ledger.external_id.in_(financial),
                Ledger.id.in_(
                    select(MCPStateRecord.ledger_id).where(MCPStateRecord.kind == "recurring_rule")
                ),
            )
        ).all()
    )
    if protected and protocol != "cloud-v1":
        raise HTTPException(426, "Update BeeCount to sync a ledger with Cloud recurring rules")
    for change in changes:
        ledger_id = protected.get(change.ledger_id)
        if not ledger_id or change.action == "delete":
            continue
        if change.entity_type == "transaction":
            items = [(change.entity_sync_id, change.payload)]
        elif change.entity_type == "ledger_snapshot":
            try:
                content = json.loads(change.payload["content"])
                items = [(i.get("syncId"), i) for i in content.get("items", [])]
            except (KeyError, TypeError, ValueError, AttributeError):
                raise HTTPException(422, "Invalid ledger snapshot") from None
        else:
            continue
        for sid, item in items:
            if not item.get("localRecurringOrigin"):
                continue
            known = db.scalar(
                select(ReadTxProjection.sync_id).where(
                    ReadTxProjection.ledger_id == ledger_id, ReadTxProjection.sync_id == sid
                )
            )
            if not known:
                last_action = db.scalar(
                    select(SyncChange.action)
                    .where(
                        SyncChange.ledger_id == ledger_id,
                        SyncChange.entity_type == "transaction",
                        SyncChange.entity_sync_id == sid,
                    )
                    .order_by(SyncChange.change_id.desc())
                    .limit(1)
                )
                if last_action == "delete":
                    raise HTTPException(
                        409, "HISTORY_CONFLICT: this recurring transaction was deleted in Cloud"
                    )
                known = db.scalar(
                    select(MCPSourceReference.id).where(
                        MCPSourceReference.ledger_id == ledger_id,
                        MCPSourceReference.tx_sync_id == sid,
                        MCPSourceReference.source_system.startswith(RECURRING_SOURCE_PREFIX),
                    )
                )
            if not known:
                raise HTTPException(
                    409, "Review this device's pending local recurring transactions before syncing"
                )
