"""检测旧版 App 删除父分类后留下的分类及其它无有效父分类的子分类。"""

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.orm import Session, aliased

from ...models import ReadBudgetProjection, ReadTxProjection, SyncChange, UserCategoryProjection
from ...sync_applier import _compact_entity_upsert_events
from .models import OrphanRecord, OrphanType


def _orphan_categories(*, user_id: str | None = None, sync_id: str | None = None):
    child = aliased(UserCategoryProjection)
    parent = aliased(UserCategoryProjection)
    parent_id = func.nullif(func.trim(child.parent_sync_id), "")
    parent_name = func.nullif(func.trim(child.parent_name), "")
    valid_parent = (
        select(1)
        .where(
            parent.user_id == child.user_id,
            parent.sync_id != child.sync_id,
            parent.kind == child.kind,
            func.coalesce(parent.level, 1) == 1,
            func.nullif(func.trim(parent.parent_sync_id), "").is_(None),
            func.nullif(func.trim(parent.parent_name), "").is_(None),
            or_(
                parent.sync_id == parent_id,
                and_(parent_id.is_(None), func.trim(parent.name) == parent_name),
            ),
        )
        .exists()
    )

    # 一个缺失的父 ID 本身不能证明父分类已删除（例如父分类尚未上传）。
    latest_parent_event = (
        select(func.max(SyncChange.change_id))
        .where(
            SyncChange.user_id == child.user_id,
            SyncChange.scope == "user",
            SyncChange.entity_type == "category",
            SyncChange.entity_sync_id == parent_id,
        )
        .correlate(child)
        .scalar_subquery()
    )
    parent_deleted = and_(
        ~select(1).where(parent.user_id == child.user_id, parent.sync_id == parent_id).exists(),
        select(1)
        .where(
            SyncChange.change_id == latest_parent_event,
            SyncChange.action == "delete",
            SyncChange.change_id > child.source_change_id,
        )
        .exists(),
    )

    tx = ReadTxProjection
    budget = ReadBudgetProjection
    dependent = aliased(UserCategoryProjection)
    referenced = or_(
        select(1)
        .where(
            tx.user_id == child.user_id,
            or_(
                tx.category_sync_id == child.sync_id,
                and_(tx.category_sync_id.is_(None), tx.category_name == child.name),
            ),
        )
        .exists(),
        select(1)
        .where(budget.user_id == child.user_id, budget.category_sync_id == child.sync_id)
        .exists(),
        select(1)
        .where(
            dependent.user_id == child.user_id,
            dependent.sync_id != child.sync_id,
            or_(
                dependent.parent_sync_id == child.sync_id,
                and_(
                    dependent.parent_sync_id.is_(None),
                    dependent.parent_name == child.name,
                    dependent.kind == child.kind,
                ),
            ),
        )
        .exists(),
    )
    stmt = select(
        child.user_id,
        child.sync_id,
        child.name,
        child.source_change_id,
        parent_id.label("parent_id"),
        parent_name.label("parent_name"),
        latest_parent_event.label("parent_event_id"),
        parent_deleted.label("parent_deleted"),
        referenced.label("referenced"),
    ).where(or_(child.level == 2, parent_id.is_not(None), parent_name.is_not(None)), ~valid_parent)
    if user_id is not None:
        stmt = stmt.where(child.user_id == user_id)
    if sync_id is not None:
        stmt = stmt.where(child.sync_id == sync_id)
    return stmt


def list_category_parent_issues(db: Session, *, user_id: str) -> Sequence[Row]:
    categories = _orphan_categories(user_id=user_id).subquery()
    return db.execute(
        select(
            categories.c.sync_id,
            categories.c.name,
            categories.c.parent_id,
            categories.c.parent_name,
            categories.c.parent_event_id,
        ).order_by(categories.c.sync_id)
    ).all()


def scan_categories_missing_parent(db: Session) -> list[OrphanRecord]:
    records = []
    for row in db.execute(_orphan_categories()):
        parent_label = row.parent_name or row.parent_id
        subtitle = f"找不到父分类「{parent_label}」" if parent_label else "未关联父分类"
        if row.referenced:
            subtitle += " · 仍有数据引用，请先迁移"
        elif row.parent_deleted:
            subtitle += " · 已确认父分类删除"
        else:
            subtitle += " · 请核对父子关系"
        records.append(
            OrphanRecord(
                type=OrphanType.CATEGORY_MISSING_PARENT,
                user_id=row.user_id,
                row_id=f"{row.user_id}:{row.sync_id}",
                sync_id=row.sync_id,
                title=f"子分类「{row.name or row.sync_id}」",
                subtitle=subtitle,
                can_clean=row.parent_deleted and not row.referenced,
                extra={"source_change_id": row.source_change_id},
            )
        )
    return records


def delete_orphan_category(db: Session, record: OrphanRecord, *, actor_user_id: str | None) -> None:
    source_change_id = (record.extra or {}).get("source_change_id")
    if not record.user_id or not record.sync_id or not isinstance(source_change_id, int):
        raise ValueError("分类清理缺少扫描版本，请重新扫描")

    key = (record.user_id, record.sync_id)
    candidate = _orphan_categories(user_id=key[0], sync_id=key[1]).subquery()
    category = UserCategoryProjection
    # DELETE 自身复核依赖关系和扫描版本，避免用过期的扫描结果删掉已修复的分类。
    deleted = db.execute(
        delete(category).where(
            category.user_id == key[0],
            category.sync_id == key[1],
            category.source_change_id == source_change_id,
            select(1)
            .select_from(candidate)
            .where(
                candidate.c.parent_deleted,
                ~candidate.c.referenced,
            )
            .exists(),
        )
    ).rowcount
    if not deleted:
        if db.get(category, key) is not None:
            raise ValueError("分类已变化、父分类删除未确认或仍有引用，请重新扫描")
        return

    db.add(
        SyncChange(
            user_id=key[0],
            scope="user",
            entity_type="category",
            entity_sync_id=key[1],
            action="delete",
            payload_json={},
            updated_at=datetime.now(timezone.utc),
            updated_by_device_id="admin-data-cleanup",
            updated_by_user_id=actor_user_id,
        )
    )
    db.flush()
    _compact_entity_upsert_events(db, user_id=key[0], entity_type="category", entity_sync_id=key[1])
