from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import UserAccount, UserCategory, UserTag


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm_text(value: str | None) -> str:
    return (value or "").strip().casefold()


def _is_blank(value: str | None) -> bool:
    return not (value or "").strip()


def _scoped_active_accounts(db: Session, *, user_id: str | None) -> list[UserAccount]:
    conditions: list[Any] = [UserAccount.deleted_at.is_(None)]
    if user_id:
        conditions.append(UserAccount.user_id == user_id)
    rows = db.scalars(
        select(UserAccount)
        .where(*conditions)
        .order_by(UserAccount.user_id.asc(), UserAccount.created_at.asc(), UserAccount.id.asc())
    ).all()
    return list(rows)


def _scoped_active_categories(db: Session, *, user_id: str | None) -> list[UserCategory]:
    conditions: list[Any] = [UserCategory.deleted_at.is_(None)]
    if user_id:
        conditions.append(UserCategory.user_id == user_id)
    rows = db.scalars(
        select(UserCategory)
        .where(*conditions)
        .order_by(
            UserCategory.user_id.asc(),
            UserCategory.created_at.asc(),
            UserCategory.id.asc(),
        )
    ).all()
    return list(rows)


def _scoped_active_tags(db: Session, *, user_id: str | None) -> list[UserTag]:
    conditions: list[Any] = [UserTag.deleted_at.is_(None)]
    if user_id:
        conditions.append(UserTag.user_id == user_id)
    rows = db.scalars(
        select(UserTag)
        .where(*conditions)
        .order_by(UserTag.user_id.asc(), UserTag.created_at.asc(), UserTag.id.asc())
    ).all()
    return list(rows)


def _dedupe_accounts(db: Session, *, user_id: str | None) -> bool:
    now = _utcnow()
    changed = False
    groups: dict[tuple[str, str], list[UserAccount]] = defaultdict(list)
    for row in _scoped_active_accounts(db, user_id=user_id):
        groups[(row.user_id, _norm_text(row.name))].append(row)

    for rows in groups.values():
        if len(rows) <= 1:
            continue
        group_changed = False
        canonical = min(rows, key=lambda row: (row.created_at, row.id))
        trimmed_name = canonical.name.strip()
        if canonical.name != trimmed_name:
            canonical.name = trimmed_name
            group_changed = True
            changed = True
        for duplicate in rows:
            if duplicate.id == canonical.id:
                continue
            if _is_blank(canonical.account_type) and not _is_blank(duplicate.account_type):
                canonical.account_type = duplicate.account_type
                group_changed = True
                changed = True
            if _is_blank(canonical.currency) and not _is_blank(duplicate.currency):
                canonical.currency = duplicate.currency
                group_changed = True
                changed = True
            if canonical.initial_balance is None and duplicate.initial_balance is not None:
                canonical.initial_balance = duplicate.initial_balance
                group_changed = True
                changed = True

            duplicate.deleted_at = now
            duplicate.updated_at = now
            db.add(duplicate)
            group_changed = True
            changed = True
        if group_changed:
            canonical.updated_at = now
            db.add(canonical)
    return changed


def _dedupe_categories(db: Session, *, user_id: str | None) -> bool:
    now = _utcnow()
    changed = False
    groups: dict[tuple[str, str, str], list[UserCategory]] = defaultdict(list)
    for row in _scoped_active_categories(db, user_id=user_id):
        groups[(row.user_id, _norm_text(row.kind), _norm_text(row.name))].append(row)

    for rows in groups.values():
        if len(rows) <= 1:
            continue
        group_changed = False
        canonical = min(rows, key=lambda row: (row.created_at, row.id))
        trimmed_name = canonical.name.strip()
        if canonical.name != trimmed_name:
            canonical.name = trimmed_name
            group_changed = True
            changed = True
        for duplicate in rows:
            if duplicate.id == canonical.id:
                continue
            if canonical.level is None and duplicate.level is not None:
                canonical.level = duplicate.level
                group_changed = True
                changed = True
            if canonical.sort_order is None and duplicate.sort_order is not None:
                canonical.sort_order = duplicate.sort_order
                group_changed = True
                changed = True
            if _is_blank(canonical.icon) and not _is_blank(duplicate.icon):
                canonical.icon = duplicate.icon
                group_changed = True
                changed = True
            if _is_blank(canonical.icon_type) and not _is_blank(duplicate.icon_type):
                canonical.icon_type = duplicate.icon_type
                group_changed = True
                changed = True
            if _is_blank(canonical.custom_icon_path) and not _is_blank(duplicate.custom_icon_path):
                canonical.custom_icon_path = duplicate.custom_icon_path
                group_changed = True
                changed = True
            if _is_blank(canonical.icon_cloud_file_id) and not _is_blank(duplicate.icon_cloud_file_id):
                canonical.icon_cloud_file_id = duplicate.icon_cloud_file_id
                group_changed = True
                changed = True
            if _is_blank(canonical.icon_cloud_sha256) and not _is_blank(duplicate.icon_cloud_sha256):
                canonical.icon_cloud_sha256 = duplicate.icon_cloud_sha256
                group_changed = True
                changed = True
            if canonical.parent_id is None and duplicate.parent_id is not None:
                canonical.parent_id = duplicate.parent_id
                group_changed = True
                changed = True

            duplicate.deleted_at = now
            duplicate.updated_at = now
            db.add(duplicate)
            group_changed = True
            changed = True

        if group_changed:
            canonical.updated_at = now
            db.add(canonical)
    return changed


def _dedupe_tags(db: Session, *, user_id: str | None) -> bool:
    now = _utcnow()
    changed = False
    groups: dict[tuple[str, str], list[UserTag]] = defaultdict(list)

    for row in _scoped_active_tags(db, user_id=user_id):
        groups[(row.user_id, _norm_text(row.name))].append(row)

    for rows in groups.values():
        if len(rows) <= 1:
            continue
        group_changed = False
        canonical = min(rows, key=lambda row: (row.created_at, row.id))
        trimmed_name = canonical.name.strip()
        if canonical.name != trimmed_name:
            canonical.name = trimmed_name
            group_changed = True
            changed = True
        for duplicate in rows:
            if duplicate.id == canonical.id:
                continue
            if _is_blank(canonical.color) and not _is_blank(duplicate.color):
                canonical.color = duplicate.color
                group_changed = True
                changed = True
            duplicate.deleted_at = now
            duplicate.updated_at = now
            db.add(duplicate)
            group_changed = True
            changed = True
        if group_changed:
            canonical.updated_at = now
            db.add(canonical)

    return changed


def deduplicate_user_dictionaries(db: Session, *, user_id: str | None) -> bool:
    """Merge duplicate user dictionaries (soft-delete duplicates, keep canonical).

    Canonical row selection is by earliest created_at/id ordering.
    """
    changed = False
    if _dedupe_accounts(db, user_id=user_id):
        changed = True
    if _dedupe_categories(db, user_id=user_id):
        changed = True
    if _dedupe_tags(db, user_id=user_id):
        changed = True
    return changed
