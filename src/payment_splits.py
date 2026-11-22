"""支出组合付款(一笔业务、多资金源)的统一编解码。

形态对齐 member_splits(0024)：

====================  =========================================================
形态                    字段
====================  =========================================================
snapshot / SyncChange  ``paymentSplits: [{"accountId","accountName","amount"}]``
(camelCase)
Web 写请求             ``payment_splits: [{"account_id","account_name","amount"}]``
(snake_case)
read_tx_projection     ``payment_splits_json`` (Text, JSON 数组)
====================  =========================================================

约定：

* 仅 ``expense`` 支持组合付款；其它类型一律清空(产品当前仅做支出组合付款)。
* 至少 2 条、至多 10 条拆分；同一 accountId 不可重复；amount > 0。
* 各项金额之和必须等于交易总额 amount(容差 0.01)。
* 存在拆分时,交易主 account 字段不再代表单一扣款账户(由各 split 承担),
  余额/账户统计按 split 明细扣减,总额只计一次。
"""
from __future__ import annotations

import json
from typing import Any

ALLOWED_TX_TYPES = {"expense"}
_SUM_TOLERANCE = 0.01
MIN_SPLITS = 2
MAX_SPLITS = 10


def _round2(v: float) -> float:
    return round(float(v) + 1e-12, 2)


def normalize_splits(
    raw: Any,
    *,
    tx_type: str,
    amount: float | None,
) -> list[dict[str, Any]]:
    """把任意来源的拆分输入归一化成 camelCase 列表。

    ``raw=None`` / 非 list / 空 list 都表示「不使用组合付款」(返回 [])。
    同一 accountId 重复时合并金额。amount<=0 的项丢弃。
    """
    if (tx_type or "").strip() not in ALLOWED_TX_TYPES:
        return []
    if not isinstance(raw, list) or not raw:
        return []

    merged: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        account_id = str(
            entry.get("accountId") or entry.get("account_id") or ""
        ).strip()
        if not account_id:
            continue
        account_name = str(
            entry.get("accountName") or entry.get("account_name") or ""
        ).strip()
        try:
            value = _round2(float(entry.get("amount") or 0))
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        existing = merged.get(account_id)
        if existing is None:
            merged[account_id] = {
                "accountId": account_id,
                "accountName": account_name,
                "amount": value,
            }
        else:
            existing["amount"] = _round2(existing["amount"] + value)
            if not existing["accountName"] and account_name:
                existing["accountName"] = account_name

    splits = list(merged.values())
    if not splits:
        return []
    if len(splits) < MIN_SPLITS:
        raise ValueError(
            "write validation failed: combined payment requires at least 2 accounts"
        )
    if len(splits) > MAX_SPLITS:
        raise ValueError(
            "write validation failed: too many combined payment accounts"
        )
    if amount is not None:
        try:
            expected = _round2(float(amount))
        except (TypeError, ValueError):
            expected = None
        total = _round2(sum(s["amount"] for s in splits))
        if expected is not None and abs(total - expected) > _SUM_TOLERANCE:
            raise ValueError(
                "write validation failed: combined payment split sum must equal transaction amount"
            )
    return splits


def projection_values(splits: list[dict[str, Any]]) -> dict[str, Any]:
    """给 projection.upsert_tx 的 values 字典用。"""
    return {
        "payment_splits_json": (
            json.dumps(splits, ensure_ascii=False) if splits else None
        ),
    }


def apply_snapshot_item(item: dict[str, Any], splits: list[dict[str, Any]]) -> None:
    """把拆分写进 snapshot item(camelCase)；空列表时清除 key。"""
    if splits:
        item["paymentSplits"] = splits
    else:
        item.pop("paymentSplits", None)


def splits_from_snapshot_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("paymentSplits")
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def decode_row(payment_splits_json: str | None) -> list[dict[str, Any]]:
    """给读接口用，返回 snake_case 拆分列表。"""
    splits: list[dict[str, Any]] = []
    if not payment_splits_json:
        return splits
    try:
        parsed = json.loads(payment_splits_json)
    except (json.JSONDecodeError, TypeError):
        return splits
    if not isinstance(parsed, list):
        return splits
    for e in parsed:
        if not isinstance(e, dict):
            continue
        account_id = str(e.get("accountId") or e.get("account_id") or "").strip()
        if not account_id:
            continue
        try:
            amt = float(e.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        splits.append(
            {
                "account_id": account_id,
                "account_name": str(e.get("accountName") or e.get("account_name") or ""),
                "amount": _round2(amt),
            }
        )
    return splits
