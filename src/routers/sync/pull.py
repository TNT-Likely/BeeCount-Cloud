"""GET /sync/pull —— mobile / web 按 cursor 拉取 SyncChange。

用于 mobile 增量同步 + web 的 WebSocket 推送掉线后的 catch-up。

user-global 重构后:一条 pull 同时返回 ledger-scope + user-scope changes。
user-scope change 在响应里 ledger_id = sentinel '__user_global__',scope='user'。
mobile 按 scope 决定 apply 路径(写主表),不再借车依附任何 ledger。
"""
from __future__ import annotations

from sqlalchemy import and_, or_

from ._shared import *  # noqa: F401,F403 — 拉取所有 imports / helpers / router / constants


# user-scope change 在 pull 响应里的 ledger_id 用这个 sentinel 标识。mobile 端
# 用同一字符串当 sync_cursors 的 ledger_external_id key,实现独立 cursor 跟踪。
USER_GLOBAL_LEDGER_SENTINEL = "__user_global__"


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
    # 无任何 ledger 的用户仍可能有 user-scope changes(场景理论上不存在,但
    # 协议上允许),所以不在此处早返。

    # LEFT JOIN Ledger:user-scope change 的 ledger_id IS NULL,INNER JOIN
    # 会把这些行过滤掉。
    # 过滤:
    #   - ledger-scope(scope='ledger'):必须属于 caller 可见 ledger
    #   - user-scope(scope='user'):必须 user_id == caller
    # `column.in_([])` 在 SQLAlchemy 2.0+ 编译成 false 表达式,不会 crash;
    # 用户无任何 ledger 时 ledger-scope 子句自然过滤掉所有行。
    scope_filter = or_(
        and_(
            SyncChange.scope == "ledger",
            SyncChange.ledger_id.in_(ledger_ids),
        ),
        and_(
            SyncChange.scope == "user",
            SyncChange.user_id == current_user.id,
        ),
    )
    query = (
        select(SyncChange, Ledger.external_id)
        .outerjoin(Ledger, SyncChange.ledger_id == Ledger.id)
        .where(
            scope_filter,
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
        # user-scope change 的 ledger_id 字段填 sentinel,让 mobile 把它当独立
        # 频道跟踪 cursor。
        out_ledger_id = (
            USER_GLOBAL_LEDGER_SENTINEL
            if change.scope == "user"
            else (ledger_external_id or "")
        )
        current_cursor = per_ledger_cursor.get(out_ledger_id, 0)
        per_ledger_cursor[out_ledger_id] = max(current_cursor, change.change_id)
        changes.append(
            SyncChangeOut(
                change_id=change.change_id,
                ledger_id=out_ledger_id,
                entity_type=change.entity_type,
                entity_sync_id=change.entity_sync_id,
                action=cast("Any", change.action),
                payload=change.payload_json,
                updated_at=change.updated_at,
                updated_by_device_id=change.updated_by_device_id,
                scope=change.scope,
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


