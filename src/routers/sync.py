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
from ..models import (
    AuditLog,
    Device,
    Ledger,
    ReadAccountProjection,
    ReadCategoryProjection,
    ReadTagProjection,
    ReadTxProjection,
    SyncChange,
    SyncCursor,
    User,
)
from ..schemas import (
    SyncChangeOut,
    SyncFullResponse,
    SyncLedgerOut,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from .. import projection, snapshot_builder, snapshot_cache

logger = logging.getLogger(__name__)

router = APIRouter()

_INDIVIDUAL_ENTITY_TYPES = {"transaction", "account", "category", "tag", "budget", "ledger"}
_ENTITY_TYPE_TO_SNAPSHOT_KEY = {
    "transaction": "items",
    "account": "accounts",
    "category": "categories",
    "tag": "tags",
    "budget": "budgets",
}
# 'ledger' 特殊:不是 snapshot 里的 array,而是 snapshot 的 top-level
# ledgerName / currency 字段。同时也要同步写回 Ledger 表自身,web read 直接拿。


def _apply_change_to_projection(
    db: Session,
    *,
    ledger_id: str,
    ledger_owner_id: str,
    change: SyncChange,
) -> None:
    """把一条 SyncChange 投到 projection 上(单事务内)。方案 B 后这是 push 路径
    保持 projection 和 sync_changes 一致的唯一挂点 —— 不再写 snapshot 行。

    关键点:
    - upsert:先 SELECT 旧 projection 行对比 name,变了就先走 rename_cascade_*
      (单条 SQL UPDATE 替换 N 次 per-row upsert),再 upsert 当前实体。
    - ledger entity:更新 Ledger.name / currency(read 路径直接查这两列)。
    """
    if change.entity_type == "ledger":
        if change.action == "delete":
            return
        payload_raw = change.payload_json
        if isinstance(payload_raw, str):
            try:
                payload_raw = json.loads(payload_raw)
            except json.JSONDecodeError:
                return
        if not isinstance(payload_raw, dict):
            return
        new_name = payload_raw.get("ledgerName")
        new_currency = payload_raw.get("currency")
        ledger_row = db.scalar(select(Ledger).where(Ledger.id == ledger_id))
        if ledger_row is not None:
            if isinstance(new_name, str) and new_name.strip():
                ledger_row.name = new_name.strip()
            if isinstance(new_currency, str) and new_currency.strip():
                ledger_row.currency = new_currency.strip()[:16]
        return

    sync_id = change.entity_sync_id
    if change.action == "delete":
        if change.entity_type == "transaction":
            projection.delete_tx(db, ledger_id=ledger_id, sync_id=sync_id)
        elif change.entity_type == "account":
            projection.delete_account(db, ledger_id=ledger_id, sync_id=sync_id)
        elif change.entity_type == "category":
            projection.delete_category(db, ledger_id=ledger_id, sync_id=sync_id)
        elif change.entity_type == "tag":
            projection.delete_tag(db, ledger_id=ledger_id, sync_id=sync_id)
        elif change.entity_type == "budget":
            projection.delete_budget(db, ledger_id=ledger_id, sync_id=sync_id)
        return

    # upsert:先 parse payload
    payload = change.payload_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return
    if not isinstance(payload, dict):
        return
    payload.setdefault("syncId", sync_id)

    # Rename cascade 探测:account/category/tag 的 name 变了,先一条 SQL UPDATE 刷 tx projection
    if change.entity_type in {"account", "category", "tag"}:
        new_name = str(payload.get("name") or "").strip()
        if new_name:
            if change.entity_type == "account":
                prev_row = db.scalar(
                    select(ReadAccountProjection).where(
                        ReadAccountProjection.ledger_id == ledger_id,
                        ReadAccountProjection.sync_id == sync_id,
                    )
                )
                old_name = (prev_row.name or "").strip() if prev_row is not None else ""
                if old_name and old_name != new_name:
                    projection.rename_cascade_account(
                        db, ledger_id=ledger_id, account_sync_id=sync_id, new_name=new_name,
                    )
            elif change.entity_type == "category":
                prev_row = db.scalar(
                    select(ReadCategoryProjection).where(
                        ReadCategoryProjection.ledger_id == ledger_id,
                        ReadCategoryProjection.sync_id == sync_id,
                    )
                )
                old_name = (prev_row.name or "").strip() if prev_row is not None else ""
                if old_name and old_name != new_name:
                    projection.rename_cascade_category(
                        db, ledger_id=ledger_id, category_sync_id=sync_id,
                        new_name=new_name,
                        new_kind=str(payload.get("kind") or "").strip() or None,
                    )
            elif change.entity_type == "tag":
                prev_row = db.scalar(
                    select(ReadTagProjection).where(
                        ReadTagProjection.ledger_id == ledger_id,
                        ReadTagProjection.sync_id == sync_id,
                    )
                )
                old_name = (prev_row.name or "").strip() if prev_row is not None else ""
                if old_name and old_name != new_name:
                    projection.rename_cascade_tag(
                        db, ledger_id=ledger_id, tag_sync_id=sync_id,
                        old_name=old_name, new_name=new_name,
                    )

    # Entity 自身 upsert —— 关键:mobile 增量 push 只带部分字段(比如只改 name),
    # 不带的字段要保留现有值,不能被默认值(0 / None / 空字符串)覆盖。所以这里
    # 先拉已有 projection 行,payload 值为 None 的 key 用旧值补齐,再 upsert。
    if change.entity_type == "transaction":
        merged = _merge_with_existing_tx(db, ledger_id, sync_id, payload)
        projection.upsert_tx(db, ledger_id=ledger_id, user_id=ledger_owner_id,
                              source_change_id=change.change_id, payload=merged)
    elif change.entity_type == "account":
        merged = _merge_with_existing_account(db, ledger_id, sync_id, payload)
        projection.upsert_account(db, ledger_id=ledger_id, user_id=ledger_owner_id,
                                    source_change_id=change.change_id, payload=merged)
    elif change.entity_type == "category":
        merged = _merge_with_existing_category(db, ledger_id, sync_id, payload)
        projection.upsert_category(db, ledger_id=ledger_id, user_id=ledger_owner_id,
                                     source_change_id=change.change_id, payload=merged)
    elif change.entity_type == "tag":
        merged = _merge_with_existing_tag(db, ledger_id, sync_id, payload)
        projection.upsert_tag(db, ledger_id=ledger_id, user_id=ledger_owner_id,
                                source_change_id=change.change_id, payload=merged)
    elif change.entity_type == "budget":
        merged = _merge_with_existing_budget(db, ledger_id, sync_id, payload)
        projection.upsert_budget(db, ledger_id=ledger_id, user_id=ledger_owner_id,
                                   source_change_id=change.change_id, payload=merged)


def _merge_with_existing_account(db, ledger_id, sync_id, payload):
    existing = db.scalar(
        select(ReadAccountProjection).where(
            ReadAccountProjection.ledger_id == ledger_id,
            ReadAccountProjection.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    # 用 existing 的字段填补 payload 里缺的(key 不存在 或 值 is None)
    base = {
        "syncId": existing.sync_id,
        "name": existing.name,
        "type": existing.account_type,
        "currency": existing.currency,
        "initialBalance": existing.initial_balance,
    }
    return {**base, **{k: v for k, v in payload.items() if v is not None}}


def _merge_with_existing_category(db, ledger_id, sync_id, payload):
    existing = db.scalar(
        select(ReadCategoryProjection).where(
            ReadCategoryProjection.ledger_id == ledger_id,
            ReadCategoryProjection.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    base = {
        "syncId": existing.sync_id,
        "name": existing.name,
        "kind": existing.kind,
        "level": existing.level,
        "sortOrder": existing.sort_order,
        "icon": existing.icon,
        "iconType": existing.icon_type,
        "customIconPath": existing.custom_icon_path,
        "iconCloudFileId": existing.icon_cloud_file_id,
        "iconCloudSha256": existing.icon_cloud_sha256,
        "parentName": existing.parent_name,
    }
    return {**base, **{k: v for k, v in payload.items() if v is not None}}


def _merge_with_existing_tag(db, ledger_id, sync_id, payload):
    existing = db.scalar(
        select(ReadTagProjection).where(
            ReadTagProjection.ledger_id == ledger_id,
            ReadTagProjection.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    base = {
        "syncId": existing.sync_id,
        "name": existing.name,
        "color": existing.color,
    }
    return {**base, **{k: v for k, v in payload.items() if v is not None}}


def _merge_with_existing_budget(db, ledger_id, sync_id, payload):
    from .models import ReadBudgetProjection

    existing = db.scalar(
        select(ReadBudgetProjection).where(
            ReadBudgetProjection.ledger_id == ledger_id,
            ReadBudgetProjection.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    base = {
        "syncId": existing.sync_id,
        "type": existing.budget_type,
        "categoryId": existing.category_sync_id,
        "amount": existing.amount,
        "period": existing.period,
        "startDay": existing.start_day,
        "enabled": existing.enabled,
    }
    return {**base, **{k: v for k, v in payload.items() if v is not None}}


def _merge_with_existing_tx(db, ledger_id, sync_id, payload):
    existing = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    import json as _json
    tag_ids = None
    if existing.tag_sync_ids_json:
        try:
            tag_ids = _json.loads(existing.tag_sync_ids_json)
        except _json.JSONDecodeError:
            tag_ids = None
    attachments = None
    if existing.attachments_json:
        try:
            attachments = _json.loads(existing.attachments_json)
        except _json.JSONDecodeError:
            attachments = None
    base = {
        "syncId": existing.sync_id,
        "type": existing.tx_type,
        "amount": existing.amount,
        "happenedAt": existing.happened_at.isoformat() if existing.happened_at else None,
        "note": existing.note,
        "categoryId": existing.category_sync_id,
        "categoryName": existing.category_name,
        "categoryKind": existing.category_kind,
        "accountId": existing.account_sync_id,
        "accountName": existing.account_name,
        "fromAccountId": existing.from_account_sync_id,
        "fromAccountName": existing.from_account_name,
        "toAccountId": existing.to_account_sync_id,
        "toAccountName": existing.to_account_name,
        "tags": existing.tags_csv,
        "tagIds": tag_ids,
        "attachments": attachments,
        "txIndex": existing.tx_index,
        "createdByUserId": existing.created_by_user_id,
    }
    return {**base, **{k: v for k, v in payload.items() if v is not None}}


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
        if change.entity_type in _INDIVIDUAL_ENTITY_TYPES:
            # lock 一次/账本,避免两个 push 并发走同个 ledger 的 cascade
            lock_ledger_for_materialize(db, ledger.id)
            _apply_change_to_projection(
                db,
                ledger_id=ledger.id,
                ledger_owner_id=ledger.user_id,
                change=row_change,
            )

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


@router.get("/full", response_model=SyncFullResponse)
def full_snapshot(
    ledger_id: str,
    _scopes: set[str] = Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncFullResponse:
    """给 mobile 的首次/全量同步。方案 B 后从 projection 懒构建(按 change_id 缓存)。

    mobile 协议兼容:返回 payload_json 还是 `{content: json_str, metadata: {...}}`,
    content 是序列化 snapshot —— mobile 零改动。
    """
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

    # Tombstone 检查:若最后一次 ledger_snapshot 是 delete(account 被软删),返回 None
    last_tombstone = db.scalar(
        select(SyncChange.action)
        .where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.action == "delete",
        )
        .order_by(SyncChange.change_id.desc())
        .limit(1)
    )
    if last_tombstone == "delete":
        return SyncFullResponse(ledger_id=ledger_id, snapshot=None, latest_cursor=latest_cursor)

    # Ledger 没任何 change → 空账本,返回 None
    ledger_change_id = snapshot_builder.latest_change_id(db, ledger.id)
    if ledger_change_id == 0:
        return SyncFullResponse(ledger_id=ledger_id, snapshot=None, latest_cursor=latest_cursor)

    # 按 change_id 缓存 —— 同一版本下所有请求复用。build 一次 ~15ms,之后 miss→hit。
    cached = snapshot_cache.get(ledger.id, ledger_change_id)
    if cached is None:
        cached = snapshot_builder.build(db, ledger)
        snapshot_cache.put(ledger.id, ledger_change_id, cached)

    payload_json = {
        "content": json.dumps(cached, ensure_ascii=False),
        "metadata": {"source": "lazy_rebuild"},
    }
    return SyncFullResponse(
        ledger_id=ledger_id,
        latest_cursor=latest_cursor,
        snapshot=SyncChangeOut(
            change_id=ledger_change_id,
            ledger_id=ledger_id,
            entity_type="ledger_snapshot",
            entity_sync_id=ledger.external_id,
            action=cast("Any", "upsert"),
            payload=payload_json,
            updated_at=datetime.now(timezone.utc),
            updated_by_device_id=None,
        ),
    )


@router.get("/ledgers", response_model=list[SyncLedgerOut])
def list_ledgers(
    _scopes: set[str] = Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SyncLedgerOut]:
    """方案 B 后:用户可见账本元数据。size 估算从 tx 行数外推(不再 byte 精确)。"""
    accessible = list_accessible_ledgers(db, user_id=current_user.id)
    out: list[SyncLedgerOut] = []
    for ledger in accessible:
        # 软删除检测:最后一次 ledger_snapshot delete tombstone
        last_tombstone = db.scalar(
            select(SyncChange.action)
            .where(
                SyncChange.ledger_id == ledger.id,
                SyncChange.entity_type == "ledger_snapshot",
                SyncChange.action == "delete",
            )
            .order_by(SyncChange.change_id.desc())
            .limit(1)
        )
        if last_tombstone == "delete":
            continue

        latest_change_id = snapshot_builder.latest_change_id(db, ledger.id)
        if latest_change_id == 0:
            continue

        # latest_updated_at:最后一次任意 change 的时间
        latest_updated = db.scalar(
            select(SyncChange.updated_at)
            .where(SyncChange.ledger_id == ledger.id)
            .order_by(SyncChange.change_id.desc())
            .limit(1)
        )

        # size 估算:每条 tx 按 ~300 字节算,配合基础元数据
        tx_count = db.scalar(
            select(func.count())
            .select_from(ReadTxProjection)
            .where(ReadTxProjection.ledger_id == ledger.id)
        ) or 0
        size = 512 + tx_count * 300  # 足够粗略的估算

        out.append(
            SyncLedgerOut(
                ledger_id=ledger.external_id,
                path=ledger.external_id,
                updated_at=latest_updated or datetime.now(timezone.utc),
                size=size,
                metadata={"source": "lazy_rebuild"},
                role=cast("Any", "owner"),
            )
        )
    return out
