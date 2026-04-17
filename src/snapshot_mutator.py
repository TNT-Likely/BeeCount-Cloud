from __future__ import annotations

import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


def _new_sync_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


_LEGACY_SYNC_ID_PATTERN = re.compile(r"^(tx|acc|cat|tag)_(\d+)_([A-Za-z0-9]+)$")


def _to_iso8601(raw: object) -> str:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc).isoformat()
    if isinstance(raw, str) and raw.strip():
        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _to_float(raw: object) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _ensure_list(snapshot: dict, key: str) -> list[dict]:
    raw = snapshot.get(key)
    if not isinstance(raw, list):
        raw = []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    snapshot[key] = out
    return out


def _ensure_sync_id(items: list[dict], prefix: str) -> None:
    for item in items:
        sync_id = item.get("syncId")
        if not isinstance(sync_id, str) or not sync_id.strip():
            item["syncId"] = _new_sync_id(prefix)


def ensure_snapshot_v2(snapshot: dict | None) -> dict:
    target = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    target["ledgerName"] = str(target.get("ledgerName") or "Untitled")
    target["currency"] = str(target.get("currency") or "CNY")

    items = _ensure_list(target, "items")
    accounts = _ensure_list(target, "accounts")
    categories = _ensure_list(target, "categories")
    tags = _ensure_list(target, "tags")

    _ensure_sync_id(items, "tx")
    _ensure_sync_id(accounts, "acc")
    _ensure_sync_id(categories, "cat")
    _ensure_sync_id(tags, "tag")

    for item in items:
        item["type"] = str(item.get("type") or "expense")
        item["amount"] = _to_float(item.get("amount"))
        item["happenedAt"] = _to_iso8601(item.get("happenedAt"))
    for account in accounts:
        account["name"] = str(account.get("name") or "").strip()
        account["type"] = str(account.get("type") or "") or None
        account["currency"] = str(account.get("currency") or "") or None
        if "initialBalance" in account:
            account["initialBalance"] = _to_float(account.get("initialBalance"))
    for category in categories:
        category["name"] = str(category.get("name") or "").strip()
        category["kind"] = str(category.get("kind") or "expense").strip()
    for tag in tags:
        tag["name"] = str(tag.get("name") or "").strip()

    target["count"] = len(items)
    return target


def _legacy_sync_id(sync_id: str) -> tuple[str, int] | None:
    match = _LEGACY_SYNC_ID_PATTERN.fullmatch(sync_id.strip())
    if match is None:
        return None
    prefix, index, _suffix = match.groups()
    return prefix, int(index)


def _find_by_sync_id(
    items: list[dict], sync_id: str, *, expected_prefix: str | None = None
) -> tuple[int, dict]:
    normalized_id = sync_id.strip()
    for idx, item in enumerate(items):
        if str(item.get("syncId")) == normalized_id:
            return idx, item

    legacy = _legacy_sync_id(normalized_id)
    if legacy is not None:
        prefix, legacy_index = legacy
        if (expected_prefix is None or prefix == expected_prefix) and 0 <= legacy_index < len(items):
            fallback_item = items[legacy_index]
            fallback_item["syncId"] = normalized_id
            return legacy_index, fallback_item
    raise KeyError("entity not found")


def _actor_user_id(payload: dict) -> str | None:
    raw = payload.get("__actor_user_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _actor_is_admin(payload: dict) -> bool:
    return bool(payload.get("__actor_is_admin"))


def _assert_actor_can_modify(item: dict, payload: dict) -> None:
    actor_user_id = _actor_user_id(payload)
    if actor_user_id is None:
        return
    if _actor_is_admin(payload):
        return
    created_by = item.get("createdByUserId")
    if isinstance(created_by, str) and created_by.strip() and created_by.strip() != actor_user_id:
        raise PermissionError("write role forbidden: entity owner mismatch")


def _mark_entity_actor(item: dict, payload: dict, *, create: bool) -> None:
    actor_user_id = _actor_user_id(payload)
    if actor_user_id is None:
        return
    if create:
        item["createdByUserId"] = actor_user_id
    elif not isinstance(item.get("createdByUserId"), str) or not str(item.get("createdByUserId")).strip():
        item["createdByUserId"] = actor_user_id
    item["updatedByUserId"] = actor_user_id


def _normalize_tx_tags(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        tags = [part.strip() for part in raw.split(",") if part.strip()]
        if not tags:
            return None
        return ",".join(dict.fromkeys(tags))
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            value = str(item).strip()
            if value:
                parts.append(value)
        if not parts:
            return None
        return ",".join(dict.fromkeys(parts))
    return None


def _sort_transactions(snapshot: dict) -> None:
    items = _ensure_list(snapshot, "items")
    items.sort(key=lambda item: _to_iso8601(item.get("happenedAt")), reverse=True)


def create_transaction(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    tx_type = str(payload.get("tx_type") or "expense")
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid transaction type")

    tx_id = _new_sync_id("tx")
    item: dict[str, object] = {
        "syncId": tx_id,
        "type": tx_type,
        "amount": _to_float(payload.get("amount")),
        "happenedAt": _to_iso8601(payload.get("happened_at")),
    }
    if payload.get("note") is not None:
        item["note"] = str(payload.get("note"))
    if payload.get("category_name") is not None:
        item["categoryName"] = str(payload.get("category_name"))
    if payload.get("category_kind") is not None:
        item["categoryKind"] = str(payload.get("category_kind"))
    if payload.get("category_id") is not None:
        item["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_name") is not None:
        item["accountName"] = str(payload.get("account_name"))
    if payload.get("account_id") is not None:
        item["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_name") is not None:
        item["fromAccountName"] = str(payload.get("from_account_name"))
    if payload.get("from_account_id") is not None:
        item["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_name") is not None:
        item["toAccountName"] = str(payload.get("to_account_name"))
    if payload.get("to_account_id") is not None:
        item["toAccountId"] = str(payload.get("to_account_id"))
    tags = _normalize_tx_tags(payload.get("tags"))
    if tags is not None:
        item["tags"] = tags
    tag_ids_raw = payload.get("tag_ids")
    if isinstance(tag_ids_raw, list):
        tag_ids: list[str] = []
        for raw in tag_ids_raw:
            value = str(raw).strip()
            if value and value not in tag_ids:
                tag_ids.append(value)
        if tag_ids:
            item["tagIds"] = tag_ids
    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        item["attachments"] = attachments
    _mark_entity_actor(item, payload, create=True)

    _ensure_list(target, "items").append(item)
    _sort_transactions(target)
    target["count"] = len(_ensure_list(target, "items"))
    return target, tx_id


def update_transaction(snapshot: dict, tx_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    items = _ensure_list(target, "items")
    _, item = _find_by_sync_id(items, tx_id, expected_prefix="tx")
    _assert_actor_can_modify(item, payload)

    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid transaction type")
        item["type"] = tx_type
    if "amount" in payload:
        item["amount"] = _to_float(payload.get("amount"))
    if "happened_at" in payload:
        item["happenedAt"] = _to_iso8601(payload.get("happened_at"))

    mapping = {
        "note": "note",
        "category_name": "categoryName",
        "category_kind": "categoryKind",
        "category_id": "categoryId",
        "account_name": "accountName",
        "account_id": "accountId",
        "from_account_name": "fromAccountName",
        "from_account_id": "fromAccountId",
        "to_account_name": "toAccountName",
        "to_account_id": "toAccountId",
    }
    for req_key, snapshot_key in mapping.items():
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                item.pop(snapshot_key, None)
            else:
                item[snapshot_key] = str(value)
    if "tags" in payload:
        raw_tags = payload.get("tags")
        normalized = _normalize_tx_tags(raw_tags)
        logger.info(
            "update_transaction.tags tx_id=%s raw=%r normalized=%r",
            tx_id, raw_tags, normalized,
        )
        if normalized is None:
            item.pop("tags", None)
        else:
            item["tags"] = normalized
    else:
        logger.info("update_transaction.tags tx_id=%s 'tags' key NOT in payload", tx_id)
    if "tag_ids" in payload:
        raw = payload.get("tag_ids")
        if isinstance(raw, list):
            tag_ids: list[str] = []
            for value in raw:
                text = str(value).strip()
                if text and text not in tag_ids:
                    tag_ids.append(text)
            if tag_ids:
                item["tagIds"] = tag_ids
            else:
                item.pop("tagIds", None)
        elif raw is None:
            item.pop("tagIds", None)
    if "attachments" in payload:
        attachments = payload.get("attachments")
        if isinstance(attachments, list):
            item["attachments"] = attachments
        elif attachments is None:
            item.pop("attachments", None)
    _mark_entity_actor(item, payload, create=False)

    _sort_transactions(target)
    target["count"] = len(items)
    return target


def delete_transaction(snapshot: dict, tx_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    items = _ensure_list(target, "items")
    idx, item = _find_by_sync_id(items, tx_id, expected_prefix="tx")
    _assert_actor_can_modify(item, payload or {})
    items.pop(idx)
    _sort_transactions(target)
    target["count"] = len(items)
    return target


def _normalize_name(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("write validation failed: name is required")
    return value


def create_account(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    name = _normalize_name(payload.get("name"))
    if any(str(row.get("name", "")).strip().lower() == name.lower() for row in accounts):
        raise ValueError("write validation failed: duplicated account name")
    sync_id = _new_sync_id("acc")
    account = {
        "syncId": sync_id,
        "name": name,
        "type": str(payload.get("account_type") or "") or None,
        "currency": str(payload.get("currency") or "") or None,
        "initialBalance": _to_float(payload.get("initial_balance")),
    }
    _mark_entity_actor(account, payload, create=True)
    accounts.append(account)
    return target, sync_id


def update_account(snapshot: dict, account_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    _, account = _find_by_sync_id(accounts, account_id, expected_prefix="acc")
    _assert_actor_can_modify(account, payload)
    old_name = str(account.get("name") or "").strip()

    if "name" in payload:
        new_name = _normalize_name(payload.get("name"))
        if any(
            str(row.get("syncId")) != account_id
            and str(row.get("name", "")).strip().lower() == new_name.lower()
            for row in accounts
        ):
            raise ValueError("write validation failed: duplicated account name")
        account["name"] = new_name
    if "account_type" in payload:
        value = payload.get("account_type")
        account["type"] = str(value) if value else None
    if "currency" in payload:
        value = payload.get("currency")
        account["currency"] = str(value) if value else None
    if "initial_balance" in payload:
        account["initialBalance"] = _to_float(payload.get("initial_balance"))

    new_name = str(account.get("name") or "").strip()
    if old_name and new_name and old_name != new_name:
        for tx in _ensure_list(target, "items"):
            if tx.get("accountName") == old_name:
                tx["accountName"] = new_name
            if tx.get("fromAccountName") == old_name:
                tx["fromAccountName"] = new_name
            if tx.get("toAccountName") == old_name:
                tx["toAccountName"] = new_name
    _mark_entity_actor(account, payload, create=False)
    return target


def delete_account(snapshot: dict, account_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    idx, account = _find_by_sync_id(accounts, account_id, expected_prefix="acc")
    _assert_actor_can_modify(account, payload or {})
    old_name = str(account.get("name") or "").strip()
    accounts.pop(idx)
    if old_name:
        for tx in _ensure_list(target, "items"):
            if tx.get("accountName") == old_name:
                tx.pop("accountName", None)
            if tx.get("fromAccountName") == old_name:
                tx.pop("fromAccountName", None)
            if tx.get("toAccountName") == old_name:
                tx.pop("toAccountName", None)
    return target


def create_category(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    name = _normalize_name(payload.get("name"))
    kind = str(payload.get("kind") or "expense").strip()
    if kind not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid category kind")
    if any(
        str(row.get("name", "")).strip().lower() == name.lower()
        and str(row.get("kind", "")).strip() == kind
        for row in categories
    ):
        raise ValueError("write validation failed: duplicated category")
    sync_id = _new_sync_id("cat")
    category = {
        "syncId": sync_id,
        "name": name,
        "kind": kind,
        "level": payload.get("level"),
        "sortOrder": payload.get("sort_order"),
        "icon": payload.get("icon"),
        "iconType": payload.get("icon_type"),
        "customIconPath": payload.get("custom_icon_path"),
        "iconCloudFileId": payload.get("icon_cloud_file_id"),
        "iconCloudSha256": payload.get("icon_cloud_sha256"),
        "parentName": payload.get("parent_name"),
    }
    _mark_entity_actor(category, payload, create=True)
    categories.append(category)
    return target, sync_id


def update_category(snapshot: dict, category_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    _, category = _find_by_sync_id(categories, category_id, expected_prefix="cat")
    _assert_actor_can_modify(category, payload)
    old_name = str(category.get("name") or "").strip()
    old_kind = str(category.get("kind") or "").strip()

    if "name" in payload:
        category["name"] = _normalize_name(payload.get("name"))
    if "kind" in payload:
        kind = str(payload.get("kind") or "").strip()
        if kind not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid category kind")
        category["kind"] = kind
    for req_key, snapshot_key in [
        ("level", "level"),
        ("sort_order", "sortOrder"),
        ("icon", "icon"),
        ("icon_type", "iconType"),
        ("custom_icon_path", "customIconPath"),
        ("icon_cloud_file_id", "iconCloudFileId"),
        ("icon_cloud_sha256", "iconCloudSha256"),
        ("parent_name", "parentName"),
    ]:
        if req_key in payload:
            category[snapshot_key] = payload.get(req_key)

    new_name = str(category.get("name") or "").strip()
    new_kind = str(category.get("kind") or "").strip()
    if any(
        str(row.get("syncId")) != category_id
        and str(row.get("name", "")).strip().lower() == new_name.lower()
        and str(row.get("kind", "")).strip() == new_kind
        for row in categories
    ):
        raise ValueError("write validation failed: duplicated category")

    if old_name and old_kind and (old_name != new_name or old_kind != new_kind):
        for tx in _ensure_list(target, "items"):
            if tx.get("categoryName") == old_name and tx.get("categoryKind") == old_kind:
                tx["categoryName"] = new_name
                tx["categoryKind"] = new_kind
    _mark_entity_actor(category, payload, create=False)
    return target


def delete_category(snapshot: dict, category_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    idx, category = _find_by_sync_id(categories, category_id, expected_prefix="cat")
    _assert_actor_can_modify(category, payload or {})
    old_name = str(category.get("name") or "").strip()
    old_kind = str(category.get("kind") or "").strip()
    categories.pop(idx)
    if old_name and old_kind:
        for tx in _ensure_list(target, "items"):
            if tx.get("categoryName") == old_name and tx.get("categoryKind") == old_kind:
                tx.pop("categoryName", None)
                tx.pop("categoryKind", None)
    return target


def _split_tags(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _join_tags(tags: list[str]) -> str | None:
    if not tags:
        return None
    return ",".join(dict.fromkeys(tags))


def create_tag(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    name = _normalize_name(payload.get("name"))
    if any(str(row.get("name", "")).strip().lower() == name.lower() for row in tags):
        raise ValueError("write validation failed: duplicated tag")
    sync_id = _new_sync_id("tag")
    item = {"syncId": sync_id, "name": name, "color": payload.get("color")}
    _mark_entity_actor(item, payload, create=True)
    tags.append(item)
    return target, sync_id


def update_tag(snapshot: dict, tag_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    _, tag = _find_by_sync_id(tags, tag_id, expected_prefix="tag")
    _assert_actor_can_modify(tag, payload)
    old_name = str(tag.get("name") or "").strip()
    if "name" in payload:
        new_name = _normalize_name(payload.get("name"))
        if any(
            str(row.get("syncId")) != tag_id
            and str(row.get("name", "")).strip().lower() == new_name.lower()
            for row in tags
        ):
            raise ValueError("write validation failed: duplicated tag")
        tag["name"] = new_name
    if "color" in payload:
        tag["color"] = payload.get("color")

    new_name = str(tag.get("name") or "").strip()
    if old_name and new_name and old_name != new_name:
        for tx in _ensure_list(target, "items"):
            tx_tags = _split_tags(tx.get("tags"))
            if not tx_tags:
                continue
            updated = [new_name if tag_name == old_name else tag_name for tag_name in tx_tags]
            merged = _join_tags(updated)
            if merged is None:
                tx.pop("tags", None)
            else:
                tx["tags"] = merged
    _mark_entity_actor(tag, payload, create=False)
    return target


def delete_tag(snapshot: dict, tag_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    idx, tag = _find_by_sync_id(tags, tag_id, expected_prefix="tag")
    _assert_actor_can_modify(tag, payload or {})
    old_name = str(tag.get("name") or "").strip()
    tags.pop(idx)
    if old_name:
        for tx in _ensure_list(target, "items"):
            tx_tags = [name for name in _split_tags(tx.get("tags")) if name != old_name]
            merged = _join_tags(tx_tags)
            if merged is None:
                tx.pop("tags", None)
            else:
                tx["tags"] = merged
    return target
