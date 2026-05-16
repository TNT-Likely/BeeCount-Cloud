"""从 projection 表按需拼装 snapshot dict。

方案 B 里 projection 是权威源,snapshot 不再 runtime 写入。但 mobile 协议
(`/sync/full`)、snapshot_mutator(web write 路径)还吃 snapshot dict 作输入,所以
提供一个按 (ledger_id, max_change_id) 缓存的 builder。

字段 shape 跟原先 mobile push 来的 snapshot 完全对齐 —— mobile 客户端零改动。
"""
from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)


def _to_iso_utc(dt) -> str | None:
    """Match snapshot_mutator._to_iso8601 output format ——带 +00:00 后缀。
    SQLite 存 DateTime 可能返回 naive datetime,这里补 UTC 再 isoformat。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def build(db: Session, ledger: Ledger) -> dict[str, Any]:
    """从 projection 5 张表 + Ledger 元数据拼装完整 snapshot dict。

    热路径 —— 用 SQL Core 跳过 ORM hydration(ORM 5000 行 ~65ms,Core ~20ms)。
    调用点:`/sync/full`、`_commit_write` 取 prev 快照做 diff、admin debug。
    """
    ledger_id = ledger.id
    user_id = ledger.user_id

    # Items —— SQL Core,按列顺序取 tuple,比 ORM 快 3 倍
    items: list[dict[str, Any]] = []
    tx_stmt = select(
        ReadTxProjection.sync_id,
        ReadTxProjection.tx_type,
        ReadTxProjection.amount,
        ReadTxProjection.happened_at,
        ReadTxProjection.note,
        ReadTxProjection.category_sync_id,
        ReadTxProjection.category_name,
        ReadTxProjection.category_kind,
        ReadTxProjection.account_sync_id,
        ReadTxProjection.account_name,
        ReadTxProjection.from_account_sync_id,
        ReadTxProjection.from_account_name,
        ReadTxProjection.to_account_sync_id,
        ReadTxProjection.to_account_name,
        ReadTxProjection.tags_csv,
        ReadTxProjection.tag_sync_ids_json,
        ReadTxProjection.attachments_json,
        ReadTxProjection.tx_index,
        ReadTxProjection.created_by_user_id,
    ).where(ReadTxProjection.ledger_id == ledger_id).order_by(
        ReadTxProjection.happened_at.desc(),
        ReadTxProjection.tx_index.desc(),
    )
    for row in db.execute(tx_stmt).all():
        (sync_id, tx_type, amount, happened_at, note,
         cat_sid, cat_name, cat_kind,
         acc_sid, acc_name,
         from_sid, from_name,
         to_sid, to_name,
         tags_csv, tag_ids_json, attachments_json,
         tx_index, created_by) = row
        item: dict[str, Any] = {
            "syncId": sync_id,
            "type": tx_type,
            "amount": amount,
            "happenedAt": _to_iso_utc(happened_at),
        }
        if note is not None:
            item["note"] = note
        if cat_sid:
            item["categoryId"] = cat_sid
        if cat_name:
            item["categoryName"] = cat_name
        if cat_kind:
            item["categoryKind"] = cat_kind
        if acc_sid:
            item["accountId"] = acc_sid
        if acc_name:
            item["accountName"] = acc_name
        if from_sid:
            item["fromAccountId"] = from_sid
        if from_name:
            item["fromAccountName"] = from_name
        if to_sid:
            item["toAccountId"] = to_sid
        if to_name:
            item["toAccountName"] = to_name
        if tags_csv:
            item["tags"] = tags_csv
        if tag_ids_json:
            try:
                tag_ids = json.loads(tag_ids_json)
                if isinstance(tag_ids, list) and tag_ids:
                    item["tagIds"] = tag_ids
            except json.JSONDecodeError:
                pass
        if attachments_json:
            try:
                atts = json.loads(attachments_json)
                if isinstance(atts, list) and atts:
                    item["attachments"] = atts
            except json.JSONDecodeError:
                pass
        if tx_index:
            item["txIndex"] = tx_index
        if created_by:
            item["createdByUserId"] = created_by
        items.append(item)

    # Accounts —— user-global per-user 表,按 user_id 取。snapshot 内仍把全用户
     # 的账户都铺出来:mobile 早期版本依赖 snapshot.accounts 完整 — 用户多账本
     # 时数据一致(同一份 accounts 拷贝到每个账本的 snapshot)。
    accounts: list[dict[str, Any]] = []
    acc_stmt = select(
        UserAccountProjection.sync_id,
        UserAccountProjection.name,
        UserAccountProjection.account_type,
        UserAccountProjection.currency,
        UserAccountProjection.initial_balance,
        UserAccountProjection.note,
        UserAccountProjection.credit_limit,
        UserAccountProjection.billing_day,
        UserAccountProjection.payment_due_day,
        UserAccountProjection.bank_name,
        UserAccountProjection.card_last_four,
    ).where(UserAccountProjection.user_id == user_id)
    for (
        sid,
        name,
        acc_type,
        acc_ccy,
        init_bal,
        note,
        credit_limit,
        billing_day,
        payment_due_day,
        bank_name,
        card_last_four,
    ) in db.execute(acc_stmt).all():
        acc: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if acc_type:
            acc["type"] = acc_type
        if acc_ccy:
            acc["currency"] = acc_ccy
        if init_bal is not None:
            acc["initialBalance"] = init_bal
        if note:
            acc["note"] = note
        if credit_limit is not None:
            acc["creditLimit"] = credit_limit
        if billing_day is not None:
            acc["billingDay"] = billing_day
        if payment_due_day is not None:
            acc["paymentDueDay"] = payment_due_day
        if bank_name:
            acc["bankName"] = bank_name
        if card_last_four:
            acc["cardLastFour"] = card_last_four
        accounts.append(acc)

    # Categories —— 同 accounts,user-global per-user。
    categories: list[dict[str, Any]] = []
    cat_stmt = select(
        UserCategoryProjection.sync_id,
        UserCategoryProjection.name,
        UserCategoryProjection.kind,
        UserCategoryProjection.level,
        UserCategoryProjection.sort_order,
        UserCategoryProjection.icon,
        UserCategoryProjection.icon_type,
        UserCategoryProjection.custom_icon_path,
        UserCategoryProjection.icon_cloud_file_id,
        UserCategoryProjection.icon_cloud_sha256,
        UserCategoryProjection.parent_name,
    ).where(UserCategoryProjection.user_id == user_id).order_by(
        UserCategoryProjection.sort_order.asc(),
        UserCategoryProjection.name.asc(),
    )
    for (sid, name, kind, level, sort_order, icon, icon_type,
         custom_icon, icon_fid, icon_sha, parent) in db.execute(cat_stmt).all():
        cat: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if kind:
            cat["kind"] = kind
        if level is not None:
            cat["level"] = level
        if sort_order is not None:
            cat["sortOrder"] = sort_order
        if icon:
            cat["icon"] = icon
        if icon_type:
            cat["iconType"] = icon_type
        if custom_icon:
            cat["customIconPath"] = custom_icon
        if icon_fid:
            cat["iconCloudFileId"] = icon_fid
        if icon_sha:
            cat["iconCloudSha256"] = icon_sha
        if parent:
            cat["parentName"] = parent
        categories.append(cat)

    # Tags —— user-global per-user。
    tags: list[dict[str, Any]] = []
    tag_stmt = select(
        UserTagProjection.sync_id,
        UserTagProjection.name,
        UserTagProjection.color,
    ).where(UserTagProjection.user_id == user_id).order_by(UserTagProjection.name.asc())
    for sid, name, color in db.execute(tag_stmt).all():
        t: dict[str, Any] = {"syncId": sid, "name": name or ""}
        if color:
            t["color"] = color
        tags.append(t)

    # Budgets
    # mobile sync_engine._applyBudgetChange 用 payload['ledgerSyncId'] 解析本地
    # ledger int id(不像 tx 用 change.ledger_id 字段),所以 budget snapshot 必
    # 须显式带这个字段;不带则 mobile 收到 change 后会因 localLedgerId==null 直接
    # skip,web 改了 mobile 那边永远刷不出来。
    budgets: list[dict[str, Any]] = []
    bud_stmt = select(
        ReadBudgetProjection.sync_id,
        ReadBudgetProjection.budget_type,
        ReadBudgetProjection.category_sync_id,
        ReadBudgetProjection.amount,
        ReadBudgetProjection.period,
        ReadBudgetProjection.start_day,
        ReadBudgetProjection.enabled,
    ).where(ReadBudgetProjection.ledger_id == ledger_id)
    for sid, btype, cat_sid, amt, period, start_day, enabled in db.execute(bud_stmt).all():
        b: dict[str, Any] = {"syncId": sid, "ledgerSyncId": ledger.external_id}
        if btype:
            b["type"] = btype
        if cat_sid:
            b["categoryId"] = cat_sid
        if amt is not None:
            b["amount"] = amt
        if period:
            b["period"] = period
        if start_day is not None:
            b["startDay"] = start_day
        b["enabled"] = bool(enabled)
        budgets.append(b)

    return {
        # ledgerSyncId 给 mutator 用 —— 新建预算时要把它写进 budget payload,
        # 让 mobile sync_engine._applyBudgetChange 能解析本地 ledger int id。
        "ledgerSyncId": ledger.external_id,
        "ledgerName": ledger.name or ledger.external_id,
        "currency": ledger.currency or "CNY",
        "count": len(items),
        "items": items,
        "accounts": accounts,
        "categories": categories,
        "tags": tags,
        "budgets": budgets,
    }


def latest_change_id(db: Session, ledger_id: str) -> int:
    """Ledger 的 latest change_id(任意 entity_type),当作"当前版本号"。"""
    return int(
        db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
        )
        or 0
    )
