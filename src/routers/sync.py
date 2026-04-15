import json
import logging
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_scopes
from ..ledger_access import (
    ACTIVE_MEMBER_STATUS,
    READABLE_ROLES,
    ROLE_OWNER,
    ROLE_VIEWER,
    WRITABLE_ROLES,
    get_accessible_ledger_by_external_id,
    list_accessible_memberships,
)
from ..metrics import metrics
from ..models import AuditLog, Device, Ledger, LedgerMember, SyncChange, SyncCursor, User
from ..schemas import (
    SyncChangeOut,
    SyncFullResponse,
    SyncLedgerOut,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from ..security import SCOPE_APP_WRITE

logger = logging.getLogger(__name__)

router = APIRouter()

_INDIVIDUAL_ENTITY_TYPES = {"transaction", "account", "category", "tag"}
_ENTITY_TYPE_TO_SNAPSHOT_KEY = {
    "transaction": "items",
    "account": "accounts",
    "category": "categories",
    "tag": "tags",
}


def _materialize_individual_changes(
    db: Session,
    *,
    ledger_id: str,
    device_id: str,
    user_id: str,
) -> None:
    """Merge individual entity changes into the latest ledger_snapshot.

    This ensures that incremental pushes from Mobile become visible to the Web
    which reads from the latest snapshot.
    """
    # 1. Find latest snapshot
    snapshot_row = db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
        )
        .order_by(SyncChange.change_id.desc())
        .limit(1)
    )

    snapshot_change_id = 0
    snapshot: dict[str, Any] = {}
    if snapshot_row is not None:
        snapshot_change_id = snapshot_row.change_id
        payload = snapshot_row.payload_json
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, str) and content.strip():
                try:
                    snapshot = json.loads(content)
                except json.JSONDecodeError:
                    snapshot = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
    else:
        # No snapshot exists yet — populate basic metadata from Ledger table
        ledger_row = db.scalar(select(Ledger).where(Ledger.id == ledger_id))
        if ledger_row:
            snapshot["ledgerName"] = ledger_row.name or ledger_row.external_id
            snapshot["currency"] = "CNY"

    logger.info(
        "_materialize_individual_changes: ledger=%s snapshot_change_id=%d",
        ledger_id, snapshot_change_id,
    )

    # 2. Get individual changes after the snapshot
    individual_changes = db.execute(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.change_id > snapshot_change_id,
            SyncChange.entity_type.in_(_INDIVIDUAL_ENTITY_TYPES),
        )
        .order_by(SyncChange.change_id.asc())
    ).scalars().all()

    if not individual_changes:
        logger.info("_materialize_individual_changes: no individual changes to apply for ledger=%s", ledger_id)
        return

    # Log per-entity-type counts
    type_counts: dict[str, int] = {}
    for ch in individual_changes:
        type_counts[ch.entity_type] = type_counts.get(ch.entity_type, 0) + 1
    logger.info(
        "_materialize_individual_changes: found %d individual changes %s for ledger=%s",
        len(individual_changes), type_counts, ledger_id,
    )

    # 3. Apply each change to the snapshot
    for change in individual_changes:
        key = _ENTITY_TYPE_TO_SNAPSHOT_KEY.get(change.entity_type)
        if key is None:
            continue
        arr: list[dict[str, Any]] = snapshot.get(key) or []  # type: ignore[assignment]
        sync_id = change.entity_sync_id

        if change.action == "delete":
            arr = [e for e in arr if e.get("syncId") != sync_id]
        else:
            # upsert
            payload = change.payload_json
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                continue
            # Ensure syncId is set
            payload.setdefault("syncId", sync_id)
            found = False
            for i, e in enumerate(arr):
                if e.get("syncId") == sync_id:
                    arr[i] = payload
                    found = True
                    break
            if not found:
                arr.append(payload)

        snapshot[key] = arr

    logger.info(
        "_materialize_individual_changes: after apply — items=%d accounts=%d categories=%d tags=%d for ledger=%s",
        len(snapshot.get("items") or []),
        len(snapshot.get("accounts") or []),
        len(snapshot.get("categories") or []),
        len(snapshot.get("tags") or []),
        ledger_id,
    )

    # 4. Write updated snapshot as a new SyncChange
    now = datetime.now(timezone.utc)
    db.add(SyncChange(
        user_id=db.scalar(select(Ledger.user_id).where(Ledger.id == ledger_id)) or user_id,
        ledger_id=ledger_id,
        entity_type="ledger_snapshot",
        entity_sync_id=db.scalar(select(Ledger.external_id).where(Ledger.id == ledger_id)) or "",
        action="upsert",
        payload_json={
            "content": json.dumps(snapshot, ensure_ascii=False),
            "metadata": {"source": "materialize_individual"},
        },
        updated_at=now,
        updated_by_device_id=device_id,
        updated_by_user_id=user_id,
    ))
    db.flush()


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_cursor_for_ledgers(db: Session, ledger_ids: list[str]) -> int:
    if not ledger_ids:
        return 0
    max_cursor = db.scalar(select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id.in_(ledger_ids)))
    return int(max_cursor or 0)


@router.post("/push", response_model=SyncPushResponse)
async def push_changes(
    req: SyncPushRequest,
    request: Request,
    _scopes: set[str] = Depends(require_scopes(SCOPE_APP_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncPushResponse:
    metrics.inc("beecount_sync_push_requests_total")
    device = db.scalar(
        select(Device).where(
            Device.id == req.device_id,
            Device.user_id == current_user.id,
            Device.revoked_at.is_(None),
        )
    )
    if not device:
        metrics.inc("beecount_sync_push_failed_total")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device")

    now = datetime.now(timezone.utc)
    device.last_seen_at = now

    accepted = 0
    rejected = 0
    conflict_count = 0
    conflict_samples: list[dict[str, Any]] = []
    max_cursor = 0
    touched_ledgers: dict[str, str] = {}
    ledgers_with_individual_changes: set[str] = set()  # ledger internal ids

    for change in req.changes:
        row = get_accessible_ledger_by_external_id(
            db,
            user_id=current_user.id,
            ledger_external_id=change.ledger_id,
            roles=READABLE_ROLES,
        )
        if row is None:
            existing_ledger = db.scalar(
                select(Ledger).where(Ledger.external_id == change.ledger_id).limit(1)
            )
            if existing_ledger is not None:
                metrics.inc("beecount_sync_push_failed_total")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No write access to ledger",
                )
            ledger = Ledger(user_id=current_user.id, external_id=change.ledger_id)
            db.add(ledger)
            db.flush()
            member = LedgerMember(
                ledger_id=ledger.id,
                user_id=current_user.id,
                role=ROLE_OWNER,
                status=ACTIVE_MEMBER_STATUS,
            )
            db.add(member)
            db.flush()
        else:
            ledger, member = row

        if member.role == ROLE_VIEWER or member.role not in WRITABLE_ROLES:
            metrics.inc("beecount_sync_push_failed_total")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Viewer cannot push changes")

        incoming_updated_at = _to_utc(change.updated_at)
        latest_entity_change = db.scalar(
            select(SyncChange)
            .where(
                SyncChange.ledger_id == ledger.id,
                SyncChange.entity_type == change.entity_type,
                SyncChange.entity_sync_id == change.entity_sync_id,
            )
            .order_by(SyncChange.change_id.desc())
            .limit(1)
        )

        if latest_entity_change and _to_utc(latest_entity_change.updated_at) > incoming_updated_at:
            rejected += 1
            conflict_count += 1
            sample = {
                "reason": "lww_rejected_older_change",
                "ledgerId": change.ledger_id,
                "entityType": change.entity_type,
                "entitySyncId": change.entity_sync_id,
                "existingChangeId": latest_entity_change.change_id,
            }
            if len(conflict_samples) < 20:
                conflict_samples.append(sample)
            db.add(
                AuditLog(
                    user_id=current_user.id,
                    ledger_id=ledger.id,
                    action="sync_conflict",
                    metadata_json={
                        **sample,
                        "incomingUpdatedAt": incoming_updated_at.isoformat(),
                        "existingUpdatedAt": _to_utc(latest_entity_change.updated_at).isoformat(),
                        "incomingDeviceId": req.device_id,
                    },
                )
            )
            continue

        if latest_entity_change and _to_utc(latest_entity_change.updated_at) == incoming_updated_at:
            db.add(
                AuditLog(
                    user_id=current_user.id,
                    ledger_id=ledger.id,
                    action="sync_conflict",
                    metadata_json={
                        "reason": "lww_same_timestamp_accept_latest_arrival",
                        "ledgerId": change.ledger_id,
                        "entityType": change.entity_type,
                        "entitySyncId": change.entity_sync_id,
                        "existingChangeId": latest_entity_change.change_id,
                        "incomingDeviceId": req.device_id,
                    },
                )
            )

        row_change = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type=change.entity_type,
            entity_sync_id=change.entity_sync_id,
            action=change.action,
            payload_json=change.payload,
            updated_at=incoming_updated_at,
            updated_by_device_id=req.device_id,
            updated_by_user_id=current_user.id,
        )
        db.add(row_change)
        db.flush()

        accepted += 1
        max_cursor = max(max_cursor, row_change.change_id)
        touched_ledgers[ledger.external_id] = ledger.id
        if change.entity_type in _INDIVIDUAL_ENTITY_TYPES:
            ledgers_with_individual_changes.add(ledger.id)
    if max_cursor == 0:
        memberships = list_accessible_memberships(db, user_id=current_user.id, roles=READABLE_ROLES)
        max_cursor = _max_cursor_for_ledgers(db, [ledger.id for ledger, _ in memberships])

    # Materialize individual entity changes into snapshot so Web can see them
    for ledger_ext_id, ledger_id in touched_ledgers.items():
        if ledger_id not in ledgers_with_individual_changes:
            continue
        _materialize_individual_changes(
            db,
            ledger_id=ledger_id,
            device_id=req.device_id,
            user_id=current_user.id,
        )
    if touched_ledgers:
        db.flush()
        # Update max_cursor to include the new snapshot changes
        new_max = _max_cursor_for_ledgers(db, list(touched_ledgers.values()))
        max_cursor = max(max_cursor, new_max)

    db.commit()

    if touched_ledgers:
        ws_manager = request.app.state.ws_manager
        for ledger_external_id, ledger_id in touched_ledgers.items():
            member_user_ids = db.scalars(
                select(LedgerMember.user_id).where(
                    LedgerMember.ledger_id == ledger_id,
                    LedgerMember.status == ACTIVE_MEMBER_STATUS,
                )
            ).all()
            for member_user_id in set(member_user_ids):
                await ws_manager.broadcast_to_user(
                    member_user_id,
                    {
                        "type": "sync_change",
                        "ledgerId": ledger_external_id,
                        "serverCursor": max_cursor,
                        "serverTimestamp": now.isoformat(),
                    },
                )

    return SyncPushResponse(
        accepted=accepted,
        rejected=rejected,
        conflict_count=conflict_count,
        conflict_samples=conflict_samples,
        server_cursor=max_cursor,
        server_timestamp=now,
    )


@router.get("/pull", response_model=SyncPullResponse)
def pull_changes(
    since: int = Query(default=0, ge=0),
    device_id: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    _scopes: set[str] = Depends(require_scopes(SCOPE_APP_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncPullResponse:
    metrics.inc("beecount_sync_pull_requests_total")
    heartbeat_updated = False
    if device_id:
        device = db.scalar(
            select(Device).where(
                Device.id == device_id,
                Device.user_id == current_user.id,
                Device.revoked_at.is_(None),
            )
        )
        if not device:
            metrics.inc("beecount_sync_pull_failed_total")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device")
        device.last_seen_at = datetime.now(timezone.utc)
        heartbeat_updated = True

    memberships = list_accessible_memberships(db, user_id=current_user.id, roles=READABLE_ROLES)
    ledger_ids = [ledger.id for ledger, _ in memberships]
    if not ledger_ids:
        if heartbeat_updated:
            db.commit()
        return SyncPullResponse(changes=[], server_cursor=since, has_more=False)

    query = (
        select(SyncChange, Ledger.external_id)
        .join(Ledger, SyncChange.ledger_id == Ledger.id)
        .where(
            SyncChange.ledger_id.in_(ledger_ids),
            SyncChange.change_id > since,
        )
        .order_by(SyncChange.change_id.asc())
        .limit(limit + 1)
    )
    if device_id:
        query = query.where(SyncChange.updated_by_device_id != device_id)

    rows = db.execute(query).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    changes: list[SyncChangeOut] = []
    server_cursor = since
    per_ledger_cursor: dict[str, int] = {}

    for change, ledger_external_id in rows:
        server_cursor = max(server_cursor, change.change_id)
        current_cursor = per_ledger_cursor.get(ledger_external_id, 0)
        per_ledger_cursor[ledger_external_id] = max(current_cursor, change.change_id)
        changes.append(
            SyncChangeOut(
                change_id=change.change_id,
                ledger_id=ledger_external_id,
                entity_type=change.entity_type,
                entity_sync_id=change.entity_sync_id,
                action=cast("Any", change.action),
                payload=change.payload_json,
                updated_at=change.updated_at,
                updated_by_device_id=change.updated_by_device_id,
            )
        )

    if device_id and per_ledger_cursor:
        now = datetime.now(timezone.utc)
        for ledger_external_id, last_cursor in per_ledger_cursor.items():
            existing = db.scalar(
                select(SyncCursor).where(
                    SyncCursor.user_id == current_user.id,
                    SyncCursor.device_id == device_id,
                    SyncCursor.ledger_external_id == ledger_external_id,
                )
            )
            if existing:
                existing.last_cursor = max(existing.last_cursor, last_cursor)
                existing.updated_at = now
            else:
                db.add(
                    SyncCursor(
                        user_id=current_user.id,
                        device_id=device_id,
                        ledger_external_id=ledger_external_id,
                        last_cursor=last_cursor,
                        updated_at=now,
                    )
                )
        db.commit()
    elif heartbeat_updated:
        db.commit()

    return SyncPullResponse(changes=changes, server_cursor=server_cursor, has_more=has_more)


@router.get("/full", response_model=SyncFullResponse)
def full_snapshot(
    ledger_id: str,
    _scopes: set[str] = Depends(require_scopes(SCOPE_APP_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncFullResponse:
    memberships = list_accessible_memberships(db, user_id=current_user.id, roles=READABLE_ROLES)
    ledger_ids = [ledger.id for ledger, _ in memberships]
    latest_cursor = _max_cursor_for_ledgers(db, ledger_ids)

    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
        roles=READABLE_ROLES,
    )
    if row is None:
        return SyncFullResponse(ledger_id=ledger_id, snapshot=None, latest_cursor=latest_cursor)
    ledger, _ = row
    latest_change = db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "ledger_snapshot",
        )
        .order_by(SyncChange.change_id.desc())
    )
    if not latest_change or latest_change.action == "delete":
        return SyncFullResponse(ledger_id=ledger_id, snapshot=None, latest_cursor=latest_cursor)

    return SyncFullResponse(
        ledger_id=ledger_id,
        latest_cursor=latest_cursor,
        snapshot=SyncChangeOut(
            change_id=latest_change.change_id,
            ledger_id=ledger_id,
            entity_type=latest_change.entity_type,
            entity_sync_id=latest_change.entity_sync_id,
            action=cast("Any", latest_change.action),
            payload=latest_change.payload_json,
            updated_at=latest_change.updated_at,
            updated_by_device_id=latest_change.updated_by_device_id,
        ),
    )


@router.get("/ledgers", response_model=list[SyncLedgerOut])
def list_ledgers(
    _scopes: set[str] = Depends(require_scopes(SCOPE_APP_WRITE)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncLedgerOut]:
    memberships = list_accessible_memberships(db, user_id=current_user.id, roles=READABLE_ROLES)
    out: list[SyncLedgerOut] = []
    for ledger, membership in memberships:
        latest_change = db.scalar(
            select(SyncChange)
            .where(
                SyncChange.ledger_id == ledger.id,
                SyncChange.entity_type == "ledger_snapshot",
            )
            .order_by(SyncChange.change_id.desc())
        )
        if not latest_change or latest_change.action == "delete":
            continue

        metadata: dict[str, Any] = {}
        size = 0
        updated_at = latest_change.updated_at
        payload = latest_change.payload_json
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, str):
                size = len(content.encode("utf-8"))
            meta = payload.get("metadata")
            if isinstance(meta, dict):
                metadata = meta

        out.append(
            SyncLedgerOut(
                ledger_id=ledger.external_id,
                path=ledger.external_id,
                updated_at=updated_at,
                size=size,
                metadata=metadata,
                role=cast("Any", membership.role),
            )
        )
    return out
