"""Sync change → projection 应用层。

这个模块负责"把一条 SyncChange 怎么落到 read_*_projection 表里"的全部业务
逻辑。从 HTTP 层(``src.routers.sync`` 的 /sync/push 端点)分出来,目的:

1. **关注点分离**:HTTP 层只管路由 / auth / LWW / 事务提交,业务逻辑在这里。
   push_changes 的批量循环里一行 ``apply_change_to_projection(...)`` 即可。
2. **review 粒度**:未来修 projection 写入的逻辑(rename cascade / 字段合并
   / 图标兜底 / 附件 GC),改动集中在本文件,reviewer 不用翻 router 找线索。
3. **复现 + 测试**:业务逻辑脱离 FastAPI 之后,单元测试可以直接构造
   ``SyncChange`` 对象 + 手造 session 跑,不用再过 TestClient。

## 架构概览

Push 路径跟 projection 的交互只有两个接触点:

    /sync/push (router) → apply_change_to_projection(change) → projection.upsert_*

每个 entity type 背后都有三张"表"维护它的行为:

- ``_MERGE_SPECS``        : entity_type → (projection model + payload 字段映射)
                            用来读"现有行",把旧字段补到增量 payload 的缺失位。
- ``_UPSERT_DISPATCH``    : entity_type → projection.upsert_* 函数。
                            merge 完成后真正往 DB 写哪张表。
- ``_DELETE_DISPATCH``    : entity_type → projection.delete_* 函数。
                            delete action 按 entity 类型清理对应 projection
                            行 + 附带资源(附件 / 自定义图标)。

三张表必须**同步**增删。新加 entity 只登记其中两张就会在测试 / assert 时
爆出 KeyError。2026-04 踩过的 ``_merge_with_existing_budget`` 里
``from .models`` 写错的 bug,根因就是 merge 逻辑 5 个函数 copy-paste,新增
entity 时容易只动一两处。改成表驱动后再也复现不了同类问题。

## Rename cascade 的位置

account / category / tag 三种 **user-global** 实体有个特殊动作:name 变了
之后,ReadTxProjection 里的冗余列(account_name / category_name / tags_csv)
也要一起刷。detect 写在 ``apply_change_to_projection`` 里(不是 merge 里),
因为它必须在 upsert 当前实体 *之前* 跑 —— cascade 用的是 SQL 单条 UPDATE
匹配**旧名**,upsert 之后旧名就丢了。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import projection
from .models import (
    Ledger,
    ReadAccountProjection,
    ReadBudgetProjection,
    ReadCategoryProjection,
    ReadTagProjection,
    ReadTxProjection,
    SyncChange,
)
from .services.category_icon import resolve_icon_by_name


# 哪些 entity_type 可以走单条 change 的 projection 应用(其它 entity
# 比如 ``ledger_snapshot`` 是 sync_changes 里的元数据行,不走这条路径)。
INDIVIDUAL_ENTITY_TYPES = {"transaction", "account", "category", "tag", "budget", "ledger"}


# --------------------------------------------------------------------------- #
# Merge with existing projection row                                           #
# --------------------------------------------------------------------------- #
# Mobile 增量 push 只带部分字段(比如只改 name),不带的字段要保留现有值,
# 不能被默认值(0 / None / 空字符串)覆盖。所以写 projection 前先拉已有
# 行,payload 值为 None 的 key 用旧值补齐,再 upsert。
#
# spec 的 fields 是 [(payload_key, projection 列名)] 或
# [(payload_key, projection 列名, transform_fn)]。transform 处理 tx 的
# json 文本列 / datetime isoformat 这种需要格式转换的情况。


def _json_loads_safe(value: Any) -> Any:
    """把 DB 里存的 JSON 字符串列反序列化回 Python 对象。解析失败返回 None。

    projection 里 ``tag_sync_ids_json`` / ``attachments_json`` 是存成文本的
    JSON array。返回给 mobile 做 merge 时要是原生 list,不能直接塞字符串。
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _isoformat_or_none(value: Any) -> Optional[str]:
    """datetime → ISO8601 字符串。None 直接返回。"""
    return value.isoformat() if value else None


# _FieldSpec 的形态二选一:
#   (payload_key, projection_column_name)                       — 直接 getattr
#   (payload_key, projection_column_name, transform_callable)   — 经 transform
_FieldSpec = tuple  # 运行时就是元组,类型系统上表达不了两种 arity


class _MergeSpec:
    """每个 entity_type 的 projection 字段映射 + 对应的 SQLAlchemy model。"""

    __slots__ = ("model", "fields")

    def __init__(self, model: type, fields: list[_FieldSpec]):
        self.model = model
        self.fields = fields


_MERGE_SPECS: dict[str, _MergeSpec] = {
    "account": _MergeSpec(ReadAccountProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("type", "account_type"),
        ("currency", "currency"),
        ("initialBalance", "initial_balance"),
    ]),
    "category": _MergeSpec(ReadCategoryProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("kind", "kind"),
        ("level", "level"),
        ("sortOrder", "sort_order"),
        ("icon", "icon"),
        ("iconType", "icon_type"),
        ("customIconPath", "custom_icon_path"),
        ("iconCloudFileId", "icon_cloud_file_id"),
        ("iconCloudSha256", "icon_cloud_sha256"),
        ("parentName", "parent_name"),
    ]),
    "tag": _MergeSpec(ReadTagProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("color", "color"),
    ]),
    "budget": _MergeSpec(ReadBudgetProjection, [
        ("syncId", "sync_id"),
        ("type", "budget_type"),
        ("categoryId", "category_sync_id"),
        ("amount", "amount"),
        ("period", "period"),
        ("startDay", "start_day"),
        ("enabled", "enabled"),
    ]),
    "transaction": _MergeSpec(ReadTxProjection, [
        ("syncId", "sync_id"),
        ("type", "tx_type"),
        ("amount", "amount"),
        ("happenedAt", "happened_at", _isoformat_or_none),
        ("note", "note"),
        ("categoryId", "category_sync_id"),
        ("categoryName", "category_name"),
        ("categoryKind", "category_kind"),
        ("accountId", "account_sync_id"),
        ("accountName", "account_name"),
        ("fromAccountId", "from_account_sync_id"),
        ("fromAccountName", "from_account_name"),
        ("toAccountId", "to_account_sync_id"),
        ("toAccountName", "to_account_name"),
        ("tags", "tags_csv"),
        ("tagIds", "tag_sync_ids_json", _json_loads_safe),
        ("attachments", "attachments_json", _json_loads_safe),
        ("txIndex", "tx_index"),
        ("createdByUserId", "created_by_user_id"),
    ]),
}


# 按 entity_type 分派到对应的 projection upsert 函数。跟 _MERGE_SPECS 互为
# 表兄弟 —— merge 负责读回补齐字段,这张表负责"合并完的 payload 写到哪个
# projection 表"。新增 entity 忘记登记会在 apply 时 KeyError(有测试覆盖),
# 比以前散在 if/elif 里漏掉一个分支安全。
_UPSERT_DISPATCH: dict[str, Callable] = {
    "account": projection.upsert_account,
    "category": projection.upsert_category,
    "tag": projection.upsert_tag,
    "budget": projection.upsert_budget,
    "transaction": projection.upsert_tx,
}


# Delete 路径:每个 entity 自己的 projection 行 + 可能的附加资源(tx 附件 /
# category 自定义图标)。handler 签名统一成 ``(db, ledger_id, sync_id) -> None``,
# 内部决定要不要做附加 GC。
def _delete_tx(db: Session, ledger_id: str, sync_id: str) -> None:
    # 先收集附件 fileId(删行后 attachments_json 就没了)再删 tx,然后 GC
    # 孤立附件。共享引用(同图多 tx)的会自动保留。
    tx_file_ids = projection.collect_tx_attachment_fileids(
        db, ledger_id=ledger_id, sync_id=sync_id,
    )
    projection.delete_tx(db, ledger_id=ledger_id, sync_id=sync_id)
    projection.gc_orphan_attachments(
        db, ledger_id=ledger_id, file_ids=tx_file_ids,
    )


def _delete_category(db: Session, ledger_id: str, sync_id: str) -> None:
    # 分类自定义图标走 attachment_files。删分类前取自己 + 子分类的
    # icon_cloud_file_id,删完 GC 孤立图标附件。
    cat_file_ids = projection.collect_category_icon_fileids(
        db, ledger_id=ledger_id, sync_id=sync_id,
    )
    projection.delete_category(db, ledger_id=ledger_id, sync_id=sync_id)
    projection.gc_orphan_attachments(
        db, ledger_id=ledger_id, file_ids=cat_file_ids,
    )


_DELETE_DISPATCH: dict[str, Callable[[Session, str, str], None]] = {
    "transaction": _delete_tx,
    "account": lambda db, lid, sid: projection.delete_account(db, ledger_id=lid, sync_id=sid),
    "category": _delete_category,
    "tag": lambda db, lid, sid: projection.delete_tag(db, ledger_id=lid, sync_id=sid),
    "budget": lambda db, lid, sid: projection.delete_budget(db, ledger_id=lid, sync_id=sid),
}


# --------------------------------------------------------------------------- #
# Rename cascade                                                               #
# --------------------------------------------------------------------------- #
# account / category / tag 的 name 变了之后,ReadTxProjection 里用作 denorm
# 列的 account_name / category_name / tags_csv 也要一起刷。必须在 upsert
# 当前实体 **之前** 跑 —— cascade 按 *旧名* 找 tx 行 UPDATE,upsert 之后
# 旧名就丢了。


def _detect_and_run_rename_cascade(
    db: Session,
    *,
    entity_type: str,
    ledger_id: str,
    sync_id: str,
    payload: dict,
) -> None:
    """探测 name 变化,若变了则走一条 SQL UPDATE 刷 tx projection。"""
    new_name = str(payload.get("name") or "").strip()
    if not new_name:
        return

    if entity_type == "account":
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
    elif entity_type == "category":
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
    elif entity_type == "tag":
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


# --------------------------------------------------------------------------- #
# Merge                                                                        #
# --------------------------------------------------------------------------- #


def merge_with_existing(
    db: Session,
    entity_type: str,
    ledger_id: str,
    sync_id: str,
    payload: dict,
) -> dict:
    """查 projection 已有行,把 payload 里缺的 / None 的字段用旧值补齐。

    对 mobile 的增量 push 很关键 —— 只带 diff 的 payload 如果被直接 upsert,
    其它字段会被默认值覆盖。合并后才能用于 ``projection.upsert_*``。

    entity_type 未登记在 _MERGE_SPECS 时(比如 'ledger' 自己),直接返回
    payload 不做处理,让调用方自己定夺。
    """
    spec = _MERGE_SPECS.get(entity_type)
    if spec is None:
        return payload
    existing = db.scalar(
        select(spec.model).where(
            spec.model.ledger_id == ledger_id,
            spec.model.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    base: dict = {}
    for spec_tuple in spec.fields:
        if len(spec_tuple) == 3:
            payload_key, db_attr, transform = spec_tuple
        else:
            payload_key, db_attr = spec_tuple
            transform = None
        value = getattr(existing, db_attr)
        if transform is not None:
            value = transform(value)
        base[payload_key] = value
    return {**base, **{k: v for k, v in payload.items() if v is not None}}


# --------------------------------------------------------------------------- #
# Top-level entry                                                              #
# --------------------------------------------------------------------------- #


def apply_change_to_projection(
    db: Session,
    *,
    ledger_id: str,
    ledger_owner_id: str,
    change: SyncChange,
) -> None:
    """把一条 SyncChange 投到 projection 上(调用方负责事务边界)。

    方案 B 之后这是 push 路径保持 projection 和 sync_changes 一致的**唯一**
    挂点 —— 不再写 ledger_snapshot 行。流程:

      1. ledger entity:更新 Ledger 表的 name / currency(snapshot 已废弃)。
      2. delete action:按 entity 类型清理对应 projection 行 + 附加资源。
      3. upsert action:
         a. parse payload → dict,注入 syncId
         b. (user-global 名字改了时)先走 rename_cascade_* 刷 tx projection
         c. merge_with_existing 把 payload 缺失 / None 的字段补齐
         d. (分类且 icon 空时)拉 byName 兜底
         e. _UPSERT_DISPATCH 写入对应 projection 表

    ``change.change_id`` 作为 ``source_change_id`` 写进行,诊断用:后续要是
    发现某行数据不对,查这一列能定位是哪次 materialize 落的。
    """
    # --- ledger entity(特殊:不是 projection 表,直接改 Ledger) --------- #
    if change.entity_type == "ledger":
        if change.action == "delete":
            return
        payload_raw = _parse_payload(change.payload_json)
        if payload_raw is None:
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

    # --- delete --------------------------------------------------------- #
    if change.action == "delete":
        handler = _DELETE_DISPATCH.get(change.entity_type)
        if handler is not None:
            handler(db, ledger_id, sync_id)
        return

    # --- upsert --------------------------------------------------------- #
    payload = _parse_payload(change.payload_json)
    if payload is None:
        return
    payload.setdefault("syncId", sync_id)

    # rename cascade 必须先于 upsert 当前实体 —— 用的是"旧名" match tx 行。
    if change.entity_type in {"account", "category", "tag"}:
        _detect_and_run_rename_cascade(
            db,
            entity_type=change.entity_type,
            ledger_id=ledger_id,
            sync_id=sync_id,
            payload=payload,
        )

    if change.entity_type in _MERGE_SPECS:
        merged = merge_with_existing(db, change.entity_type, ledger_id, sync_id, payload)
        # 分类 icon 兜底:老 App(Flutter 3.0 及之前)可能推空 icon 的 category。
        # 写进 projection 前按分类名字 byName 推一次,跟 alembic 0002 backfill
        # 对齐,避免 web 端继续看到兜底图。Flutter 3.0.1 做完 write-time
        # migration 后这段可以退役。
        if change.entity_type == "category":
            icon_val = merged.get("icon") if isinstance(merged, dict) else None
            if icon_val is None or (isinstance(icon_val, str) and not icon_val.strip()):
                merged = {**merged, "icon": resolve_icon_by_name(merged.get("name"))}
        _UPSERT_DISPATCH[change.entity_type](
            db,
            ledger_id=ledger_id,
            user_id=ledger_owner_id,
            source_change_id=change.change_id,
            payload=merged,
        )


def _parse_payload(raw: Any) -> Optional[dict]:
    """把 ``SyncChange.payload_json`` 归一成 dict。非法 JSON / 非 dict 返回 None。

    DB 里这列声明成 JSON,但 SQLAlchemy 对 SQLite 的 JSON 类型存进去是字符串,
    取出来也可能是字符串;Postgres 直接反序列化成 dict。两种都要 handle。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if isinstance(raw, dict):
        return raw
    return None
