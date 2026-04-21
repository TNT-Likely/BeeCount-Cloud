"""GET /sync/pull —— mobile / web 按 cursor 拉取 SyncChange。

用于 mobile 增量同步 + web 的 WebSocket 推送掉线后的 catch-up。
"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — 拉取所有 imports / helpers / router / constants

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

    if changes:
        logger.info(
            "sync.pull user=%s device=%s since=%d returned=%d hasMore=%s",
            current_user.id,
            device_id,
            since,
            len(changes),
            has_more,
        )
    return SyncPullResponse(changes=changes, server_cursor=server_cursor, has_more=has_more)


