"""退款功能（0025）单元：snapshot_mutator 层校验。

覆盖「强制从原单发起」的核心不变量：
  - 退款必须关联一笔已存在的原单（refund_of_sync_id）
  - 原单必须是 expense（关联 income 等其它类型被拒）
  - 退款金额必须为正
  - 「已退总额 + 本次退款」不得超过原单金额（浮点容差）
  - 退款交易标记 isRefund=True，原单保持 isRefund=False / type=expense

更上层的「退款冲减原支出分类」「不计入收入」由读接口 analytics 负责，已在
scripts/qa/smoke_refunds.py 的端到端冒烟中验证（餐饮美食净增 70）。
"""
from __future__ import annotations

import pytest

from src.snapshot_mutator import create_transaction

_BASE = {
    "items": [],
    "accounts": [],
    "categories": [],
    "tags": [],
}


def _make_expense(snapshot: dict, amount: float = 100.0, category: str = "餐饮美食"):
    result, tx_id = create_transaction(
        snapshot,
        {
            "tx_type": "expense",
            "amount": amount,
            "happened_at": "2026-11-10T12:00:00+08:00",
            "note": "原单",
            "account_name": "支付宝",
            "category_name": category,
            "category_kind": "expense",
        },
    )
    return result, tx_id


def test_refund_requires_refund_of_sync_id() -> None:
    snapshot = dict(_BASE)
    with pytest.raises(ValueError):
        create_transaction(
            snapshot,
            {
                "tx_type": "refund",
                "amount": 30.0,
                "happened_at": "2026-11-11T12:00:00+08:00",
                "account_name": "Cash",
            },
        )


def test_refund_requires_existing_original() -> None:
    snapshot = dict(_BASE)
    with pytest.raises(ValueError):
        create_transaction(
            snapshot,
            {
                "tx_type": "refund",
                "amount": 30.0,
                "happened_at": "2026-11-11T12:00:00+08:00",
                "account_name": "Cash",
                "refund_of_sync_id": "tx-does-not-exist",
            },
        )


def test_refund_must_target_expense_not_income() -> None:
    snapshot = dict(_BASE)
    result, inc_id = create_transaction(
        snapshot,
        {
            "tx_type": "income",
            "amount": 200.0,
            "happened_at": "2026-11-09T12:00:00+08:00",
            "account_name": "支付宝",
            "category_name": "工资薪酬",
            "category_kind": "income",
        },
    )
    with pytest.raises(ValueError):
        create_transaction(
            result,
            {
                "tx_type": "refund",
                "amount": 10.0,
                "happened_at": "2026-11-11T12:00:00+08:00",
                "account_name": "Cash",
                "refund_of_sync_id": inc_id,
            },
        )


def test_refund_amount_must_be_positive() -> None:
    snapshot = dict(_BASE)
    result, orig_id = _make_expense(snapshot)
    with pytest.raises(ValueError):
        create_transaction(
            result,
            {
                "tx_type": "refund",
                "amount": 0.0,
                "happened_at": "2026-11-11T12:00:00+08:00",
                "account_name": "Cash",
                "refund_of_sync_id": orig_id,
            },
        )


def test_refund_cannot_exceed_original_total() -> None:
    snapshot = dict(_BASE)
    result, orig_id = _make_expense(snapshot, amount=100.0)
    # 退 80 → 允许
    result, _rf1 = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 80.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_id,
        },
    )
    # 再退 30 → 总额 110 > 100 → 拒绝
    with pytest.raises(ValueError):
        create_transaction(
            result,
            {
                "tx_type": "refund",
                "amount": 30.0,
                "happened_at": "2026-11-12T12:00:00+08:00",
                "account_name": "Cash",
                "refund_of_sync_id": orig_id,
            },
        )
    # 再退 20 → 总额 100 ≤ 100 → 允许
    result, rf2 = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 20.0,
            "happened_at": "2026-11-12T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_id,
        },
    )
    rf_item = next(it for it in result["items"] if it["syncId"] == rf2)
    assert rf_item["type"] == "refund"
    assert rf_item["refundOfSyncId"] == orig_id
    assert rf_item["isRefund"] is True


def test_refund_marks_item_and_preserves_original() -> None:
    snapshot = dict(_BASE)
    result, orig_id = _make_expense(snapshot, amount=100.0)
    result, rf_id = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 30.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_id,
        },
    )
    rf_item = next(it for it in result["items"] if it["syncId"] == rf_id)
    assert rf_item["type"] == "refund"
    assert rf_item["refundOfSyncId"] == orig_id
    assert rf_item["isRefund"] is True
    # 原单保持 expense 且未标记为退款
    orig_item = next(it for it in result["items"] if it["syncId"] == orig_id)
    assert orig_item["type"] == "expense"
    assert orig_item.get("isRefund") is False


def test_refund_note_derived_from_original() -> None:
    """退款(0025)备注规则:退款流水备注 = 原交易备注,由后端派生(无后缀)。"""
    from src.snapshot_mutator import update_transaction

    snapshot = dict(_BASE)
    result, orig_id = _make_expense(snapshot, amount=100.0)
    # 原单备注为 "午餐"
    orig_item = next(it for it in result["items"] if it["syncId"] == orig_id)
    orig_item["note"] = "午餐"
    # 创建退款,前端不再传 note;后端派生
    result, rf_id = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 30.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_id,
        },
    )
    rf_item = next(it for it in result["items"] if it["syncId"] == rf_id)
    assert rf_item["note"] == "午餐"
    # 原单无备注时,退款备注也为空
    snapshot2 = dict(_BASE)
    result2, orig2 = _make_expense(snapshot2, amount=50.0)
    orig2_item = next(it for it in result2["items"] if it["syncId"] == orig2)
    orig2_item["note"] = ""
    result2, rf2 = create_transaction(
        result2,
        {
            "tx_type": "refund",
            "amount": 10.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig2,
        },
    )
    rf2_item = next(it for it in result2["items"] if it["syncId"] == rf2)
    assert rf2_item["note"] == ""


def test_original_note_change_syncs_refund_note() -> None:
    """原交易(被退)备注被修改时,联动更新关联退款备注 = 新备注(无后缀)。"""
    from src.snapshot_mutator import update_transaction

    snapshot = dict(_BASE)
    result, orig_id = _make_expense(snapshot, amount=100.0)
    result, rf_id = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 30.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_id,
        },
    )
    # 修改原单备注(走完整 snapshot,refund 项存在 → 联动生效)
    result = update_transaction(result, orig_id, {"note": "晚餐"})
    orig_item = next(it for it in result["items"] if it["syncId"] == orig_id)
    rf_item = next(it for it in result["items"] if it["syncId"] == rf_id)
    assert orig_item["note"] == "晚餐"
    assert rf_item["note"] == "晚餐"
    # 多笔退款都应被同步
    result, rf2_id = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 20.0,
            "happened_at": "2026-11-12T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_id,
        },
    )
    result = update_transaction(result, orig_id, {"note": "夜宵"})
    rf1_item = next(it for it in result["items"] if it["syncId"] == rf_id)
    rf2_item = next(it for it in result["items"] if it["syncId"] == rf2_id)
    assert rf1_item["note"] == "夜宵"
    assert rf2_item["note"] == "夜宵"
    # 清空原单备注 → 退款备注也为空
    result = update_transaction(result, orig_id, {"note": ""})
    rf1_item = next(it for it in result["items"] if it["syncId"] == rf_id)
    assert rf1_item["note"] == ""


def test_original_note_change_does_not_touch_unrelated_refunds() -> None:
    """改 A 原单备注不应影响 B 原单的退款备注。"""
    from src.snapshot_mutator import update_transaction

    snapshot = dict(_BASE)
    result, orig_a = _make_expense(snapshot, amount=100.0)
    result, orig_b = _make_expense(result, amount=80.0)
    result, rf_a = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 30.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_a,
        },
    )
    result, rf_b = create_transaction(
        result,
        {
            "tx_type": "refund",
            "amount": 20.0,
            "happened_at": "2026-11-11T12:00:00+08:00",
            "account_name": "Cash",
            "refund_of_sync_id": orig_b,
        },
    )
    result = update_transaction(result, orig_a, {"note": "改A"})
    rf_a_item = next(it for it in result["items"] if it["syncId"] == rf_a)
    rf_b_item = next(it for it in result["items"] if it["syncId"] == rf_b)
    assert rf_a_item["note"] == "改A"
    # B 的退款备注仍来自 B 原单(note="原单"),不被 A 的改动影响
    assert rf_b_item["note"] == "原单"

