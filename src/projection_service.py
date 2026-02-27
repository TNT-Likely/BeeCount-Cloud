from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import (
    Ledger,
    SyncChange,
    UserAccount,
    UserCategory,
    UserTag,
    WebAccountProjection,
    WebCategoryProjection,
    WebLedgerProjection,
    WebTagProjection,
    WebTransactionProjection,
)


def _to_datetime(raw: Any) -> datetime:
    if isinstance(raw, str) and raw:
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _to_float(raw: Any) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _sync_id(raw_item: dict[str, Any], prefix: str, idx: int) -> str:
    existing = raw_item.get("syncId")
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    return f"{prefix}_{idx}_{uuid4().hex[:8]}"


def _normalize_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _split_tags(raw: Any) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _clear_projection(db: Session, ledger_id: str) -> None:
    db.execute(delete(WebTransactionProjection).where(WebTransactionProjection.ledger_id == ledger_id))
    db.execute(delete(WebAccountProjection).where(WebAccountProjection.ledger_id == ledger_id))
    db.execute(delete(WebCategoryProjection).where(WebCategoryProjection.ledger_id == ledger_id))
    db.execute(delete(WebTagProjection).where(WebTagProjection.ledger_id == ledger_id))
    db.execute(delete(WebLedgerProjection).where(WebLedgerProjection.ledger_id == ledger_id))


def _upsert_web_account_projection(db: Session, row: WebAccountProjection) -> None:
    existing = db.scalar(
        select(WebAccountProjection).where(
            WebAccountProjection.ledger_id == row.ledger_id,
            WebAccountProjection.sync_id == row.sync_id,
        )
    )
    if existing is None:
        existing = db.scalar(
            select(WebAccountProjection).where(
                WebAccountProjection.ledger_id == row.ledger_id,
                WebAccountProjection.name == row.name,
            )
        )
    if existing is None:
        db.add(row)
        return
    existing.created_by_user_id = row.created_by_user_id
    existing.sync_id = row.sync_id
    existing.name = row.name
    existing.account_type = row.account_type
    existing.currency = row.currency
    existing.initial_balance = row.initial_balance
    db.add(existing)


def _upsert_web_category_projection(db: Session, row: WebCategoryProjection) -> None:
    existing = db.scalar(
        select(WebCategoryProjection).where(
            WebCategoryProjection.ledger_id == row.ledger_id,
            WebCategoryProjection.sync_id == row.sync_id,
        )
    )
    if existing is None:
        existing = db.scalar(
            select(WebCategoryProjection).where(
                WebCategoryProjection.ledger_id == row.ledger_id,
                WebCategoryProjection.kind == row.kind,
                WebCategoryProjection.name == row.name,
            )
        )
    if existing is None:
        db.add(row)
        return
    existing.created_by_user_id = row.created_by_user_id
    existing.sync_id = row.sync_id
    existing.name = row.name
    existing.kind = row.kind
    existing.level = row.level
    existing.sort_order = row.sort_order
    existing.icon = row.icon
    existing.icon_type = row.icon_type
    existing.custom_icon_path = row.custom_icon_path
    existing.icon_cloud_file_id = row.icon_cloud_file_id
    existing.icon_cloud_sha256 = row.icon_cloud_sha256
    existing.parent_name = row.parent_name
    db.add(existing)


def _upsert_web_tag_projection(db: Session, row: WebTagProjection) -> None:
    existing = db.scalar(
        select(WebTagProjection).where(
            WebTagProjection.ledger_id == row.ledger_id,
            WebTagProjection.sync_id == row.sync_id,
        )
    )
    if existing is None:
        existing = db.scalar(
            select(WebTagProjection).where(
                WebTagProjection.ledger_id == row.ledger_id,
                WebTagProjection.name == row.name,
            )
        )
    if existing is None:
        db.add(row)
        return
    existing.created_by_user_id = row.created_by_user_id
    existing.sync_id = row.sync_id
    existing.name = row.name
    existing.color = row.color
    db.add(existing)


def _upsert_web_ledger_projection(db: Session, row: WebLedgerProjection) -> None:
    existing = db.scalar(
        select(WebLedgerProjection).where(
            WebLedgerProjection.ledger_id == row.ledger_id,
        )
    )
    if existing is None:
        db.add(row)
        return
    existing.ledger_name = row.ledger_name
    existing.currency = row.currency
    existing.transaction_count = row.transaction_count
    existing.income_total = row.income_total
    existing.expense_total = row.expense_total
    existing.balance = row.balance
    existing.exported_at = row.exported_at
    existing.source_change_id = row.source_change_id
    existing.updated_at = row.updated_at
    db.add(existing)


def _load_existing_creator_maps(db: Session, ledger_id: str) -> dict[str, dict[str, str | None]]:
    tx_map = {
        row.sync_id: row.created_by_user_id
        for row in db.scalars(
            select(WebTransactionProjection).where(WebTransactionProjection.ledger_id == ledger_id)
        ).all()
    }
    acc_map = {
        row.sync_id: row.created_by_user_id
        for row in db.scalars(
            select(WebAccountProjection).where(WebAccountProjection.ledger_id == ledger_id)
        ).all()
    }
    cat_map = {
        row.sync_id: row.created_by_user_id
        for row in db.scalars(
            select(WebCategoryProjection).where(WebCategoryProjection.ledger_id == ledger_id)
        ).all()
    }
    tag_map = {
        row.sync_id: row.created_by_user_id
        for row in db.scalars(
            select(WebTagProjection).where(WebTagProjection.ledger_id == ledger_id)
        ).all()
    }
    return {"tx": tx_map, "acc": acc_map, "cat": cat_map, "tag": tag_map}


def _creator_user_id(
    raw_item: dict[str, Any],
    *,
    existing_user_id: str | None,
    fallback_user_id: str | None,
) -> str | None:
    created_by = raw_item.get("createdByUserId")
    if isinstance(created_by, str) and created_by.strip():
        return created_by.strip()
    if existing_user_id:
        return existing_user_id
    return fallback_user_id


def _dictionary_users(
    *,
    items: list[Any],
    accounts: list[Any],
    categories: list[Any],
    tags: list[Any],
    fallback_user_id: str | None,
) -> set[str]:
    out: set[str] = set()
    if fallback_user_id:
        out.add(fallback_user_id)
    for collection in (items, accounts, categories, tags):
        for raw_item in collection:
            if not isinstance(raw_item, dict):
                continue
            created_by = raw_item.get("createdByUserId")
            if isinstance(created_by, str) and created_by.strip():
                out.add(created_by.strip())
    return out


def _load_user_dictionary_maps(
    db: Session, *, user_ids: set[str]
) -> tuple[
    dict[tuple[str, str], UserAccount],
    dict[tuple[str, str, str], UserCategory],
    dict[tuple[str, str], UserTag],
    dict[str, UserAccount],
    dict[str, UserCategory],
    dict[str, UserTag],
]:
    if not user_ids:
        return {}, {}, {}, {}, {}, {}
    accounts = db.scalars(
        select(UserAccount).where(
            UserAccount.user_id.in_(user_ids),
            UserAccount.deleted_at.is_(None),
        )
    ).all()
    categories = db.scalars(
        select(UserCategory).where(
            UserCategory.user_id.in_(user_ids),
            UserCategory.deleted_at.is_(None),
        )
    ).all()
    tags = db.scalars(
        select(UserTag).where(
            UserTag.user_id.in_(user_ids),
            UserTag.deleted_at.is_(None),
        )
    ).all()
    account_by_key = {(row.user_id, row.name.lower()): row for row in accounts}
    category_by_key = {(row.user_id, row.kind, row.name.lower()): row for row in categories}
    tag_by_key = {(row.user_id, row.name.lower()): row for row in tags}
    account_by_id = {row.id: row for row in accounts}
    category_by_id = {row.id: row for row in categories}
    tag_by_id = {row.id: row for row in tags}
    return account_by_key, category_by_key, tag_by_key, account_by_id, category_by_id, tag_by_id


def _ensure_user_account(
    db: Session,
    *,
    account_by_key: dict[tuple[str, str], UserAccount],
    account_by_id: dict[str, UserAccount],
    user_id: str | None,
    name: str | None,
    account_type: str | None = None,
    currency: str | None = None,
    initial_balance: float | None = None,
) -> UserAccount | None:
    normalized = _normalize_name(name)
    if not user_id or not normalized:
        return None
    key = (user_id, normalized.lower())
    row = account_by_key.get(key)
    if row is not None:
        updated = False
        if (not row.account_type) and account_type:
            row.account_type = account_type
            updated = True
        if (not row.currency) and currency:
            row.currency = currency
            updated = True
        if row.initial_balance is None and initial_balance is not None:
            row.initial_balance = initial_balance
            updated = True
        if updated:
            row.updated_at = datetime.now(timezone.utc)
            db.add(row)
        return row
    row = UserAccount(
        user_id=user_id,
        name=normalized,
        account_type=account_type,
        currency=currency,
        initial_balance=initial_balance,
    )
    db.add(row)
    db.flush()
    account_by_key[key] = row
    account_by_id[row.id] = row
    return row


def _ensure_user_category(
    db: Session,
    *,
    category_by_key: dict[tuple[str, str, str], UserCategory],
    category_by_id: dict[str, UserCategory],
    user_id: str | None,
    kind: str | None,
    name: str | None,
    level: int | None = None,
    sort_order: int | None = None,
    icon: str | None = None,
    icon_type: str | None = None,
    custom_icon_path: str | None = None,
    icon_cloud_file_id: str | None = None,
    icon_cloud_sha256: str | None = None,
) -> UserCategory | None:
    normalized_name = _normalize_name(name)
    normalized_kind = _normalize_name(kind)
    if not user_id or not normalized_name or not normalized_kind:
        return None
    key = (user_id, normalized_kind, normalized_name.lower())
    row = category_by_key.get(key)
    if row is not None:
        updated = False
        if row.level is None and level is not None:
            row.level = level
            updated = True
        if row.sort_order is None and sort_order is not None:
            row.sort_order = sort_order
            updated = True
        if (not row.icon) and icon:
            row.icon = icon
            updated = True
        if (not row.icon_type) and icon_type:
            row.icon_type = icon_type
            updated = True
        if (not row.custom_icon_path) and custom_icon_path:
            row.custom_icon_path = custom_icon_path
            updated = True
        if (not row.icon_cloud_file_id) and icon_cloud_file_id:
            row.icon_cloud_file_id = icon_cloud_file_id
            updated = True
        if (not row.icon_cloud_sha256) and icon_cloud_sha256:
            row.icon_cloud_sha256 = icon_cloud_sha256
            updated = True
        if updated:
            row.updated_at = datetime.now(timezone.utc)
            db.add(row)
        return row
    row = UserCategory(
        user_id=user_id,
        name=normalized_name,
        kind=normalized_kind,
        level=level,
        sort_order=sort_order,
        icon=icon,
        icon_type=icon_type,
        custom_icon_path=custom_icon_path,
        icon_cloud_file_id=icon_cloud_file_id,
        icon_cloud_sha256=icon_cloud_sha256,
    )
    db.add(row)
    db.flush()
    category_by_key[key] = row
    category_by_id[row.id] = row
    return row


def _ensure_user_tag(
    db: Session,
    *,
    tag_by_key: dict[tuple[str, str], UserTag],
    tag_by_id: dict[str, UserTag],
    user_id: str | None,
    name: str | None,
    color: str | None = None,
) -> UserTag | None:
    normalized = _normalize_name(name)
    if not user_id or not normalized:
        return None
    key = (user_id, normalized.lower())
    row = tag_by_key.get(key)
    if row is not None:
        if (not row.color) and color:
            row.color = color
            row.updated_at = datetime.now(timezone.utc)
            db.add(row)
        return row
    row = UserTag(user_id=user_id, name=normalized, color=color)
    db.add(row)
    db.flush()
    tag_by_key[key] = row
    tag_by_id[row.id] = row
    return row


def rebuild_projection_from_snapshot_change(db: Session, *, ledger_id: str, change: SyncChange) -> None:
    existing_maps = _load_existing_creator_maps(db, ledger_id)
    owner_user_id = db.scalar(select(Ledger.user_id).where(Ledger.id == ledger_id))
    fallback_user_id = change.updated_by_user_id or owner_user_id
    _clear_projection(db, ledger_id)
    # Ensure previous projection rows are physically removed before inserting
    # replacement rows to avoid transient UNIQUE conflicts in sqlite.
    db.flush()
    if change.action == "delete":
        return

    payload = change.payload_json
    if not isinstance(payload, dict):
        return
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return

    try:
        snapshot = json.loads(content)
    except json.JSONDecodeError:
        return
    if not isinstance(snapshot, dict):
        return

    items = snapshot.get("items")
    if not isinstance(items, list):
        items = []
    accounts = snapshot.get("accounts")
    if not isinstance(accounts, list):
        accounts = []
    categories = snapshot.get("categories")
    if not isinstance(categories, list):
        categories = []
    tags = snapshot.get("tags")
    if not isinstance(tags, list):
        tags = []

    dictionary_users = _dictionary_users(
        items=items,
        accounts=accounts,
        categories=categories,
        tags=tags,
        fallback_user_id=fallback_user_id,
    )
    (
        account_by_key,
        category_by_key,
        tag_by_key,
        account_by_id,
        category_by_id,
        tag_by_id,
    ) = _load_user_dictionary_maps(db, user_ids=dictionary_users)

    income_total = 0.0
    expense_total = 0.0
    tx_rows: list[WebTransactionProjection] = []
    seen_tx_sync_ids: set[str] = set()

    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue
        sync_id = _sync_id(raw_item, "tx", idx)
        if sync_id in seen_tx_sync_ids:
            continue
        seen_tx_sync_ids.add(sync_id)
        tx_type = str(raw_item.get("type") or "expense")
        amount = _to_float(raw_item.get("amount"))
        created_by_user_id = _creator_user_id(
            raw_item,
            existing_user_id=existing_maps["tx"].get(sync_id),
            fallback_user_id=fallback_user_id,
        )
        if tx_type == "income":
            income_total += amount
        elif tx_type == "expense":
            expense_total += amount
        attachments = raw_item.get("attachments")

        account_name = raw_item.get("accountName") if isinstance(raw_item.get("accountName"), str) else None
        from_account_name = (
            raw_item.get("fromAccountName") if isinstance(raw_item.get("fromAccountName"), str) else None
        )
        to_account_name = (
            raw_item.get("toAccountName") if isinstance(raw_item.get("toAccountName"), str) else None
        )
        category_name = (
            raw_item.get("categoryName") if isinstance(raw_item.get("categoryName"), str) else None
        )
        category_kind = (
            raw_item.get("categoryKind") if isinstance(raw_item.get("categoryKind"), str) else None
        )

        account_id_raw = raw_item.get("accountId")
        from_account_id_raw = raw_item.get("fromAccountId")
        to_account_id_raw = raw_item.get("toAccountId")
        category_id_raw = raw_item.get("categoryId")
        tag_ids_raw = raw_item.get("tagIds")

        account_row = None
        if isinstance(account_id_raw, str) and account_id_raw.strip():
            account_row = account_by_id.get(account_id_raw.strip())
        if account_row is None:
            account_row = _ensure_user_account(
                db,
                account_by_key=account_by_key,
                account_by_id=account_by_id,
                user_id=created_by_user_id,
                name=account_name,
            )

        from_account_row = None
        if isinstance(from_account_id_raw, str) and from_account_id_raw.strip():
            from_account_row = account_by_id.get(from_account_id_raw.strip())
        if from_account_row is None:
            from_account_row = _ensure_user_account(
                db,
                account_by_key=account_by_key,
                account_by_id=account_by_id,
                user_id=created_by_user_id,
                name=from_account_name,
            )

        to_account_row = None
        if isinstance(to_account_id_raw, str) and to_account_id_raw.strip():
            to_account_row = account_by_id.get(to_account_id_raw.strip())
        if to_account_row is None:
            to_account_row = _ensure_user_account(
                db,
                account_by_key=account_by_key,
                account_by_id=account_by_id,
                user_id=created_by_user_id,
                name=to_account_name,
            )

        category_row = None
        if isinstance(category_id_raw, str) and category_id_raw.strip():
            category_row = category_by_id.get(category_id_raw.strip())
        if category_row is None:
            category_row = _ensure_user_category(
                db,
                category_by_key=category_by_key,
                category_by_id=category_by_id,
                user_id=created_by_user_id,
                kind=category_kind,
                name=category_name,
            )

        resolved_tag_rows: list[UserTag] = []
        if isinstance(tag_ids_raw, list):
            for raw_tag_id in tag_ids_raw:
                if isinstance(raw_tag_id, str):
                    row = tag_by_id.get(raw_tag_id.strip())
                    if row is not None:
                        resolved_tag_rows.append(row)
        if not resolved_tag_rows:
            for tag_name in _split_tags(raw_item.get("tags")):
                row = _ensure_user_tag(
                    db,
                    tag_by_key=tag_by_key,
                    tag_by_id=tag_by_id,
                    user_id=created_by_user_id,
                    name=tag_name,
                )
                if row is not None:
                    resolved_tag_rows.append(row)
        deduped_tag_ids: list[str] = []
        for row in resolved_tag_rows:
            if row.id not in deduped_tag_ids:
                deduped_tag_ids.append(row.id)

        tx_rows.append(
            WebTransactionProjection(
                ledger_id=ledger_id,
                created_by_user_id=created_by_user_id,
                sync_id=sync_id,
                tx_index=idx,
                tx_type=tx_type,
                amount=amount,
                happened_at=_to_datetime(raw_item.get("happenedAt")),
                note=raw_item.get("note") if isinstance(raw_item.get("note"), str) else None,
                category_name=category_name or (category_row.name if category_row is not None else None),
                category_kind=category_kind or (category_row.kind if category_row is not None else None),
                account_name=account_name or (account_row.name if account_row is not None else None),
                from_account_name=from_account_name or (from_account_row.name if from_account_row is not None else None),
                to_account_name=to_account_name or (to_account_row.name if to_account_row is not None else None),
                account_id=account_row.id if account_row is not None else None,
                from_account_id=from_account_row.id if from_account_row is not None else None,
                to_account_id=to_account_row.id if to_account_row is not None else None,
                category_id=category_row.id if category_row is not None else None,
                tag_ids_json=deduped_tag_ids or None,
                tags=raw_item.get("tags") if isinstance(raw_item.get("tags"), str) else None,
                attachments_json=attachments if isinstance(attachments, list) else None,
            )
        )
    if tx_rows:
        db.add_all(tx_rows)

    account_rows: list[WebAccountProjection] = []
    seen_account_sync_ids: set[str] = set()
    for idx, raw_account in enumerate(accounts):
        if not isinstance(raw_account, dict):
            continue
        sync_id = _sync_id(raw_account, "acc", idx)
        if sync_id in seen_account_sync_ids:
            continue
        name = raw_account.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        created_by_user_id = _creator_user_id(
            raw_account,
            existing_user_id=existing_maps["acc"].get(sync_id),
            fallback_user_id=fallback_user_id,
        )
        normalized_name = name.strip()
        account_type = raw_account.get("type") if isinstance(raw_account.get("type"), str) else None
        currency = raw_account.get("currency") if isinstance(raw_account.get("currency"), str) else None
        initial_balance = _to_float(raw_account.get("initialBalance"))
        dict_account = _ensure_user_account(
            db,
            account_by_key=account_by_key,
            account_by_id=account_by_id,
            user_id=created_by_user_id,
            name=normalized_name,
            account_type=account_type,
            currency=currency,
            initial_balance=initial_balance,
        )
        account_rows.append(
            WebAccountProjection(
                ledger_id=ledger_id,
                created_by_user_id=created_by_user_id,
                sync_id=sync_id,
                name=dict_account.name if dict_account is not None else normalized_name,
                account_type=account_type,
                currency=currency,
                initial_balance=initial_balance,
            )
        )
        seen_account_sync_ids.add(sync_id)
    if account_rows:
        # Guard against legacy snapshots containing duplicate account names/syncIds.
        deduped_account_rows: list[WebAccountProjection] = []
        emitted_names: set[str] = set()
        emitted_sync_ids: set[str] = set()
        for row in account_rows:
            normalized_name = row.name.strip().lower()
            if row.sync_id in emitted_sync_ids:
                continue
            if normalized_name in emitted_names:
                continue
            emitted_sync_ids.add(row.sync_id)
            emitted_names.add(normalized_name)
            deduped_account_rows.append(row)
        if deduped_account_rows:
            for row in deduped_account_rows:
                _upsert_web_account_projection(db, row)

    category_rows: list[WebCategoryProjection] = []
    seen_category_sync_ids: set[str] = set()
    for idx, raw_category in enumerate(categories):
        if not isinstance(raw_category, dict):
            continue
        sync_id = _sync_id(raw_category, "cat", idx)
        if sync_id in seen_category_sync_ids:
            continue
        name = raw_category.get("name")
        kind = raw_category.get("kind")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(kind, str) or not kind.strip():
            continue
        created_by_user_id = _creator_user_id(
            raw_category,
            existing_user_id=existing_maps["cat"].get(sync_id),
            fallback_user_id=fallback_user_id,
        )
        normalized_name = name.strip()
        normalized_kind = kind.strip()
        level = raw_category.get("level") if isinstance(raw_category.get("level"), int) else None
        sort_order = raw_category.get("sortOrder") if isinstance(raw_category.get("sortOrder"), int) else None
        icon = raw_category.get("icon") if isinstance(raw_category.get("icon"), str) else None
        icon_type = raw_category.get("iconType") if isinstance(raw_category.get("iconType"), str) else None
        custom_icon_path = (
            raw_category.get("customIconPath")
            if isinstance(raw_category.get("customIconPath"), str)
            else None
        )
        icon_cloud_file_id = (
            raw_category.get("iconCloudFileId")
            if isinstance(raw_category.get("iconCloudFileId"), str)
            else None
        )
        icon_cloud_sha256 = (
            raw_category.get("iconCloudSha256")
            if isinstance(raw_category.get("iconCloudSha256"), str)
            else None
        )
        dict_category = _ensure_user_category(
            db,
            category_by_key=category_by_key,
            category_by_id=category_by_id,
            user_id=created_by_user_id,
            kind=normalized_kind,
            name=normalized_name,
            level=level,
            sort_order=sort_order,
            icon=icon,
            icon_type=icon_type,
            custom_icon_path=custom_icon_path,
            icon_cloud_file_id=icon_cloud_file_id,
            icon_cloud_sha256=icon_cloud_sha256,
        )
        category_rows.append(
            WebCategoryProjection(
                ledger_id=ledger_id,
                created_by_user_id=created_by_user_id,
                sync_id=sync_id,
                name=dict_category.name if dict_category is not None else normalized_name,
                kind=dict_category.kind if dict_category is not None else normalized_kind,
                level=level,
                sort_order=sort_order,
                icon=icon,
                icon_type=icon_type,
                custom_icon_path=custom_icon_path,
                icon_cloud_file_id=icon_cloud_file_id,
                icon_cloud_sha256=icon_cloud_sha256,
                parent_name=(
                    raw_category.get("parentName")
                    if isinstance(raw_category.get("parentName"), str)
                    else None
                ),
            )
        )
        seen_category_sync_ids.add(sync_id)
    if category_rows:
        deduped_category_rows: list[WebCategoryProjection] = []
        emitted_sync_ids: set[str] = set()
        emitted_keys: set[tuple[str, str]] = set()
        for row in category_rows:
            key = (row.kind.strip().lower(), row.name.strip().lower())
            if row.sync_id in emitted_sync_ids:
                continue
            if key in emitted_keys:
                continue
            emitted_sync_ids.add(row.sync_id)
            emitted_keys.add(key)
            deduped_category_rows.append(row)
        if deduped_category_rows:
            for row in deduped_category_rows:
                _upsert_web_category_projection(db, row)

    tag_projection_rows: list[WebTagProjection] = []
    seen_tag_sync_ids: set[str] = set()
    for idx, raw_tag in enumerate(tags):
        if not isinstance(raw_tag, dict):
            continue
        sync_id = _sync_id(raw_tag, "tag", idx)
        if sync_id in seen_tag_sync_ids:
            continue
        name = raw_tag.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        created_by_user_id = _creator_user_id(
            raw_tag,
            existing_user_id=existing_maps["tag"].get(sync_id),
            fallback_user_id=fallback_user_id,
        )
        normalized_name = name.strip()
        color = raw_tag.get("color") if isinstance(raw_tag.get("color"), str) else None
        dict_tag = _ensure_user_tag(
            db,
            tag_by_key=tag_by_key,
            tag_by_id=tag_by_id,
            user_id=created_by_user_id,
            name=normalized_name,
            color=color,
        )
        tag_projection_rows.append(
            WebTagProjection(
                ledger_id=ledger_id,
                created_by_user_id=created_by_user_id,
                sync_id=sync_id,
                name=dict_tag.name if dict_tag is not None else normalized_name,
                color=color,
            )
        )
        seen_tag_sync_ids.add(sync_id)
    if tag_projection_rows:
        deduped_tag_rows: list[WebTagProjection] = []
        emitted_sync_ids: set[str] = set()
        emitted_names: set[str] = set()
        for row in tag_projection_rows:
            normalized_name = row.name.strip().lower()
            if row.sync_id in emitted_sync_ids:
                continue
            if normalized_name in emitted_names:
                continue
            emitted_sync_ids.add(row.sync_id)
            emitted_names.add(normalized_name)
            deduped_tag_rows.append(row)
        if deduped_tag_rows:
            for row in deduped_tag_rows:
                _upsert_web_tag_projection(db, row)

    transaction_count_raw = snapshot.get("count")
    transaction_count = transaction_count_raw if isinstance(transaction_count_raw, int) else len(tx_rows)
    balance = income_total - expense_total
    _upsert_web_ledger_projection(
        db,
        WebLedgerProjection(
            ledger_id=ledger_id,
            ledger_name=str(snapshot.get("ledgerName") or "Untitled"),
            currency=str(snapshot.get("currency") or "CNY"),
            transaction_count=transaction_count,
            income_total=income_total,
            expense_total=expense_total,
            balance=balance,
            exported_at=_to_datetime(snapshot.get("exportedAt")),
            source_change_id=change.change_id,
            updated_at=datetime.now(timezone.utc),
        ),
    )
