"""Reserve the canonical transaction for each local period during a Cloud handoff."""

from datetime import date, datetime, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .models import (
    MCPSourceReference,
    MCPStateRecord,
    MCPWriteOperation,
    ReadTxProjection,
    SyncChange,
)


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


def local_periods(db, ledger, user, rule_id, config, items, *, commit=False):
    """Two installations can hold different sync IDs for the same pre-Cloud period."""
    if not items:
        return {}
    source = "beecount.recurring." + rule_id

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


def required_uploads(db, ledger_id, mappings):
    # A previous deletion still consumes the occurrence. A handoff must never resurrect it.
    candidates = sorted({sid for sid, canonical_id in mappings.items() if sid == canonical_id})
    result = []
    for offset in range(0, len(candidates), 400):
        page = candidates[offset : offset + 400]
        seen = set(
            db.scalars(
                select(ReadTxProjection.sync_id).where(
                    ReadTxProjection.ledger_id == ledger_id, ReadTxProjection.sync_id.in_(page)
                )
            )
        )
        seen.update(
            db.scalars(
                select(SyncChange.entity_sync_id).where(
                    SyncChange.ledger_id == ledger_id,
                    SyncChange.entity_type == "transaction",
                    SyncChange.entity_sync_id.in_(page),
                )
            )
        )
        result.extend(sid for sid in page if sid not in seen)
    return result


def receipt(db, key):
    prior = db.get(MCPStateRecord, key) if key else None
    if prior and prior.kind == "recurring_handoff":
        return {
            "id": prior.data_json["rule_id"],
            "handoff_confirmed": True,
            "local_transactions": prior.data_json["transactions"],
            "required_uploads": prior.data_json.get("required_uploads", []),
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
                known = db.scalar(
                    select(MCPSourceReference.id).where(
                        MCPSourceReference.ledger_id == ledger_id,
                        MCPSourceReference.tx_sync_id == sid,
                        MCPSourceReference.source_system.startswith("beecount.recurring."),
                    )
                )
            if not known:
                raise HTTPException(
                    409, "Review this device's pending local recurring transactions before syncing"
                )
