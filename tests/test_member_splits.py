"""成员关联 / 分摊（0024）编解码与校验的单元测试。"""
from __future__ import annotations

import json

import pytest

from src.member_splits import (
    apply_snapshot_item,
    decode_row,
    ids_and_names,
    normalize_splits,
    projection_values,
    splits_from_snapshot_item,
    stats_splits,
)


def test_transfer_never_keeps_members() -> None:
    raw = [{"member_id": "mem_1", "name": "张三", "amount": 100}]
    assert normalize_splits(raw, tx_type="transfer", amount=100) == []


@pytest.mark.parametrize("raw", [None, [], "not-a-list", [{"amount": 10}], [None, 3]])
def test_invalid_or_empty_inputs_clear_members(raw) -> None:
    assert normalize_splits(raw, tx_type="expense", amount=100) == []


def test_web_snake_case_and_snapshot_camel_case_both_accepted() -> None:
    web = normalize_splits(
        [{"member_id": "mem_1", "name": "张三", "amount": 100}],
        tx_type="expense",
        amount=100,
    )
    snapshot = normalize_splits(
        [{"memberId": "mem_1", "name": "张三", "amount": 100}],
        tx_type="income",
        amount=100,
    )
    assert web == snapshot == [{"memberId": "mem_1", "name": "张三", "amount": 100.0}]


def test_duplicate_member_ids_merge_amounts() -> None:
    splits = normalize_splits(
        [
            {"member_id": "mem_1", "name": "张三", "amount": 30},
            {"member_id": "mem_1", "amount": 20},
            {"member_id": "mem_2", "name": "李四", "amount": 50},
        ],
        tx_type="expense",
        amount=100,
    )
    assert splits == [
        {"memberId": "mem_1", "name": "张三", "amount": 50.0},
        {"memberId": "mem_2", "name": "李四", "amount": 50.0},
    ]


def test_split_sum_mismatch_raises_value_error() -> None:
    with pytest.raises(ValueError, match="member split sum"):
        normalize_splits(
            [
                {"member_id": "mem_1", "amount": 50},
                {"member_id": "mem_2", "amount": 30},
            ],
            tx_type="expense",
            amount=100,
        )


def test_split_sum_tolerance_is_one_cent() -> None:
    splits = normalize_splits(
        [
            {"member_id": "mem_1", "amount": 33.33},
            {"member_id": "mem_2", "amount": 33.33},
            {"member_id": "mem_3", "amount": 33.33},
        ],
        tx_type="expense",
        amount=100,
    )
    assert len(splits) == 3


def test_amount_none_skips_sum_validation() -> None:
    splits = normalize_splits(
        [{"member_id": "mem_1", "amount": 1}], tx_type="income", amount=None
    )
    assert splits[0]["amount"] == 1.0


def test_projection_values_round_trip() -> None:
    splits = normalize_splits(
        [
            {"member_id": "mem_1", "name": "张三", "amount": 60},
            {"member_id": "mem_2", "name": "李四", "amount": 40},
        ],
        tx_type="expense",
        amount=100,
    )
    values = projection_values(splits)
    assert json.loads(values["member_sync_ids_json"]) == ["mem_1", "mem_2"]
    assert values["member_names_csv"] == "张三,李四"

    ids, names, decoded = decode_row(
        values["member_sync_ids_json"],
        values["member_names_csv"],
        values["member_splits_json"],
    )
    assert ids == ["mem_1", "mem_2"]
    assert names == "张三,李四"
    assert [d["member_id"] for d in decoded] == ["mem_1", "mem_2"]
    assert abs(sum(d["amount"] for d in decoded) - 100) < 0.01


def test_decode_row_tolerates_corrupt_json() -> None:
    ids, names, splits = decode_row("not-json", None, "{broken")
    assert ids == [] and names is None and splits == []


def test_apply_snapshot_item_writes_and_clears() -> None:
    item: dict = {"note": "午餐"}
    splits = normalize_splits(
        [{"member_id": "mem_1", "name": "张三", "amount": 38}],
        tx_type="expense",
        amount=38,
    )
    apply_snapshot_item(item, splits)
    assert item["memberIds"] == ["mem_1"]
    assert item["memberNames"] == "张三"
    assert splits_from_snapshot_item(item) == splits
    assert ids_and_names(splits) == (["mem_1"], "张三")

    apply_snapshot_item(item, [])
    assert "memberIds" not in item
    assert "memberNames" not in item
    assert "memberSplits" not in item
    assert splits_from_snapshot_item(item) == []


def test_stats_splits_filters_non_positive_amounts() -> None:
    raw = json.dumps(
        [
            {"memberId": "mem_1", "name": "张三", "amount": 30},
            {"memberId": "mem_2", "name": "李四", "amount": 0},
            {"memberId": "", "name": "无主", "amount": 10},
        ],
        ensure_ascii=False,
    )
    assert stats_splits(raw) == [("mem_1", "张三", 30.0)]
