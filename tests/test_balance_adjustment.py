"""Dedicated balance-adjustment transaction invariants."""

import pytest

from src.snapshot_mutator import create_transaction, update_transaction


def test_create_balance_adjustment_forces_tag_and_exclusions() -> None:
    snapshot = {"items": [], "accounts": [], "categories": [], "tags": []}
    result, tx_id = create_transaction(
        snapshot,
        {
            "tx_type": "balance_adjustment",
            "amount": -12.5,
            "account_id": "acc-1",
            "account_name": "Cash",
            "tags": ["自定义"],
            "exclude_from_stats": False,
            "exclude_from_budget": False,
        },
    )

    item = next(row for row in result["items"] if row["syncId"] == tx_id)
    assert item["type"] == "balance_adjustment"
    assert item["amount"] == -12.5
    assert item["tags"] == "自定义,平账"
    assert item["excludeFromStats"] is True
    assert item["excludeFromBudget"] is True
    assert item["tagIds"] == [result["tags"][0]["syncId"]]
    assert result["tags"][0]["name"] == "平账"


def test_balance_adjustment_cannot_gain_category_or_lose_fixed_tag() -> None:
    snapshot = {"items": [], "accounts": [], "categories": [], "tags": []}
    result, tx_id = create_transaction(
        snapshot,
        {
            "tx_type": "balance_adjustment",
            "amount": 5,
            "account_id": "acc-1",
        },
    )

    with pytest.raises(ValueError, match="cannot have a category"):
        update_transaction(
            result,
            tx_id,
            {
                "category_name": "Should be rejected",
                "tags": [],
                "exclude_from_stats": False,
            },
        )
    # The mutator rejects the category before persisting a malformed item.
    assert result["items"][0]["tags"] == "平账"
