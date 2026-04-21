"""POST /sync/push —— mobile 批量推送本地变更。

每条 change:LWW 决胜(updated_at + device_id tie-break)→ 写 SyncChange 行
→ 走 sync_applier.apply_change_to_projection 刷 projection。整批单事务
提交,一条坏 change 炸会带 traceback 日志并 rollback 整批。
"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — 拉取所有 imports / helpers / router / constants

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
            logger.warning(
                "sync.push.conflict entity=%s action=%s ledger=%s sync_id=%s device=%s "
                "incoming_ts=%s existing_ts=%s existing_change=%d",
                change.entity_type,
                change.action,
                change.ledger_id,
                change.entity_sync_id,
                req.device_id,
                incoming_updated_at.isoformat(),
                existing_tuple[0].isoformat(),
                latest_entity_change.change_id,
            )
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
            logger.debug(
                "sync.push.replay entity=%s action=%s ledger=%s sync_id=%s device=%s",
                change.entity_type,
                change.action,
                change.ledger_id,
                change.entity_sync_id,
                req.device_id,
            )
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

        # 方案 B:projection 随 push 同事务刷新。不再写 ledger_snapshot 行。
        if change.entity_type in INDIVIDUAL_ENTITY_TYPES:
            # lock 一次/账本,避免两个 push 并发走同个 ledger 的 cascade
            lock_ledger_for_materialize(db, ledger.id)
            try:
                apply_change_to_projection(
                    db,
                    ledger_id=ledger.id,
                    ledger_owner_id=ledger.user_id,
                    change=row_change,
                )
            except Exception:
                # 批量 push 里一条坏 change 炸了要看得到是哪一条;不然 500 只见
                # generic Internal server error,得上生产日志面板才能查。
                logger.exception(
                    "sync.push.apply_failed entity=%s action=%s ledger=%s sync_id=%s "
                    "change_id=%d payload=%s",
                    change.entity_type,
                    change.action,
                    change.ledger_id,
                    change.entity_sync_id,
                    row_change.change_id,
                    change.payload,
                )
                raise

        accepted += 1
        max_cursor = max(max_cursor, row_change.change_id)
        touched_ledgers[ledger.external_id] = ledger.id
        logger.info(
            "sync.push.accept entity=%s action=%s ledger=%s sync_id=%s change_id=%d device=%s user=%s",
            change.entity_type,
            change.action,
            change.ledger_id,
            change.entity_sync_id,
            row_change.change_id,
            req.device_id,
            current_user.id,
        )
    if max_cursor == 0:
        accessible = list_accessible_ledgers(db, user_id=current_user.id)
        max_cursor = _max_cursor_for_ledgers(db, [lg.id for lg in accessible])

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

    logger.info(
        "sync.push user=%s device=%s accepted=%d rejected=%d conflict=%d ledgers=%d",
        current_user.id,
        req.device_id,
        accepted,
        rejected,
        conflict_count,
        len(touched_ledgers),
    )
    return SyncPushResponse(
        accepted=accepted,
        rejected=rejected,
        conflict_count=conflict_count,
        conflict_samples=conflict_samples,
        server_cursor=max_cursor,
        server_timestamp=now,
    )


