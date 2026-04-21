"""GET /sync/full —— 按需构建并返回账本的完整 snapshot。

方案 B 之后不再持续写 ledger_snapshot,只有这条路径按需从 projection 懒
构建(snapshot_builder)+ 短缓存。mobile 首次同步或重装时一次性吃下来。
"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — 拉取所有 imports / helpers / router / constants

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


