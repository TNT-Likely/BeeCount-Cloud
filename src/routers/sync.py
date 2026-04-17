import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..concurrency import lock_ledger_for_materialize
from ..database import get_db
from ..deps import get_current_user, require_any_scopes, require_scopes
from ..ledger_access import (
    get_accessible_ledger_by_external_id,
    list_accessible_ledgers,
)
from ..metrics import metrics
from ..models import AuditLog, Device, Ledger, SyncChange, SyncCursor, User
from ..schemas import (
    SyncChangeOut,
    SyncFullResponse,
    SyncLedgerOut,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ

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
    # 0. Serialize materialization per ledger to prevent two concurrent pushes
    #    from each reading the same snapshot and writing competing new ones.
    lock_ledger_for_materialize(db, ledger_id)

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
            old_entity: dict[str, Any] | None = None
            for i, e in enumerate(arr):
                if e.get("syncId") == sync_id:
                    old_entity = e
                    # 合并而不是整体替换：mobile 的增量 push 只带发生变化的核心
                    # 字段（如 name / icon），不会每次都带上 iconCloudFileId /
                    # iconCloudSha256 这种"得先上传文件才知道"的字段。直接替换
                    # 会把 snapshot 里已有的云端图标引用弄丢。合并时 payload 里
                    # 未出现的键沿用旧值，显式 null 也视为保留（无法区分"缺失"
                    # vs "null"），只覆盖明确带有非空值的字段。
                    merged = {**e, **{k: v for k, v in payload.items() if v is not None}}
                    # 强制采用 payload 的 syncId（保证一致），并确保核心字段若
                    # payload 显式传空字符串则仍覆盖（name 改空的边缘情况）。
                    if "name" in payload and isinstance(payload.get("name"), str):
                        merged["name"] = payload["name"]
                    arr[i] = merged
                    found = True
                    break
            if not found:
                arr.append(payload)

            # 重命名级联：当 account / category / tag 的 name 变了，snapshot.items
            # 里以前引用旧 name 的 tx 也要跟着改。否则 web 查 tx 列表返回的
            # categoryName / accountName / tags 字符串就是老名字，表现为
            # "mobile 改了名，web 上 tx 里的标签/分类还是老名字（看起来是缓存）"。
            if old_entity is not None and change.entity_type in {"account", "category", "tag"}:
                old_name = str(old_entity.get("name") or "").strip()
                new_name = str(payload.get("name") or "").strip()
                if old_name and new_name and old_name != new_name:
                    items_arr = snapshot.get("items") or []
                    if change.entity_type == "category":
                        old_kind = str(old_entity.get("kind") or "").strip()
                        for tx in items_arr:
                            if tx.get("categoryName") == old_name and (
                                not old_kind or tx.get("categoryKind") == old_kind
                            ):
                                tx["categoryName"] = new_name
                    elif change.entity_type == "account":
                        for tx in items_arr:
                            if tx.get("accountName") == old_name:
                                tx["accountName"] = new_name
                            if tx.get("fromAccountName") == old_name:
                                tx["fromAccountName"] = new_name
                            if tx.get("toAccountName") == old_name:
                                tx["toAccountName"] = new_name
                    elif change.entity_type == "tag":
                        for tx in items_arr:
                            raw_tags = tx.get("tags")
                            if not raw_tags or not isinstance(raw_tags, str):
                                continue
                            parts = [
                                new_name if part.strip() == old_name else part
                                for part in raw_tags.split(",")
                            ]
                            tx["tags"] = ",".join(p for p in parts if p.strip())

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
        )
        if row is None:
            # Caller doesn't own a ledger with this external_id — auto-create.
            # The (user_id, external_id) unique constraint keeps per-user ids
            # isolated, so two users can independently own "default".
            ledger = Ledger(user_id=current_user.id, external_id=change.ledger_id)
            db.add(ledger)
            db.flush()
        else:
            ledger, _ = row

        # Clamp incoming updated_at to the server clock to neutralize client
        # clock skew. Without this, a mobile device whose local clock is ahead
        # of the server by minutes/hours will always win LWW against a legitimate
        # web write that used server time — silently overriding the user's latest
        # change. Cap the incoming timestamp at (server_now + 5s); legitimate
        # small skew still passes, intentional-or-accidental future dates don't.
        raw_updated_at = _to_utc(change.updated_at)
        max_allowed = now + timedelta(seconds=5)
        incoming_updated_at = min(raw_updated_at, max_allowed)
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

        # Deterministic LWW with device_id tie-break:
        # compare (updated_at, device_id) tuples lexicographically so two servers
        # or retried calls produce the same winner regardless of arrival order.
        incoming_device_id = req.device_id or ""
        incoming_tuple = (incoming_updated_at, incoming_device_id)
        existing_tuple: tuple[datetime, str] | None = None
        if latest_entity_change:
            existing_tuple = (
                _to_utc(latest_entity_change.updated_at),
                latest_entity_change.updated_by_device_id or "",
            )

        if existing_tuple is not None and existing_tuple > incoming_tuple:
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
                        "existingUpdatedAt": existing_tuple[0].isoformat(),
                        "incomingDeviceId": req.device_id,
                        "existingDeviceId": existing_tuple[1],
                    },
                )
            )
            continue

        if existing_tuple is not None and existing_tuple == incoming_tuple:
            # Idempotent replay (same device, same timestamp) — don't duplicate.
            accepted += 1
            continue

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
        accessible = list_accessible_ledgers(db, user_id=current_user.id)
        max_cursor = _max_cursor_for_ledgers(db, [lg.id for lg in accessible])

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
        # Single-user-per-ledger: broadcast only to the owner.
        owner_user_ids = db.scalars(
            select(Ledger.user_id).where(Ledger.id.in_(list(touched_ledgers.values())))
        ).all()
        for owner_user_id in set(owner_user_ids):
            for ledger_external_id in touched_ledgers:
                await ws_manager.broadcast_to_user(
                    owner_user_id,
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
    _scopes: set[str] = Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)),
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

    accessible = list_accessible_ledgers(db, user_id=current_user.id)
    ledger_ids = [lg.id for lg in accessible]
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
    _scopes: set[str] = Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncFullResponse:
    accessible = list_accessible_ledgers(db, user_id=current_user.id)
    ledger_ids = [lg.id for lg in accessible]
    latest_cursor = _max_cursor_for_ledgers(db, ledger_ids)

    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
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
    _scopes: set[str] = Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncLedgerOut]:
    accessible = list_accessible_ledgers(db, user_id=current_user.id)
    out: list[SyncLedgerOut] = []
    for ledger in accessible:
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
                role=cast("Any", "owner"),
            )
        )
    return out
