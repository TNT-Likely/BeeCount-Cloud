"""成员关联 / 分摊(0024)的统一编解码。

一份数据，三种形态：

====================  =========================================================
形态                    字段
====================  =========================================================
snapshot / SyncChange  ``memberIds: [str]``、``memberNames: "张三,李四"``、
(camelCase)            ``memberSplits: [{"memberId","name","amount"}]``
Web 写请求             ``member_splits: [{"member_id","name","amount"}]``
(snake_case)
read_tx_projection     ``member_sync_ids_json`` / ``member_names_csv`` /
(3 个 Text 列)         ``member_splits_json``
====================  =========================================================

约定：

* 只有 ``expense`` / ``income`` 能关联成员；``transfer`` 一律清空。
* 单成员 = 只有 1 项且 ``amount`` 等于交易金额；多成员 = 分摊。
* 分摊金额之和必须等于交易金额(容差 0.01)，否则写入直接 4xx —— 成员是统计
  维度，脏分摊会让排行口径失真。
* 金额按交易原币存储；统计端跟其它口径一样用 ``coalesce(native_amount, amount)``
  做本位币折算时，按各成员占比等比缩放。
"""
from __future__ import annotations

import json
from typing import Any

ALLOWED_TX_TYPES = {"expense", "income"}
_SUM_TOLERANCE = 0.01


def normalize_splits(
    raw: Any,
    *,
    tx_type: str,
    amount: float | None,
) -> list[dict[str, Any]]:
    """把任意来源的分摊输入归一化成 camelCase 列表。

    ``raw=None`` / 非 list / 空 list 都表示「不关联成员」。同名 memberId 重复时
    合并金额。返回 ``[]`` 表示清空。
    """
    if (tx_type or "").strip() not in ALLOWED_TX_TYPES:
        return []
    if not isinstance(raw, list) or not raw:
        return []

    merged: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        member_id = str(
            entry.get("memberId") or entry.get("member_id") or ""
        ).strip()
        if not member_id:
            continue
        name = str(entry.get("name") or "").strip()
        try:
            value = round(float(entry.get("amount") or 0), 2)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        existing = merged.get(member_id)
        if existing is None:
            merged[member_id] = {
                "memberId": member_id,
                "name": name,
                "amount": value,
            }
        else:
            existing["amount"] = round(existing["amount"] + value, 2)
            if not existing["name"] and name:
                existing["name"] = name

    splits = list(merged.values())
    if not splits:
        return []
    if amount is not None:
        try:
            expected = round(float(amount), 2)
        except (TypeError, ValueError):
            expected = None
        # 先按分四舍五入再比较：33.33 * 3 = 99.99，与 100 的差在二进制浮点里
        # 是 0.010000000000005，直接跟 0.01 比会把合法的三人平摊误判为超出容差。
        total = round(sum(s["amount"] for s in splits), 2)
        if expected is not None and round(abs(total - expected), 2) > _SUM_TOLERANCE:
            raise ValueError(
                "write validation failed: member split sum must equal transaction amount"
            )
    return splits


def ids_and_names(splits: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    ids = list(dict.fromkeys(str(s["memberId"]) for s in splits))
    names = [str(s["name"]) for s in splits if s.get("name")]
    deduped = list(dict.fromkeys(names))
    return ids, ",".join(deduped) or None


def projection_values(splits: list[dict[str, Any]]) -> dict[str, Any]:
    """给 ``projection.upsert_tx`` 的 values 字典用。"""
    ids, names = ids_and_names(splits)
    return {
        "member_sync_ids_json": json.dumps(ids, ensure_ascii=False) if ids else None,
        "member_names_csv": names,
        "member_splits_json": (
            json.dumps(splits, ensure_ascii=False) if splits else None
        ),
    }


def apply_snapshot_item(item: dict[str, Any], splits: list[dict[str, Any]]) -> None:
    """把分摊写进 snapshot item(camelCase)；空列表时清除 3 个 key。"""
    ids, names = ids_and_names(splits)
    if splits:
        item["memberIds"] = ids
        if names:
            item["memberNames"] = names
        else:
            item.pop("memberNames", None)
        item["memberSplits"] = splits
    else:
        item.pop("memberIds", None)
        item.pop("memberNames", None)
        item.pop("memberSplits", None)


def splits_from_snapshot_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    """给 ``snapshot_builder`` / mutator 读既有 item 用。"""
    raw = item.get("memberSplits")
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def decode_row(
    member_sync_ids_json: str | None,
    member_names_csv: str | None,
    member_splits_json: str | None,
) -> tuple[list[str], str | None, list[dict[str, Any]]]:
    """给读接口用，返回 (member_ids, member_names, member_splits[snake_case])。"""
    ids: list[str] = []
    if member_sync_ids_json:
        try:
            parsed = json.loads(member_sync_ids_json)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed]
        except (json.JSONDecodeError, TypeError):
            ids = []
    splits: list[dict[str, Any]] = []
    if member_splits_json:
        try:
            parsed = json.loads(member_splits_json)
            if isinstance(parsed, list):
                splits = [
                    {
                        "member_id": str(e.get("memberId") or e.get("member_id") or ""),
                        "name": str(e.get("name") or ""),
                        "amount": float(e.get("amount") or 0),
                    }
                    for e in parsed
                    if isinstance(e, dict)
                ]
        except (json.JSONDecodeError, TypeError, ValueError):
            splits = []
    if not ids and splits:
        ids = list(dict.fromkeys(s["member_id"] for s in splits if s["member_id"]))
    names = member_names_csv
    if not names and splits:
        names = ",".join(dict.fromkeys(s["name"] for s in splits if s["name"])) or None
    return ids, names, splits


def stats_splits(member_splits_json: str | None) -> list[tuple[str, str, float]]:
    """给统计聚合用，返回 (member_id, name, amount) 三元组。"""
    _ids, _names, splits = decode_row(None, None, member_splits_json)
    out: list[tuple[str, str, float]] = []
    for s in splits:
        if s["member_id"] and s["amount"] > 0:
            out.append((s["member_id"], s["name"], float(s["amount"])))
    return out
