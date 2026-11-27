"""POST /sync/push —— mobile 批量推送本地变更。

每条 change:LWW 决胜(updated_at + device_id tie-break)→ 写 SyncChange 行
→ 走 sync_applier.apply_change_to_projection 刷 projection。整批单事务
提交,一条坏 change 炸会带 traceback 日志并 rollback 整批。
"""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from ...concurrency import lock_sync_writer
from ...models import SyncOperationReceipt, TransactionMoveRetirement
from ...recurring_handoff import check_mobile_push
from ...schemas import SyncChangeOut, SyncPushItemResult
from ...services.sync_health import observe_server_errors, record_failure, record_recovery
from ...sync_applier import merge_with_existing, merge_with_existing_user
from ._shared import (
    INDIVIDUAL_ENTITY_TYPES,
    SCOPE_APP_WRITE,
    USER_GLOBAL_ENTITY_TYPES,
    Any,
    AuditLog,
    Depends,
    Device,
    HTTPException,
    Ledger,
    LedgerMember,
    ReadTxProjection,
    Request,
    Session,
    SyncChange,
    SyncPushRequest,
    SyncPushResponse,
    User,
    _max_cursor_for_ledgers,
    _to_utc,
    apply_change_to_projection,
    apply_user_change_to_projection,
    datetime,
    get_accessible_ledger_by_external_id,
    get_current_user,
    get_db,
    list_accessible_ledgers,
    lock_ledger_for_materialize,
    logger,
    metrics,
    require_scopes,
    router,
    select,
    status,
    sync_client_version_check,
    timedelta,
    timezone,
)


def _winner(db: Session, row: SyncChange, ledger: Ledger | None) -> SyncChangeOut:
    """Include the materialized row even when its last event was a partial edit."""
    payload = dict(row.payload_json or {})
    if row.action != "delete":
        if row.scope == "user":
            payload.update(merge_with_existing_user(db, row.entity_type, row.user_id, row.entity_sync_id, {}))
        elif ledger is not None and row.entity_type == "ledger":
            payload.update(ledgerName=ledger.name, currency=ledger.currency, monthStartDay=ledger.month_start_day)
        elif ledger is not None:
            payload.update(merge_with_existing(db, row.entity_type, ledger.id, row.entity_sync_id, {}))
    return SyncChangeOut(
        change_id=row.change_id, ledger_id=ledger.external_id if ledger else "__user_global__",
        entity_type=row.entity_type, entity_sync_id=row.entity_sync_id, action=row.action,
        payload=payload, updated_at=_to_utc(row.updated_at),
        updated_by_device_id=row.updated_by_device_id, scope=row.scope,
    )


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

    sync_client_version_check(request, device, db)

    with observe_server_errors(db, device.id, "push"):
        return await _push_for_device(req, request, current_user, device, db)


async def _push_for_device(
    req: SyncPushRequest, request: Request, current_user: User, device: Device, db: Session,
) -> SyncPushResponse:
    now = datetime.now(timezone.utc)
    device.last_seen_at = now

    accepted = 0
    rejected = 0
    conflict_count = 0
    conflict_samples: list[dict[str, Any]] = []
    results: list[SyncPushItemResult] = []
    max_cursor = 0
    touched_ledgers: dict[str, str] = {}
    # 共享账本 Phase 1:user-global category/account/tag 变更要 fan-out 给
    # 该 user 作为 owner 的所有共享账本的非 owner member。收集后 commit 后广播。
    pending_shared_resource_events: list[dict[str, Any]] = []

    # 是否触动 user-global —— 触动了就额外给 owner 广播一条 __user_global__
    # 通道的 sync_change(让其他设备拉这一份)。
    touched_user_global = False

    requested_ledgers = {
        change.ledger_id for change in req.changes
        if change.entity_type not in USER_GLOBAL_ENTITY_TYPES and change.ledger_id is not None
    }
    locked_ids: set[str] = set()
    preloaded_ids: set[str] = set()
    retired_transactions: set[tuple[str, str]] = set()
    if req.changes:
        await run_in_threadpool(lock_sync_writer, db)
    if requested_ledgers:
        ledger_ids = sorted(
            ledger.id for ledger in list_accessible_ledgers(db, user_id=current_user.id)
            if ledger.external_id in requested_ledgers
        )
        for ledger_id in ledger_ids:
            await run_in_threadpool(lock_ledger_for_materialize, db, ledger_id)
            locked_ids.add(ledger_id)
        preloaded_ids.update(ledger_ids)

        check_mobile_push(db, ledger_ids, req.changes, request.headers.get("X-BeeCount-Recurring"))

        tx_ids = list({
            change.entity_sync_id for change in req.changes if change.entity_type == "transaction"
        })
        for start in range(0, len(tx_ids), 500):
            rows = db.execute(
                select(TransactionMoveRetirement.ledger_id, TransactionMoveRetirement.sync_id)
                .where(
                    TransactionMoveRetirement.ledger_id.in_(ledger_ids),
                    TransactionMoveRetirement.sync_id.in_(tx_ids[start:start + 500]),
                )
            )
            retired_transactions.update((row.ledger_id, row.sync_id) for row in rows)

    for change in req.changes:
        is_user_global = change.entity_type in USER_GLOBAL_ENTITY_TYPES
        receipt = SyncPushItemResult(status="rejected")
        if req.return_results:
            results.append(receipt)
        previous = (
            db.get(SyncOperationReceipt, (req.device_id, change.operation_id))
            if change.operation_id else None
        )

        # ============================================================
        # 路径分流:user-global vs ledger-scoped
        # ============================================================
        # user-global(category/account/tag)在新协议下不依附 ledger:
        #   - SyncChange.user_id = current_user.id(真请求方,非账本 owner)
        #   - SyncChange.ledger_id = NULL
        #   - SyncChange.scope = 'user'
        #   - LWW 按 (user_id, scope='user', entity_type, entity_sync_id) 决胜
        #   - 不做 ledger 自动创建(user-global 不挂账本)
        # ledger-scoped(transaction/budget/ledger)沿用老路径。
        # ============================================================

        ledger: Ledger | None = None  # 仅 ledger-scoped 用
        if is_user_global:
            # 老 mobile 可能填了 change.ledger_id(借车协议),server 端忽略;
            # 不做 get_accessible_ledger 校验。
            pass
        else:
            if change.ledger_id is None:
                # 老协议契约要求 ledger-scoped 必须带 ledger_id;新协议同样要求。
                logger.warning(
                    "sync.push.skip ledger-scoped change missing ledger_id "
                    "entity=%s sync_id=%s",
                    change.entity_type,
                    change.entity_sync_id,
                )
                receipt.reason = "missing_ledger"
                rejected += 1
                continue
            row = get_accessible_ledger_by_external_id(
                db,
                user_id=current_user.id,
                ledger_external_id=change.ledger_id,
            )
            if row is None:
                # A replay cannot recreate a ledger that was subsequently removed
                # or made inaccessible to this device.
                if previous is not None:
                    receipt.reason = "ledger_unavailable"
                    rejected += 1
                    continue
                # Caller doesn't own a ledger with this external_id — auto-create.
                # The (user_id, external_id) unique constraint keeps per-user ids
                # isolated, so two users can independently own "default".
                ledger = Ledger(user_id=current_user.id, external_id=change.ledger_id)
                db.add(ledger)
                db.flush()
                # 共享账本 Phase 1:auto-create 时同步建 owner LedgerMember 行
                db.add(LedgerMember(
                    ledger_id=ledger.id,
                    user_id=current_user.id,
                    role="owner",
                    joined_at=now,
                ))
                db.flush()
            else:
                ledger, caller_role = row
                # 共享账本 Phase 1:Editor 只能推 transaction / budget;不能推
                # ledger / ledger_snapshot(账本 meta 改 / 删账本属 owner 操作)。
                # 老协议没有 entity_type='ledger_snapshot' 路径(0011 后),所以
                # 实际只挡 'ledger'。
                if caller_role != "owner" and change.entity_type in ("ledger", "ledger_snapshot"):
                    logger.warning(
                        "sync.push.reject ledger-meta change from non-owner "
                        "user=%s ledger=%s role=%s entity=%s",
                        current_user.id, change.ledger_id, caller_role, change.entity_type,
                    )
                    receipt.reason = "ledger_owner_required"
                    rejected += 1
                    continue

        if ledger is not None and ledger.id not in locked_ids:
            await run_in_threadpool(lock_ledger_for_materialize, db, ledger.id)
            locked_ids.add(ledger.id)

        # Clamp incoming updated_at to the server clock to neutralize client
        # clock skew. Without this, a mobile device whose local clock is ahead
        # of the server by minutes/hours will always win LWW against a legitimate
        # web write that used server time — silently overriding the user's latest
        # change. Cap the incoming timestamp at (server_now + 5s); legitimate
        # small skew still passes, intentional-or-accidental future dates don't.
        raw_updated_at = _to_utc(change.updated_at)
        max_allowed = now + timedelta(seconds=5)
        incoming_updated_at = min(raw_updated_at, max_allowed)

        # ============================================================
        # LWW lookup —— scope-aware
        # ============================================================
        if is_user_global:
            latest_entity_change = db.scalar(
                select(SyncChange)
                .where(
                    SyncChange.user_id == current_user.id,
                    SyncChange.scope == "user",
                    SyncChange.entity_type == change.entity_type,
                    SyncChange.entity_sync_id == change.entity_sync_id,
                )
                .order_by(SyncChange.change_id.desc())
                .limit(1)
            )
        else:
            assert ledger is not None
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

        if previous is not None:
            if (previous.entity_type != change.entity_type or previous.entity_sync_id != change.entity_sync_id
                    or previous.ledger_id != (ledger.id if ledger else None) or previous.action != change.action):
                rejected += 1
                receipt.reason = "operation_identity_mismatch"
                continue
            accepted += 1
            receipt.status = "accepted"
            receipt.change_id = previous.change_id
            if (req.return_results and latest_entity_change is not None
                    and (previous.change_id != latest_entity_change.change_id or change.changed_fields is not None)):
                receipt.winner = _winner(db, latest_entity_change, ledger)
            continue

        if (
            ledger is not None
            and change.entity_type == "transaction"
            and (
                (ledger.id, change.entity_sync_id) in retired_transactions
                or (
                    ledger.id not in preloaded_ids
                    and db.get(TransactionMoveRetirement, (ledger.id, change.entity_sync_id)) is not None
                )
            )
        ):
            receipt.status = "conflict"
            receipt.reason = "transaction_moved"
            if req.return_results and latest_entity_change is not None and latest_entity_change.action == "delete":
                receipt.winner = _winner(db, latest_entity_change, ledger)
                receipt.change_id = latest_entity_change.change_id
            rejected += 1
            conflict_count += 1
            if len(conflict_samples) < 20:
                conflict_samples.append({
                    "reason": "transaction_moved",
                    "ledgerId": change.ledger_id,
                    "entityType": "transaction",
                    "entitySyncId": change.entity_sync_id,
                })
            continue

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

        conflict_reason = None
        if change.restore_after_change_id is not None and (
            latest_entity_change is None or latest_entity_change.action != "delete"
            or latest_entity_change.change_id != change.restore_after_change_id or change.action != "upsert"
        ):
            receipt.reason = "restore_version_mismatch"
            if latest_entity_change is not None:
                receipt.change_id = latest_entity_change.change_id
                if req.return_results:
                    receipt.winner = _winner(db, latest_entity_change, ledger)
            rejected += 1
            continue
        if (latest_entity_change is not None and latest_entity_change.action == "delete"
                and change.action == "upsert" and change.entity_type in {"transaction", "category", "tag", "account", "budget"}):
            if change.entity_type == "transaction" and change.payload.get("restoredSnapshot") is True:
                conflict_reason = "restore_deleted_transaction"
            elif change.restore_after_change_id is None:
                conflict_reason = "entity_deleted"
        if existing_tuple is not None and existing_tuple > incoming_tuple and conflict_reason is None:
            conflict_reason = "lww_rejected_older_change"
        if conflict_reason is not None:
            assert latest_entity_change is not None and existing_tuple is not None
            rejected += 1
            conflict_count += 1
            sample = {
                "reason": conflict_reason,
                "ledgerId": change.ledger_id,
                "entityType": change.entity_type,
                "entitySyncId": change.entity_sync_id,
                "existingChangeId": latest_entity_change.change_id,
            }
            receipt.status = "conflict"
            receipt.reason = sample["reason"]
            receipt.change_id = latest_entity_change.change_id
            if req.return_results:
                receipt.winner = _winner(db, latest_entity_change, ledger)
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
                    ledger_id=ledger.id if ledger is not None else None,
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

        if not change.operation_id and existing_tuple is not None and existing_tuple == incoming_tuple:
            # Legacy clients identify retries by device and modification time.
            receipt.status = "accepted"
            receipt.change_id = latest_entity_change.change_id
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

        # ============================================================
        # SyncChange row + apply 路径
        # ============================================================
        if (change.changed_fields is not None and latest_entity_change is not None
                and latest_entity_change.action != "delete" and change.action != "delete" and change.entity_type in ("transaction", "ledger")
                and ledger is not None):
            # New clients also send the full row for older servers. Persist a complete
            # event so existing incremental readers receive the same state as a full read.
            patch = {key: change.payload[key] for key in change.changed_fields if key in change.payload}
            if change.entity_type == "transaction":
                change.payload = merge_with_existing(db, "transaction", ledger.id, change.entity_sync_id, patch)
            else:
                change.payload = {**_winner(db, latest_entity_change, ledger).payload, **patch}

        if is_user_global:
            row_change = SyncChange(
                user_id=current_user.id,       # 真请求方
                ledger_id=None,
                scope="user",
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
            try:
                apply_user_change_to_projection(
                    db,
                    user_id=current_user.id,
                    change=row_change,
                )
            except Exception:
                logger.exception(
                    "sync.push.apply_failed (user-scope) entity=%s action=%s "
                    "sync_id=%s change_id=%d payload=%s",
                    change.entity_type,
                    change.action,
                    change.entity_sync_id,
                    row_change.change_id,
                    change.payload,
                )
                raise
            touched_user_global = True
            # 共享账本 fan-out:只对 category/account/tag 三种 user-global 类型
            # 推 shared_resource_change(其他 user-global 类型如 device 等不外推)
            if change.entity_type in ("category", "account", "tag"):
                pending_shared_resource_events.append({
                    "resource_type": change.entity_type,
                    "action": change.action,
                    "sync_id": change.entity_sync_id,
                    "payload": change.payload or {"sync_id": change.entity_sync_id},
                })
        else:
            assert ledger is not None
            # §7 共享账本:mobile 历史路径未在本地 transactions.created_by_user_id
            # / last_edited_by_user_id 上回填(addTransaction / updateTransaction
            # 不写这两列),所以 EntitySerializer 序列化出来的 payload 缺这俩
            # 字段。server 这里兜底注入,让 SyncChange.payload_json 保留正确身份;
            # 否则 pull 端拿到的 payload 没有 user id,mobile 本地 DB 的
            # created_by / last_edited 字段全空,UI 无法显示"X 创建 / Y 编辑"。
            #
            # createdByUserId:first-write-wins — 已存在 read_tx_projection
            # 行就保留(避免 B 编辑 A 创建的 tx 时把 created 改成 B),否则用
            # 当前 actor。
            # updatedByUserId:始终用当前 actor(谁 push 就是谁编辑)。
            if change.entity_type == "transaction" and isinstance(change.payload, dict):
                if not change.payload.get("updatedByUserId"):
                    change.payload["updatedByUserId"] = current_user.id
                if not change.payload.get("createdByUserId"):
                    existing_creator = db.scalar(
                        select(ReadTxProjection.created_by_user_id).where(
                            ReadTxProjection.ledger_id == ledger.id,
                            ReadTxProjection.sync_id == change.entity_sync_id,
                        )
                    )
                    change.payload["createdByUserId"] = (
                        existing_creator or current_user.id
                    )
            row_change = SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                scope="ledger",
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
            touched_ledgers[ledger.external_id] = ledger.id

        if change.operation_id:
            db.add(SyncOperationReceipt(
                device_id=req.device_id, operation_id=change.operation_id,
                change_id=row_change.change_id, ledger_id=ledger.id if ledger else None,
                entity_type=change.entity_type, entity_sync_id=change.entity_sync_id,
                action=change.action,
            ))
            db.flush()
        accepted += 1
        receipt.status = "accepted"
        receipt.change_id = row_change.change_id
        if req.return_results and change.changed_fields is not None:
            receipt.winner = _winner(db, row_change, ledger)
        max_cursor = max(max_cursor, row_change.change_id)
        logger.info(
            "sync.push.accept entity=%s action=%s ledger=%s sync_id=%s change_id=%d device=%s user=%s scope=%s",
            change.entity_type,
            change.action,
            change.ledger_id,
            change.entity_sync_id,
            row_change.change_id,
            req.device_id,
            current_user.id,
            row_change.scope,
        )
    if max_cursor == 0:
        accessible = list_accessible_ledgers(db, user_id=current_user.id)
        max_cursor = _max_cursor_for_ledgers(db, [lg.id for lg in accessible])

    if rejected > conflict_count:
        record_failure(db, req.device_id, "push", "changes_rejected")
    elif accepted:
        record_recovery(db, req.device_id, "push")
    db.commit()

    if touched_ledgers:
        ws_manager = request.app.state.ws_manager
        # 共享账本 Phase 1:fan-out 给该 ledger 所有 member(LedgerMember 表),
        # 包含 owner 自己(其他设备需要拉)+ Editor 等。client 用 device_id 去重。
        from ...websocket_manager import broadcast_to_ledger
        for ledger_external_id, ledger_id in touched_ledgers.items():
            await broadcast_to_ledger(
                db=db,
                ws_manager=ws_manager,
                ledger_id=ledger_id,
                payload={
                    "type": "sync_change",
                    "ledgerId": ledger_external_id,
                    "serverCursor": max_cursor,
                    "serverTimestamp": now.isoformat(),
                },
            )

    if touched_user_global:
        # user-global change broadcast 走 sentinel ledger external id,mobile/web
        # 收到后会去 pull __user_global__ 拉这一份增量。
        ws_manager = request.app.state.ws_manager
        await ws_manager.broadcast_to_user(
            current_user.id,
            {
                "type": "sync_change",
                "ledgerId": "__user_global__",
                "serverCursor": max_cursor,
                "serverTimestamp": now.isoformat(),
            },
        )

    if pending_shared_resource_events:
        # 共享账本 fan-out:对 caller 作为 owner 的所有共享账本,推该事件给
        # 非 owner member。Editor 收到后更新本地 SharedLedger* 镜像。
        ws_manager = request.app.state.ws_manager
        from sqlalchemy import func as _func

        from ...models import Ledger as _L
        from ...models import LedgerMember as _LM
        rows = db.execute(
            select(_L.id, _L.external_id)
            .join(_LM, _LM.ledger_id == _L.id)
            .where(_L.user_id == current_user.id)
            .group_by(_L.id, _L.external_id)
            .having(_func.count(_LM.user_id) > 1)
        ).all()
        from ...ledger_access import list_ledger_members
        for ledger_id, ledger_external_id in rows:
            for member_user_id, role in list_ledger_members(db, ledger_id=ledger_id):
                if role == "owner":
                    continue
                for ev in pending_shared_resource_events:
                    await ws_manager.broadcast_to_user(member_user_id, {
                        "type": "shared_resource_change",
                        "ledgerId": ledger_external_id,
                        "resourceType": ev["resource_type"],
                        "action": ev["action"],
                        "payload": {**ev["payload"], "syncId": ev["sync_id"]},
                    }, shared_owner_id=current_user.id)

    logger.info(
        "sync.push user=%s device=%s accepted=%d rejected=%d conflict=%d ledgers=%d user_global=%s",
        current_user.id,
        req.device_id,
        accepted,
        rejected,
        conflict_count,
        len(touched_ledgers),
        touched_user_global,
    )
    return SyncPushResponse(
        accepted=accepted,
        rejected=rejected,
        conflict_count=conflict_count,
        conflict_samples=conflict_samples,
        results=results if req.return_results else None,
        server_cursor=max_cursor,
        server_timestamp=now,
    )


