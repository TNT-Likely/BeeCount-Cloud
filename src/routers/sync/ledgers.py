"""GET /sync/ledgers —— 列出 caller 可访问的账本元信息。

每条返回 ledger_id / path / updated_at / size(粗略估算,按 tx 数量乘
固定系数)。mobile 启动时用来做账本差异比对。
"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403 — 拉取所有 imports / helpers / router / constants

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
