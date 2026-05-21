"""Ledgers write endpoints.

POST / PATCH / DELETE for /ledgers/{ledger_id}/ledgers(ledgers 自身除外)。
依赖 `._shared` 里的 _commit_write / _prepare_write / normalize helper /
WRITE 响应表。Endpoint 自身只管参数校验 + mutate lambda 的构造。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...models import AttachmentFile
from ...services.data_cleanup.cleaner import _remove_empty_parents

router = APIRouter()


@router.post("/ledgers", response_model=WriteCommitMeta, responses=_WRITE_RESPONSES)
async def create_ledger(
    req: WriteLedgerCreateRequest,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    external_id = (req.ledger_id or f"ledger_{uuid4().hex[:12]}").strip()
    if not external_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ledger id is required")
    # Scope uniqueness to current user — different users can use the same
    # external_id (enforced by the (user_id, external_id) unique constraint).
    exists = db.scalar(
        select(Ledger).where(
            Ledger.external_id == external_id,
            Ledger.user_id == current_user.id,
        )
    )
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ledger already exists")

    name = _normalize_ledger_name(req.ledger_name)
    currency = _normalize_currency(req.currency)
    now = _utcnow()

    ledger = Ledger(
        user_id=current_user.id,
        external_id=external_id,
        name=name,
        currency=currency,
    )
    db.add(ledger)
    db.flush()

    # 共享账本 Phase 1:创建者自动 owner — 否则 ledger_access 找不到 member,后续
    # 所有 read/write/sync 路径都 404。
    db.add(LedgerMember(
        ledger_id=ledger.id,
        user_id=current_user.id,
        role="owner",
        joined_at=now,
    ))
    db.flush()

    # 方案 B:不写 ledger_snapshot 行。emit 一个 ledger entity SyncChange 个体事件,
    # mobile /sync/pull 能收到这个 ledger 被创建的事件。
    row_change = SyncChange(
        user_id=current_user.id,
        ledger_id=ledger.id,
        entity_type="ledger",
        entity_sync_id=external_id,
        action="upsert",
        payload_json={"ledgerName": name, "currency": currency},
        updated_at=now,
        updated_by_device_id="web-console",
        updated_by_user_id=current_user.id,
    )
    db.add(row_change)
    db.flush()
    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_ledger_create",
            metadata_json={
                "ledgerId": external_id,
                "newChangeId": row_change.change_id,
            },
        )
    )
    db.commit()

    await request.app.state.ws_manager.broadcast_to_user(
        current_user.id,
        {
            "type": "sync_change",
            "ledgerId": external_id,
            "serverCursor": row_change.change_id,
            "serverTimestamp": row_change.updated_at.isoformat(),
        },
    )

    logger.info(
        "write.ledger.create ledger=%s name=%s currency=%s user=%s",
        external_id,
        name,
        currency,
        current_user.id,
    )
    return WriteCommitMeta(
        ledger_id=external_id,
        base_change_id=0,
        new_change_id=row_change.change_id,
        server_timestamp=row_change.updated_at,
        idempotency_replayed=False,
        entity_id=external_id,
    )


@router.patch(
    "/ledgers/{ledger_id}/meta",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_ledger_meta(
    ledger_id: str,
    req: WriteLedgerMetaUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    if "ledger_name" in payload:
        payload["ledger_name"] = _normalize_ledger_name(payload.get("ledger_name"))
    if "currency" in payload:
        payload["currency"] = _normalize_currency(payload.get("currency"))
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay

    # mutate 在 _commit_write 内部跑,在 snapshot_builder 之后。
    # ledger.name / ledger.currency 必须延迟到 mutate 里改,否则 snapshot
    # _builder 读已经新值 → prev/next 一样 → diff 检测不到任何变更。
    # 同时显式 emit 一条 'ledger' SyncChange,因为 _emit_entity_diffs 只覆盖
    # items/accounts/categories/tags/budgets,不 diff 顶层 ledgerName/currency
    # —— 不显式 emit 的话 mobile _applyLedgerChange 永远收不到变更。
    def mutate(snapshot: dict) -> tuple[dict, str]:
        next_snapshot = ensure_snapshot_v2(snapshot)
        new_name: str | None = None
        new_currency: str | None = None
        if "ledger_name" in payload:
            new_name = payload["ledger_name"]
            next_snapshot["ledgerName"] = new_name
            ledger.name = new_name
        if "currency" in payload:
            new_currency = payload["currency"]
            next_snapshot["currency"] = new_currency
            ledger.currency = new_currency
        # 显式 emit ledger meta change(action=upsert,跟 create_ledger 同款
        # payload 字段)。mobile _applyLedgerChange 用 ledgerName/currency 写
        # 本地 ledgers 表。
        if new_name is not None or new_currency is not None:
            change_payload: dict = {}
            change_payload["ledgerName"] = ledger.name
            change_payload["currency"] = ledger.currency
            row_change = SyncChange(
                user_id=current_user.id,
                ledger_id=ledger.id,
                entity_type="ledger",
                entity_sync_id=ledger.external_id,
                action="upsert",
                payload_json=change_payload,
                updated_at=_utcnow(),
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
            db.add(row_change)
            db.flush()
        return next_snapshot, ledger.external_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_ledger_meta_update",
        mutate=mutate,
    )


@router.delete(
    "/ledgers/{ledger_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_ledger(
    ledger_id: str,
    request: Request,
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    """Delete a ledger thoroughly. 写一个 ``ledger_snapshot action=delete`` tombstone
    SyncChange,然后**真清干净**:
      1. 清 read_*_projection(让 /read/* 立刻看不到)
      2. 清 LedgerMember(账本没了 membership 无意义)
      3. **删 sync_changes 历史**(只留 tombstone,clients 拿到 tombstone 后
         就知道账本已删,中间过程的 events 不再有意义 — 跟"删除账本但保留
         交易历史"是矛盾语义)
      4. **删 attachment_files 行 + unlink 物理文件**(原本只 truncate projection
         的话物理文件永远孤儿 — storage / 隐私两头都不好)
      5. Ledger 行**保留**(soft-delete) — sync_changes 的 ledger_id FK 还指着它,
         tombstone 也需要它在;留个壳 + content=NULL 即可

    共享账本 Phase 1:owner only。Editor 想离开走 DELETE /members/{user_id}(MVP)
    或 transfer + leave(Phase 2)路径。
    """
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_id,
        roles=_OWNER_ONLY_ROLES,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")
    ledger, _ = row

    lock_ledger_for_materialize(db, ledger.id)
    now = _utcnow()
    # 共享账本 Phase 1:删账本前,先把所有非 owner member 的 user_id 记下来,
    # commit 后给他们发 member_change.removed,client 端走 _purgeLocalLedger
    # 自动清本地数据(复用被踢路径),避免"Owner 删了 Editor 那边还在"。
    from ...ledger_access import list_ledger_members
    member_ids_to_notify = [
        uid
        for uid, role in list_ledger_members(db, ledger_id=ledger.id)
        if uid != current_user.id
    ]
    tombstone = SyncChange(
        user_id=ledger.user_id,
        ledger_id=ledger.id,
        entity_type="ledger_snapshot",
        entity_sync_id=ledger.external_id,
        action="delete",
        payload_json={},
        updated_at=now,
        updated_by_device_id=device_id,
        updated_by_user_id=current_user.id,
    )
    db.add(tombstone)
    db.flush()
    snapshot_cache.invalidate(ledger.id)
    # 软删除:Ledger 行不动(留着外键历史),但 projection 清零,让 /read/* 立刻看不到
    projection._truncate_ledger(db, ledger.id)
    # 删非 owner LedgerMember(owner 自己保留)。
    #
    # **不能删 owner 自己**:pull endpoint 按 `list_accessible_ledgers`(走
    # LedgerMember)过滤 `scope=ledger` change。owner 删完后如果连自己也踢出
    # member,后续自己 pull 时 ledger_id NOT IN accessible → 拉不到刚写的
    # tombstone,client 永远收不到删除信号本地 ledger 一直留着。
    # 老版本删 owner 也是个 bug,首次发现于 mobile 收 WS 后 pull 返回 0 changes。
    db.execute(
        delete(LedgerMember).where(
            LedgerMember.ledger_id == ledger.id,
            LedgerMember.user_id != current_user.id,
        )
    )

    # ────────────── 真清干净:附件 + 历史 sync_changes ──────────────
    # 1) 收集本账本的 transaction 附件(category_icon 是 user-global,ledger_id
    #    为 NULL,不归本账本,跳过)。先收集 storage_path,DB 删完再 unlink。
    attachment_rows = list(
        db.scalars(
            select(AttachmentFile).where(
                AttachmentFile.ledger_id == ledger.id,
                AttachmentFile.attachment_kind == "transaction",
            )
        ).all()
    )
    attachment_paths_to_unlink: list[str] = [
        row.storage_path for row in attachment_rows if row.storage_path
    ]
    attachment_count = len(attachment_rows)
    for row in attachment_rows:
        db.delete(row)

    # 2) 删 sync_changes 历史 — 只保留刚写的 tombstone。tombstone 是单一权威
    #    "ledger 已删除"事件,clients pull 到它就走 _purgeLocalLedger;
    #    保留之前的 entity upsert / delete events 没有实际用途(读不到 +
    #    apply 后又被 tombstone 覆盖),纯占空间。
    sync_changes_pruned_result = db.execute(
        delete(SyncChange)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.change_id != tombstone.change_id,
        )
    )
    sync_changes_pruned = int(sync_changes_pruned_result.rowcount or 0)

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action="web_ledger_delete",
            metadata_json={
                "ledgerId": ledger.external_id,
                "newChangeId": tombstone.change_id,
                "attachmentsDeleted": attachment_count,
                "syncChangesPruned": sync_changes_pruned,
            },
        )
    )
    db.commit()

    # 3) DB commit + 锁释放后再做文件 IO。失败只 warn,不回滚 DB(行已删是
    #    事实,残留物会被 data_cleanup B3 类扫到下次 GC)。
    for path in attachment_paths_to_unlink:
        try:
            if os.path.exists(path):
                os.remove(path)
            _remove_empty_parents(path)
        except OSError as exc:
            logger.warning(
                "delete_ledger unlink failed ledger=%s path=%s err=%s",
                ledger.external_id, path, exc,
            )

    logger.info(
        "write.ledger.delete ledger=%s user=%s attachments=%d sync_changes_pruned=%d",
        ledger.external_id,
        current_user.id,
        attachment_count,
        sync_changes_pruned,
    )

    # Fan-out:
    # - 给 owner 自己发 sync_change → owner 其它 web tab 拉 tombstone 清 projection 缓存
    # - 给 owner 自己**也**发 member_change.removed → owner 的 mobile 设备走
    #   `_handleMemberChange` 的 `_purgeLocalLedgerByExternalId` 清本地 ledger 数据。
    #   这条额外通知是绕开 mobile 端契约漏洞:`_applyRemoteChange` 把所有
    #   `ledger_snapshot` change 无条件 skip(老注释:"全量快照在 fullPull 中处理"),
    #   delete tombstone 也被一并跳过 → owner mobile 自己 pull 到 tombstone 不处理 →
    #   本地账本一直留着。member_change.removed 是 mobile 已经实现的 purge 通道,
    #   语义上"被自己 web tab 踢出"虽然有点怪但行为一致。
    # - 非 owner member 跟原来一样,走 member_change.removed → client _purgeLocalLedger。
    await request.app.state.ws_manager.broadcast_to_user(
        ledger.user_id,
        {
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": tombstone.change_id,
            "serverTimestamp": tombstone.updated_at.isoformat(),
        },
    )
    # owner 自己 + 其它 member 都发 member_change.removed
    purge_targets = [ledger.user_id, *member_ids_to_notify]
    for member_id in purge_targets:
        await request.app.state.ws_manager.broadcast_to_user(
            member_id,
            {
                "type": "member_change",
                "ledgerId": ledger.external_id,
                "changeType": "removed",
                "userId": member_id,
                "reason": "ledger_deleted",
            },
        )
    return WriteCommitMeta(
        ledger_id=ledger.external_id,
        base_change_id=0,
        new_change_id=tombstone.change_id,
        server_timestamp=tombstone.updated_at,
        idempotency_replayed=False,
        entity_id=ledger.external_id,
    )


