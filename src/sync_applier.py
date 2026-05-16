"""Sync change → projection 应用层。

这个模块负责"把一条 SyncChange 怎么落到 read_*_projection 表里"的全部业务
逻辑。从 HTTP 层(``src.routers.sync`` 的 /sync/push 端点)分出来,目的:

1. **关注点分离**:HTTP 层只管路由 / auth / LWW / 事务提交,业务逻辑在这里。
   push_changes 的批量循环里一行 ``apply_change_to_projection(...)`` 即可。
2. **review 粒度**:未来修 projection 写入的逻辑(rename cascade / 字段合并
   / 图标兜底 / 附件 GC),改动集中在本文件,reviewer 不用翻 router 找线索。
3. **复现 + 测试**:业务逻辑脱离 FastAPI 之后,单元测试可以直接构造
   ``SyncChange`` 对象 + 手造 session 跑,不用再过 TestClient。

## 架构概览(user-global 重构后)

Push 路径跟 projection 的交互按 scope 分两条:

    /sync/push (router)
        ├─ scope='ledger' → apply_change_to_projection(change)      → projection.upsert_tx/budget
        └─ scope='user'   → apply_user_change_to_projection(change) → projection.upsert_account/category/tag

每条路径都有自己的三张"表"驱动 dispatch:

- ledger-scope:``_LEDGER_MERGE_SPECS`` / ``_LEDGER_UPSERT_DISPATCH`` / ``_LEDGER_DELETE_DISPATCH``
- user-scope: ``_USER_MERGE_SPECS`` / ``_USER_UPSERT_DISPATCH`` / ``_USER_DELETE_DISPATCH``

新加 entity 只登记其中两张会在测试 / assert 时爆出 KeyError(2026-04 踩过
的 copy-paste bug 在表驱动后已复现不了)。

## Rename cascade 的位置

account / category / tag 是 user-global 实体,name 变了之后 ReadTxProjection
里的冗余列(account_name / category_name / tags_csv)要一起刷。detect 写在
``apply_user_change_to_projection`` 里(不在 merge 里),因为它必须在 upsert
当前实体 *之前* 跑 —— cascade 用的是 SQL 单条 UPDATE 匹配**旧名**,upsert
之后旧名就丢了。范围是该用户的所有 ledger(单条 SQL WHERE user_id=X,不再
循环 ledger)。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import projection
from .models import (
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    SyncChange,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from .services.category_icon import resolve_icon_by_name


# 哪些 entity_type 可以走单条 change 的 projection 应用(其它 entity
# 比如 ``ledger_snapshot`` 是 sync_changes 里的元数据行,不走这条路径)。
INDIVIDUAL_ENTITY_TYPES = {"transaction", "account", "category", "tag", "budget", "ledger"}

# user-global entity 类型白名单 —— 跟 mobile lib/cloud/sync/change_tracker.dart
# 的 userGlobalEntityTypes 保持一致。push 路径按这个集合分流到 user-scope 应用。
USER_GLOBAL_ENTITY_TYPES = {"account", "category", "tag"}


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


# user-scope merge specs:用 (user_id, sync_id) 当主键检 existing row。
_USER_MERGE_SPECS: dict[str, _MergeSpec] = {
    "account": _MergeSpec(UserAccountProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("type", "account_type"),
        ("currency", "currency"),
        ("initialBalance", "initial_balance"),
        # 扩展字段:跟 mobile lib/data/db.dart Account 表对齐,sync_engine 已经
        # 在 push 这些字段(driftCamel),server 现在落库 + round-trip。
        ("note", "note"),
        ("creditLimit", "credit_limit"),
        ("billingDay", "billing_day"),
        ("paymentDueDay", "payment_due_day"),
        ("bankName", "bank_name"),
        ("cardLastFour", "card_last_four"),
    ]),
    "category": _MergeSpec(UserCategoryProjection, [
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
    "tag": _MergeSpec(UserTagProjection, [
        ("syncId", "sync_id"),
        ("name", "name"),
        ("color", "color"),
    ]),
}


# ledger-scope merge specs:用 (ledger_id, sync_id) 当主键检 existing row。
_LEDGER_MERGE_SPECS: dict[str, _MergeSpec] = {
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


# user-scope upsert dispatch:apply_user_change_to_projection 用。
_USER_UPSERT_DISPATCH: dict[str, Callable] = {
    "account": projection.upsert_account,
    "category": projection.upsert_category,
    "tag": projection.upsert_tag,
}


# ledger-scope upsert dispatch:apply_change_to_projection 用。
_LEDGER_UPSERT_DISPATCH: dict[str, Callable] = {
    "budget": projection.upsert_budget,
    "transaction": projection.upsert_tx,
}


# Delete 路径分两组。
#
# ledger-scope:tx / budget,handler 签名 ``(db, ledger_id, sync_id, user_id)``。
# user-scope:account / category / tag,handler 签名 ``(db, user_id, sync_id)``。
#
# tx 删除时附带 GC tx 附件(走 user_id scope —— attachment GC 已重构);
# category 删除时附带 GC icon 附件(同样 user_id scope)。


def _delete_tx(db: Session, ledger_id: str, sync_id: str, user_id: str) -> None:
    # 先收集附件 fileId(删行后 attachments_json 就没了)再删 tx,然后 GC
    # 孤立附件。共享引用(同图多 tx)的会自动保留。
    tx_file_ids = projection.collect_tx_attachment_fileids(
        db, ledger_id=ledger_id, sync_id=sync_id,
    )
    projection.delete_tx(db, ledger_id=ledger_id, sync_id=sync_id)
    projection.gc_orphan_attachments(
        db, user_id=user_id, file_ids=tx_file_ids,
    )


def _delete_user_category(db: Session, user_id: str, sync_id: str) -> None:
    # 删 user_category_projection 行,再 GC 自身 + 子分类的图标附件。
    cat_file_ids = projection.collect_category_icon_fileids(
        db, user_id=user_id, sync_id=sync_id,
    )
    projection.delete_category(db, user_id=user_id, sync_id=sync_id)
    projection.gc_orphan_attachments(
        db, user_id=user_id, file_ids=cat_file_ids,
    )


_LEDGER_DELETE_DISPATCH: dict[str, Callable[[Session, str, str, str], None]] = {
    "transaction": _delete_tx,
    "budget": lambda db, lid, sid, uid: projection.delete_budget(db, ledger_id=lid, sync_id=sid),
}


_USER_DELETE_DISPATCH: dict[str, Callable[[Session, str, str], None]] = {
    "account": lambda db, uid, sid: projection.delete_account(db, user_id=uid, sync_id=sid),
    "category": _delete_user_category,
    "tag": lambda db, uid, sid: projection.delete_tag(db, user_id=uid, sync_id=sid),
}


# --------------------------------------------------------------------------- #
# Rename cascade                                                               #
# --------------------------------------------------------------------------- #
# account / category / tag 的 name 变了之后,ReadTxProjection 里用作 denorm
# 列的 account_name / category_name / tags_csv 也要一起刷。必须在 upsert
# 当前实体 **之前** 跑 —— cascade 按 *旧名* 找 tx 行 UPDATE,upsert 之后
# 旧名就丢了。


def _detect_and_run_rename_cascade_user(
    db: Session,
    *,
    entity_type: str,
    user_id: str,
    sync_id: str,
    payload: dict,
) -> None:
    """user-global rename cascade:探测 name 变化,刷遍该用户所有 ledger 的
    read_tx_projection denorm 列。account / category / tag 三种。"""
    new_name = str(payload.get("name") or "").strip()
    if not new_name:
        return

    if entity_type == "account":
        prev_row = db.scalar(
            select(UserAccountProjection).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id == sync_id,
            )
        )
        old_name = (prev_row.name or "").strip() if prev_row is not None else ""
        if old_name and old_name != new_name:
            projection.rename_cascade_account(
                db, user_id=user_id, account_sync_id=sync_id, new_name=new_name,
            )
    elif entity_type == "category":
        prev_row = db.scalar(
            select(UserCategoryProjection).where(
                UserCategoryProjection.user_id == user_id,
                UserCategoryProjection.sync_id == sync_id,
            )
        )
        old_name = (prev_row.name or "").strip() if prev_row is not None else ""
        if old_name and old_name != new_name:
            projection.rename_cascade_category(
                db, user_id=user_id, category_sync_id=sync_id,
                new_name=new_name,
                new_kind=str(payload.get("kind") or "").strip() or None,
            )
    elif entity_type == "tag":
        prev_row = db.scalar(
            select(UserTagProjection).where(
                UserTagProjection.user_id == user_id,
                UserTagProjection.sync_id == sync_id,
            )
        )
        old_name = (prev_row.name or "").strip() if prev_row is not None else ""
        if old_name and old_name != new_name:
            projection.rename_cascade_tag(
                db, user_id=user_id, tag_sync_id=sync_id,
                old_name=old_name, new_name=new_name,
            )


# --------------------------------------------------------------------------- #
# Merge                                                                        #
# --------------------------------------------------------------------------- #


def _merge_from_spec(spec: _MergeSpec, existing, payload: dict) -> dict:
    """从 existing row + spec.fields 构造 base dict,再把 payload 里非 None 的
    字段叠加上来。两条 merge 路径(ledger / user)共用这段。"""
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


def merge_with_existing(
    db: Session,
    entity_type: str,
    ledger_id: str,
    sync_id: str,
    payload: dict,
) -> dict:
    """**ledger-scope** merge:查 projection 已有行,把 payload 里缺的 / None 的
    字段用旧值补齐。entity_type 未登记在 _LEDGER_MERGE_SPECS 时(比如 'ledger'
    自己),直接返回 payload 不做处理。"""
    spec = _LEDGER_MERGE_SPECS.get(entity_type)
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
    return _merge_from_spec(spec, existing, payload)


def merge_with_existing_user(
    db: Session,
    entity_type: str,
    user_id: str,
    sync_id: str,
    payload: dict,
) -> dict:
    """**user-scope** merge:查 user_*_projection 已有行,补齐缺失字段。"""
    spec = _USER_MERGE_SPECS.get(entity_type)
    if spec is None:
        return payload
    existing = db.scalar(
        select(spec.model).where(
            spec.model.user_id == user_id,
            spec.model.sync_id == sync_id,
        )
    )
    if existing is None:
        return payload
    return _merge_from_spec(spec, existing, payload)


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
    """把一条 **ledger-scope** SyncChange 投到 projection 上。
    (user-global 走 ``apply_user_change_to_projection``。)

    流程:

      1. ledger entity:更新 Ledger 表的 name / currency(snapshot 已废弃)。
      2. delete action:按 entity 类型清理对应 projection 行 + 附加资源。
      3. upsert action:
         a. parse payload → dict,注入 syncId
         b. merge_with_existing 把 payload 缺失 / None 的字段补齐
         c. _LEDGER_UPSERT_DISPATCH 写入对应 projection 表

    ``change.change_id`` 作为 ``source_change_id`` 写进行,诊断用:后续要是
    发现某行数据不对,查这一列能定位是哪次 materialize 落的。

    防御性:若 entity_type 是 user-global(category/account/tag),调用方走错
    路径 — 抛 AssertionError 让 caller 修(应该走 apply_user_change_to_projection)。
    """
    assert change.entity_type not in USER_GLOBAL_ENTITY_TYPES, (
        f"apply_change_to_projection 收到 user-global entity {change.entity_type},"
        f"应该走 apply_user_change_to_projection(change_id={change.change_id})"
    )

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
        handler = _LEDGER_DELETE_DISPATCH.get(change.entity_type)
        if handler is not None:
            handler(db, ledger_id, sync_id, ledger_owner_id)
        return

    # --- upsert --------------------------------------------------------- #
    payload = _parse_payload(change.payload_json)
    if payload is None:
        return
    payload.setdefault("syncId", sync_id)

    if change.entity_type in _LEDGER_MERGE_SPECS:
        merged = merge_with_existing(db, change.entity_type, ledger_id, sync_id, payload)
        _LEDGER_UPSERT_DISPATCH[change.entity_type](
            db,
            ledger_id=ledger_id,
            user_id=ledger_owner_id,
            source_change_id=change.change_id,
            payload=merged,
        )


def apply_user_change_to_projection(
    db: Session,
    *,
    user_id: str,
    change: SyncChange,
) -> None:
    """把一条 **user-scope** SyncChange 投到 user_*_projection 上。

    流程跟 ledger-scope 对偶,但:
      - 主键查 / 写都按 (user_id, sync_id),跟账本无关
      - rename cascade 也按 user_id 跨该用户所有 ledger 刷 read_tx_projection

    防御性:entity_type 必须在 USER_GLOBAL_ENTITY_TYPES,否则 caller 错路径。
    """
    assert change.entity_type in USER_GLOBAL_ENTITY_TYPES, (
        f"apply_user_change_to_projection 收到非 user-global entity "
        f"{change.entity_type}(change_id={change.change_id})"
    )

    sync_id = change.entity_sync_id

    # --- delete --------------------------------------------------------- #
    if change.action == "delete":
        handler = _USER_DELETE_DISPATCH.get(change.entity_type)
        if handler is not None:
            handler(db, user_id, sync_id)
        return

    # --- upsert --------------------------------------------------------- #
    payload = _parse_payload(change.payload_json)
    if payload is None:
        return
    payload.setdefault("syncId", sync_id)

    # rename cascade 必须先于 upsert 当前实体 —— 用的是"旧名" match tx 行。
    _detect_and_run_rename_cascade_user(
        db,
        entity_type=change.entity_type,
        user_id=user_id,
        sync_id=sync_id,
        payload=payload,
    )

    if change.entity_type in _USER_MERGE_SPECS:
        merged = merge_with_existing_user(
            db, change.entity_type, user_id, sync_id, payload,
        )
        # 分类 icon 兜底:老 App(Flutter 3.0 及之前)可能推空 icon 的 category。
        # 写进 projection 前按分类名字 byName 推一次,跟 alembic 0002 backfill
        # 对齐,避免 web 端继续看到兜底图。Flutter 3.0.1 做完 write-time
        # migration 后这段可以退役。
        if change.entity_type == "category":
            icon_val = merged.get("icon") if isinstance(merged, dict) else None
            if icon_val is None or (isinstance(icon_val, str) and not icon_val.strip()):
                merged = {**merged, "icon": resolve_icon_by_name(merged.get("name"))}
        _USER_UPSERT_DISPATCH[change.entity_type](
            db,
            user_id=user_id,
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
