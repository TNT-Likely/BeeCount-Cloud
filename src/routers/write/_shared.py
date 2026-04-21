"""write.py 的共享 helper 层。

原 src/routers/write.py(1658 行)按路由拆成 6 个子模块后,所有 endpoint
都依赖的辅助函数 / 常量 / 响应表 / 写入引擎(_commit_write /
_commit_write_fast_tx / _diff_entity_list / _emit_entity_diffs /
idempotency key 机制 / normalize helper / projection upsert-deleter 派发表)
集中在这里。

子模块只管:
  - endpoint 路由定义
  - 参数 / 权限校验
  - 调 _commit_write / _prepare_write / 其它 _shared 里的 helper
  - 返回 WriteCommitMeta

修改 snapshot 写入 / idempotency / cascade 规则应当来这里改一处即可,
修改单个 entity 的 endpoint 行为在对应子模块改。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...concurrency import lock_ledger_for_materialize
from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user, require_any_scopes, require_scopes
from ...ledger_access import (
    ROLE_EDITOR,
    ROLE_OWNER,
    get_accessible_ledger_by_external_id,
)
from ...models import (
    AuditLog,
    Ledger,
    ReadTxProjection,
    SyncChange,
    SyncPushIdempotency,
    User,
)
from ...schemas import (
    WriteAccountCreateRequest,
    WriteAccountUpdateRequest,
    WriteCategoryCreateRequest,
    WriteCategoryUpdateRequest,
    WriteCommitMeta,
    WriteEntityDeleteRequest,
    WriteLedgerCreateRequest,
    WriteLedgerMetaUpdateRequest,
    WriteTagCreateRequest,
    WriteTagUpdateRequest,
    WriteTransactionCreateRequest,
    WriteTransactionUpdateRequest,
)
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE
from ... import projection, snapshot_builder, snapshot_cache
from ...snapshot_mutator import (
    create_account,
    create_category,
    create_tag,
    create_transaction,
    delete_account,
    delete_category,
    delete_tag,
    delete_transaction,
    ensure_snapshot_v2,
    update_account,
    update_category,
    update_tag,
    update_transaction,
)

logger = logging.getLogger(__name__)

# router instance lives in each sub-module (ledgers.py / transactions.py / ...).
# _shared.py is helper-only, no routes.
settings = get_settings()
_WRITE_SCOPE_DEP = (
    require_any_scopes(SCOPE_WEB_WRITE, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_WEB_WRITE)
)

_TRANSACTION_WRITE_ROLES = {ROLE_OWNER, ROLE_EDITOR}
_OWNER_ONLY_ROLES = {ROLE_OWNER}
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "description": "Write role forbidden",
    },
    status.HTTP_404_NOT_FOUND: {
        "description": "Ledger or entity not found",
    },
    status.HTTP_409_CONFLICT: {
        "description": "Write conflict",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "WRITE_CONFLICT",
                        "message": "Write conflict",
                        "request_id": "req_xxx",
                    },
                    "detail": "Write conflict",
                    "latest_change_id": 12,
                    "latest_server_timestamp": "2026-02-24T12:00:00+00:00",
                }
            }
        },
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_PROJECTION_UPSERTERS: dict[str, Any] = {
    "account": projection.upsert_account,
    "category": projection.upsert_category,
    "tag": projection.upsert_tag,
    "transaction": projection.upsert_tx,
    "budget": projection.upsert_budget,
}
_PROJECTION_DELETERS: dict[str, Any] = {
    "account": projection.delete_account,
    "category": projection.delete_category,
    "tag": projection.delete_tag,
    "transaction": projection.delete_tx,
    "budget": projection.delete_budget,
}

# tx 里的 denormalized 字段 —— 当 account/category/tag rename 时,
# snapshot_mutator 会 cascade 到 items[] 里的这些字段。projection 那头
# 我们用 rename_cascade_* SQL UPDATE 处理,**不走** per-row upsert,
# 避免 10k tx 的 rename 场景跑 10k 次 ON CONFLICT DO UPDATE。
_TX_CASCADE_FIELDS = frozenset({
    "accountName", "fromAccountName", "toAccountName",
    "categoryName",
    "tags",
})


def _tx_diff_only_cascade(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    """prev/next 两个 tx dict 是否**只**在 cascade 字段上有差异。
    是 = 整行 projection upsert 可跳过,rename_cascade_* SQL 批量搞定。"""
    keys = set(prev.keys()) | set(nxt.keys())
    changed_any_cascade = False
    for k in keys:
        pv = prev.get(k)
        nv = nxt.get(k)
        if pv == nv:
            continue
        if k in _TX_CASCADE_FIELDS:
            changed_any_cascade = True
            continue
        return False
    return changed_any_cascade


def _collect_renames(
    prev_list: list[dict[str, Any]],
    next_list: list[dict[str, Any]],
) -> list[tuple[str, str, str, str | None]]:
    """返回 (sync_id, old_name, new_name, new_kind_optional) 列表。
    只取 name 真正改了的(既非新增也非删除),用来批量走 rename_cascade_*。"""
    prev_map = {e["syncId"]: e for e in prev_list if e.get("syncId")}
    out: list[tuple[str, str, str, str | None]] = []
    for e in next_list:
        sync_id = e.get("syncId")
        if not sync_id or sync_id not in prev_map:
            continue
        prev = prev_map[sync_id]
        old = (prev.get("name") or "").strip()
        new = (e.get("name") or "").strip()
        if old and new and old != new:
            out.append((sync_id, old, new, (e.get("kind") or "").strip() or None))
    return out


def _diff_entity_list(
    db: Session,
    ledger: Ledger,
    current_user: User,
    device_id: str,
    now: datetime,
    prev_list: list[dict[str, Any]],
    next_list: list[dict[str, Any]],
    entity_type: str,
    emitted_ids: list[int],
    cascade_covered: bool = False,
) -> None:
    """Emit per-entity SyncChange rows + 同事务 projection 写入。

    性能关键点:**cascade-only tx 改动走 bulk insert**(一次 executemany 把 N 条
    SyncChange 塞进去),不 per-row flush。rename_cascade_* SQL 已经刷了 projection,
    所以这些 tx 只需要一条 SyncChange 行给 mobile pull 用,不需要 change_id 回读。

    10k tx 的标签改名:从 10k 次 INSERT + flush (~800ms) 降到 1 条 executemany (~50ms)。
    """
    prev_map = {e["syncId"]: e for e in prev_list if "syncId" in e}
    next_map = {e["syncId"]: e for e in next_list if "syncId" in e}
    upsert_fn = _PROJECTION_UPSERTERS.get(entity_type)
    delete_fn = _PROJECTION_DELETERS.get(entity_type)

    # bulk 队列:(entity_type, "upsert"/"delete", sync_id, payload_json)
    # 只收 cascade-only tx —— 它们不需要 source_change_id 回读
    bulk_upsert_rows: list[dict[str, Any]] = []

    for sync_id, entity in next_map.items():
        prev_entity = prev_map.get(sync_id)
        if prev_entity is None or entity != prev_entity:
            is_cascade_only = (
                entity_type == "transaction"
                and cascade_covered
                and prev_entity is not None
                and _tx_diff_only_cascade(prev_entity, entity)
            )
            if is_cascade_only:
                bulk_upsert_rows.append({
                    "user_id": ledger.user_id,
                    "ledger_id": ledger.id,
                    "entity_type": entity_type,
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "payload_json": entity,
                    "updated_at": now,
                    "updated_by_device_id": device_id,
                    "updated_by_user_id": current_user.id,
                })
                continue
            # 普通路径:insert + flush 取 change_id,再走 projection upsert
            change_row = SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                entity_type=entity_type,
                entity_sync_id=sync_id,
                action="upsert",
                payload_json=entity,
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
            db.add(change_row)
            db.flush()
            emitted_ids.append(change_row.change_id)
            if upsert_fn is not None:
                upsert_fn(
                    db,
                    ledger_id=ledger.id,
                    user_id=ledger.user_id,
                    source_change_id=change_row.change_id,
                    payload=entity,
                )

    for sync_id in prev_map:
        if sync_id not in next_map:
            # 删 tx / category 前先收集附件 fileId(tx 从 attachments_json,
            # category 从 icon_cloud_file_id + 子分类图标)。删完 projection
            # 行后调 gc_orphan_attachments:被共享引用的 blob 保留,完全孤立
            # 的 DELETE attachment_files + unlink 物理文件。
            gc_file_ids: set[str] = set()
            if entity_type == "transaction":
                gc_file_ids = projection.collect_tx_attachment_fileids(
                    db, ledger_id=ledger.id, sync_id=sync_id,
                )
            elif entity_type == "category":
                gc_file_ids = projection.collect_category_icon_fileids(
                    db, ledger_id=ledger.id, sync_id=sync_id,
                )

            change_row = SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                entity_type=entity_type,
                entity_sync_id=sync_id,
                action="delete",
                payload_json={},
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
            db.add(change_row)
            db.flush()
            emitted_ids.append(change_row.change_id)
            if delete_fn is not None:
                delete_fn(db, ledger_id=ledger.id, sync_id=sync_id)
            if gc_file_ids:
                projection.gc_orphan_attachments(
                    db, ledger_id=ledger.id, file_ids=gc_file_ids,
                )

    # Bulk flush cascade-only rows
    if bulk_upsert_rows:
        from sqlalchemy import insert as sa_insert
        db.execute(sa_insert(SyncChange), bulk_upsert_rows)
        # 取新插入的最大 change_id 作 emitted_ids(给 response.new_change_id)
        new_max = db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger.id)
        )
        if new_max:
            emitted_ids.append(int(new_max))


def _emit_entity_diffs(
    db: Session,
    *,
    ledger: Ledger,
    current_user: User,
    device_id: str,
    prev: dict[str, Any] | None,
    next_snapshot: dict[str, Any],
    now: datetime,
) -> list[int]:
    """Diff prev/next snapshots and emit individual SyncChange rows for each changed entity.

    顺序很重要：先 account / category / tag（被引用方），最后 transaction（引用
    方）。mobile 在 _pull 里按 change_id ASC 逐条 apply，若 tx change 的 change_id
    比它引用的 category change 还小，`_resolveCategoryId` 在 category 更新前就
    查老名字 → 查不到 → tx.categoryId = null。典型表现：web 改分类名后 mobile
    上的相关交易分类变空。

    Projection 优化:若本次修改里 account/category/tag 有 rename,先发一次
    rename_cascade_* (SQL UPDATE,O(1) 条语句,不受 tx 数影响),然后 tx diff 时
    对"仅 cascade 字段改变"的 tx 行跳过 per-row projection upsert。
    10k tx 的 category rename 从 10k 次 ON CONFLICT 降到 1 条 UPDATE + N 个
    SyncChange 插入。
    """
    prev = prev or {}

    account_renames = _collect_renames(prev.get("accounts") or [], next_snapshot.get("accounts") or [])
    category_renames = _collect_renames(prev.get("categories") or [], next_snapshot.get("categories") or [])
    tag_renames = _collect_renames(prev.get("tags") or [], next_snapshot.get("tags") or [])
    any_rename = bool(account_renames or category_renames or tag_renames)

    emitted_ids: list[int] = []
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("accounts") or [], next_snapshot.get("accounts") or [],
                      "account", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("categories") or [], next_snapshot.get("categories") or [],
                      "category", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("tags") or [], next_snapshot.get("tags") or [],
                      "tag", emitted_ids)

    # Rename cascade via SQL batch,放在 tx diff 之前 —— 保证 tx diff 跳过的
    # cascade 行已经被刷新过。
    for sync_id, _old, new_name, _kind in account_renames:
        projection.rename_cascade_account(
            db, ledger_id=ledger.id, account_sync_id=sync_id, new_name=new_name,
        )
    for sync_id, _old, new_name, new_kind in category_renames:
        projection.rename_cascade_category(
            db, ledger_id=ledger.id, category_sync_id=sync_id,
            new_name=new_name, new_kind=new_kind,
        )
    for sync_id, old, new, _ in tag_renames:
        projection.rename_cascade_tag(
            db, ledger_id=ledger.id, tag_sync_id=sync_id,
            old_name=old, new_name=new,
        )

    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("items") or [], next_snapshot.get("items") or [],
                      "transaction", emitted_ids, cascade_covered=any_rename)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("budgets") or [], next_snapshot.get("budgets") or [],
                      "budget", emitted_ids)
    logger.info("_emit_entity_diffs: emitted %d entity changes for ledger %s", len(emitted_ids), ledger.external_id)
    return emitted_ids


def _load_ledger_for_write(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str],  # noqa: ARG001 — back-compat, ignored under single-user-per-ledger
) -> tuple[Ledger, None]:
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
    )
    if row is not None:
        return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")


def _latest_snapshot_change(db: Session, ledger_id: str) -> SyncChange | None:
    return db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.action == "upsert",
        )
        .order_by(SyncChange.change_id.desc())
    )


def _parse_snapshot(change: SyncChange | None) -> dict:
    if change is None:
        return ensure_snapshot_v2({})
    payload = change.payload_json
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str) or not content.strip():
        return ensure_snapshot_v2({})
    try:
        snapshot = json.loads(content)
    except json.JSONDecodeError:
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return ensure_snapshot_v2(snapshot)


def _hash_request(method: str, path: str, payload: dict) -> str:
    raw = json.dumps(
        {"method": method, "path": path, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _purge_expired_idempotency(db: Session) -> None:
    now = _utcnow()
    expired = db.scalars(
        select(SyncPushIdempotency).where(SyncPushIdempotency.expires_at < now).limit(200)
    ).all()
    for row in expired:
        db.delete(row)
    if expired:
        db.flush()


def _load_idempotent_response(
    db: Session,
    *,
    user_id: str,
    device_id: str,
    idempotency_key: str,
    request_hash: str,
) -> WriteCommitMeta | None:
    row = db.scalar(
        select(SyncPushIdempotency).where(
            SyncPushIdempotency.user_id == user_id,
            SyncPushIdempotency.device_id == device_id,
            SyncPushIdempotency.idempotency_key == idempotency_key,
        )
    )
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key reused with different payload",
        )
    payload = dict(row.response_json)
    payload["idempotency_replayed"] = True
    return WriteCommitMeta.model_validate(payload)


async def _commit_write_fast_tx(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    request_payload: dict,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    tx_id: str,
    mutate_payload: dict,
    action: str,  # "upsert" | "delete"
) -> WriteCommitMeta:
    """Fast path:单 tx update/delete,跳过全 snapshot build。只 SELECT 目标 tx
    (1 条 query by PK)→ 合并 payload → 写 SyncChange + projection。~10-15ms。
    """
    lock_ledger_for_materialize(db, ledger.id)
    now = _utcnow()

    # 1. 读目标 tx from projection
    tx_row = db.scalar(
        select(ReadTxProjection).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.sync_id == tx_id,
        )
    )
    if tx_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    # 2. 把 projection row → dict(mutator 认的 snapshot item 格式)
    prev_item = _projection_row_to_tx_dict(tx_row)

    # 3. actor 权限检查(复用现有逻辑)
    from ...snapshot_mutator import _assert_actor_can_modify  # 延迟 import 避免循环
    try:
        _assert_actor_can_modify(prev_item, mutate_payload)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if action == "delete":
        # 删 tx 前收集引用的 cloudFileId,删后 GC 孤立附件(物理 blob + 行)
        tx_file_ids = projection.collect_tx_attachment_fileids(
            db, ledger_id=ledger.id, sync_id=tx_id,
        )
        change_row = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="transaction",
            entity_sync_id=tx_id,
            action="delete",
            payload_json={},
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )
        db.add(change_row)
        db.flush()
        projection.delete_tx(db, ledger_id=ledger.id, sync_id=tx_id)
        projection.gc_orphan_attachments(
            db, ledger_id=ledger.id, file_ids=tx_file_ids,
        )
    else:
        # Upsert:merge payload 到 prev_item
        from ...snapshot_mutator import update_transaction
        # 构造最小 snapshot 让 mutator 跑逻辑(只有 1 个 item)
        minimal_snap = {"items": [prev_item], "count": 1}
        minimal_snap = update_transaction(minimal_snap, tx_id, mutate_payload)
        new_item = minimal_snap["items"][0]

        change_row = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="transaction",
            entity_sync_id=tx_id,
            action="upsert",
            payload_json=new_item,
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )
        db.add(change_row)
        db.flush()
        projection.upsert_tx(
            db,
            ledger_id=ledger.id,
            user_id=ledger.user_id,
            source_change_id=change_row.change_id,
            payload=new_item,
        )

    new_change_id = change_row.change_id

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action=audit_action,
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": base_change_id,
                "newChangeId": new_change_id,
                "entityId": tx_id,
            },
        )
    )

    response = WriteCommitMeta(
        ledger_id=ledger.external_id,
        base_change_id=base_change_id,
        new_change_id=new_change_id,
        server_timestamp=now,
        idempotency_replayed=False,
        entity_id=tx_id,
    )

    request_hash = _hash_request(request.method, request.url.path, request_payload)
    if idempotency_key:
        db.add(
            SyncPushIdempotency(
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_json=response.model_dump(mode="json"),
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key:
            replay = _load_idempotent_response(
                db,
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
        raise exc

    await request.app.state.ws_manager.broadcast_to_user(
        ledger.user_id,
        {
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": response.new_change_id,
            "serverTimestamp": response.server_timestamp.isoformat(),
        },
    )
    logger.info(
        "write.commit.fast action=%s ledger=%s entity=%s change_id=%d device=%s user=%s",
        audit_action, ledger.external_id, tx_id, response.new_change_id, device_id, current_user.id,
    )
    return response


def _projection_row_to_tx_dict(row: ReadTxProjection) -> dict[str, Any]:
    """projection row → snapshot item dict,跟 snapshot_builder.build 的格式一致。"""
    from ...snapshot_builder import _to_iso_utc
    item: dict[str, Any] = {
        "syncId": row.sync_id,
        "type": row.tx_type,
        "amount": row.amount,
        "happenedAt": _to_iso_utc(row.happened_at),
    }
    if row.note is not None:
        item["note"] = row.note
    if row.category_sync_id:
        item["categoryId"] = row.category_sync_id
    if row.category_name:
        item["categoryName"] = row.category_name
    if row.category_kind:
        item["categoryKind"] = row.category_kind
    if row.account_sync_id:
        item["accountId"] = row.account_sync_id
    if row.account_name:
        item["accountName"] = row.account_name
    if row.from_account_sync_id:
        item["fromAccountId"] = row.from_account_sync_id
    if row.from_account_name:
        item["fromAccountName"] = row.from_account_name
    if row.to_account_sync_id:
        item["toAccountId"] = row.to_account_sync_id
    if row.to_account_name:
        item["toAccountName"] = row.to_account_name
    if row.tags_csv:
        item["tags"] = row.tags_csv
    if row.tag_sync_ids_json:
        try:
            tag_ids = json.loads(row.tag_sync_ids_json)
            if isinstance(tag_ids, list) and tag_ids:
                item["tagIds"] = tag_ids
        except json.JSONDecodeError:
            pass
    if row.attachments_json:
        try:
            atts = json.loads(row.attachments_json)
            if isinstance(atts, list) and atts:
                item["attachments"] = atts
        except json.JSONDecodeError:
            pass
    if row.tx_index:
        item["txIndex"] = row.tx_index
    if row.created_by_user_id:
        item["createdByUserId"] = row.created_by_user_id
    return item


async def _commit_write(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    request_payload: dict,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    mutate: Callable[[dict], tuple[dict, str | None]],
) -> WriteCommitMeta:
    # Serialize concurrent writers on the same ledger。方案 B 后不再写 snapshot,
    # 但依然锁 —— 防止 rename cascade 的 SQL UPDATE 和 tx upsert 交叉跑。
    lock_ledger_for_materialize(db, ledger.id)

    # strict_base_change_id 语义转换:原先比 latest ledger_snapshot.change_id,
    # 方案 B 后 snapshot 不再写,改比 ledger 上任意 entity 的最新 change_id
    # (更严格 —— 连 tx 级修改都会触发 409)。默认关闭的 feature flag,生产用不到。
    #
    # 动态读 get_settings() 而不是缓存模块级 `settings`,是为了让测试 flip
    # STRICT_BASE_CHANGE_ID + get_settings.cache_clear() 的方式能立刻生效
    # (否则写入包分多个文件后,importlib.reload 不会重新执行 _shared.py
    # 的 module-level `settings = get_settings()`,flag 读的永远是旧值)。
    if get_settings().strict_base_change_id:
        latest_any_change_id = snapshot_builder.latest_change_id(db, ledger.id)
        if base_change_id != latest_any_change_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Write conflict",
                    "latest_change_id": latest_any_change_id,
                },
            )

    # 从 projection 按需构建当前状态给 mutator 吃。这个 snapshot dict 不写回 DB ——
    # 只是 mutator 内部用来查当前实体、做 duplicate/actor 校验。
    snapshot = snapshot_builder.build(db, ledger)
    # Shallow-per-entity copy for diffing(mutator 会原地改 items[i] 等)
    prev_snapshot = {**snapshot}
    for _k in ("items", "accounts", "categories", "tags", "budgets"):
        arr = snapshot.get(_k)
        if isinstance(arr, list):
            prev_snapshot[_k] = [dict(e) if isinstance(e, dict) else e for e in arr]
    try:
        next_snapshot, entity_id = mutate(snapshot)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    now = _utcnow()
    # 不再写 ledger_snapshot 行。emit 个体 SyncChange + 同事务 projection 写入,
    # new_change_id 用 emit 出来的最后一条 SyncChange 的 change_id。
    emitted_change_ids = _emit_entity_diffs(
        db,
        ledger=ledger,
        current_user=current_user,
        device_id=device_id,
        prev=prev_snapshot,
        next_snapshot=next_snapshot,
        now=now,
    )
    # 无变化 → 用当前 max change_id(幂等/只是触发写但没真修改的场景)
    new_change_id = max(emitted_change_ids) if emitted_change_ids else (
        snapshot_builder.latest_change_id(db, ledger.id)
    )

    db.add(
        AuditLog(
            user_id=current_user.id,
            ledger_id=ledger.id,
            action=audit_action,
            metadata_json={
                "ledgerId": ledger.external_id,
                "baseChangeId": base_change_id,
                "newChangeId": new_change_id,
                "entityId": entity_id,
            },
        )
    )

    response = WriteCommitMeta(
        ledger_id=ledger.external_id,
        base_change_id=base_change_id,
        new_change_id=new_change_id,
        server_timestamp=now,
        idempotency_replayed=False,
        entity_id=entity_id,
    )

    request_hash = _hash_request(request.method, request.url.path, request_payload)
    if idempotency_key:
        db.add(
            SyncPushIdempotency(
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response_json=response.model_dump(mode="json"),
                created_at=now,
                expires_at=now + timedelta(hours=24),
            )
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if idempotency_key:
            replay = _load_idempotent_response(
                db,
                user_id=current_user.id,
                device_id=device_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
        raise exc

    logger.info(
        "write.commit action=%s ledger=%s entity=%s change_id=%d device=%s user=%s",
        audit_action,
        ledger.external_id,
        entity_id,
        response.new_change_id,
        device_id,
        current_user.id,
    )
    # Single-user-per-ledger: notify only the owner.
    await request.app.state.ws_manager.broadcast_to_user(
        ledger.user_id,
        {
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": response.new_change_id,
            "serverTimestamp": response.server_timestamp.isoformat(),
        },
    )
    return response


def _prepare_write(
    *,
    db: Session,
    current_user: User,
    ledger_external_id: str,
    required_roles: set[str],
    idempotency_key: str | None,
    device_id: str,
    method: str,
    path: str,
    payload: dict,
) -> tuple[Ledger, WriteCommitMeta | None]:
    ledger, _ = _load_ledger_for_write(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles=required_roles,
    )
    if not idempotency_key:
        return ledger, None
    _purge_expired_idempotency(db)
    replay = _load_idempotent_response(
        db,
        user_id=current_user.id,
        device_id=device_id,
        idempotency_key=idempotency_key,
        request_hash=_hash_request(method, path, payload),
    )
    return ledger, replay


def _normalize_currency(raw: str | None) -> str:
    value = (raw or "CNY").strip().upper()
    if not value:
        return "CNY"
    return value[:16]


def _normalize_ledger_name(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ledger name is required")
    return value[:255]


def _payload_with_actor(payload: dict, current_user: User) -> dict:
    merged = dict(payload)
    merged["__actor_user_id"] = current_user.id
    merged["__actor_is_admin"] = bool(current_user.is_admin)
    return merged


def _assert_can_modify_entity(
    *,
    db: Session,  # noqa: ARG001 — retained for signature compat
    ledger: Ledger,
    current_user: User,
    entity_sync_id: str,  # noqa: ARG001 — retained for signature compat
) -> None:
    """Ownership check: ledger.user_id must match current user."""
    if ledger.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")



__all__ = [
    'hashlib',
    'json',
    'logging',
    'Callable',
    'datetime',
    'timedelta',
    'timezone',
    'Any',
    'uuid4',
    'APIRouter',
    'Depends',
    'Header',
    'HTTPException',
    'Request',
    'status',
    'func',
    'select',
    'IntegrityError',
    'Session',
    'lock_ledger_for_materialize',
    'get_settings',
    'get_db',
    'get_current_user',
    'require_any_scopes',
    'require_scopes',
    'ROLE_EDITOR',
    'ROLE_OWNER',
    'get_accessible_ledger_by_external_id',
    'AuditLog',
    'Ledger',
    'ReadTxProjection',
    'SyncChange',
    'SyncPushIdempotency',
    'User',
    'WriteAccountCreateRequest',
    'WriteAccountUpdateRequest',
    'WriteCategoryCreateRequest',
    'WriteCategoryUpdateRequest',
    'WriteCommitMeta',
    'WriteEntityDeleteRequest',
    'WriteLedgerCreateRequest',
    'WriteLedgerMetaUpdateRequest',
    'WriteTagCreateRequest',
    'WriteTagUpdateRequest',
    'WriteTransactionCreateRequest',
    'WriteTransactionUpdateRequest',
    'SCOPE_APP_WRITE',
    'SCOPE_WEB_WRITE',
    'projection',
    'snapshot_builder',
    'snapshot_cache',
    'create_account',
    'create_category',
    'create_tag',
    'create_transaction',
    'delete_account',
    'delete_category',
    'delete_tag',
    'delete_transaction',
    'ensure_snapshot_v2',
    'update_account',
    'update_category',
    'update_tag',
    'update_transaction',
    'logger',
    'settings',
    '_WRITE_SCOPE_DEP',
    '_TRANSACTION_WRITE_ROLES',
    '_OWNER_ONLY_ROLES',
    '_WRITE_RESPONSES',
    '_utcnow',
    '_PROJECTION_UPSERTERS',
    '_PROJECTION_DELETERS',
    '_TX_CASCADE_FIELDS',
    '_tx_diff_only_cascade',
    '_collect_renames',
    '_diff_entity_list',
    '_emit_entity_diffs',
    '_load_ledger_for_write',
    '_latest_snapshot_change',
    '_parse_snapshot',
    '_hash_request',
    '_purge_expired_idempotency',
    '_load_idempotent_response',
    '_commit_write_fast_tx',
    '_projection_row_to_tx_dict',
    '_commit_write',
    '_prepare_write',
    '_normalize_currency',
    '_normalize_ledger_name',
    '_payload_with_actor',
    '_assert_can_modify_entity',
]
