"""账户平账使用普通收支交易和固定分类。"""

from src.snapshot_mutator import create_transaction


def test_balance_settlement_uses_regular_income_and_category() -> None:
    snapshot = {"items": [], "accounts": [], "categories": [], "tags": []}
    result, tx_id = create_transaction(
        snapshot,
        {
            "tx_type": "income",
            "amount": 12.5,
            "account_id": "acc-1",
            "account_name": "Cash",
            "category_id": "cat-income",
            "category_name": "平账",
            "category_kind": "income",
        },
    )

    item = next(row for row in result["items"] if row["syncId"] == tx_id)
    assert item["type"] == "income"
    assert item["amount"] == 12.5
    assert item["categoryId"] == "cat-income"
    assert item["categoryName"] == "平账"
    assert item["categoryKind"] == "income"
    assert item["excludeFromStats"] is False
    assert item["excludeFromBudget"] is False


def test_negative_balance_settlement_uses_regular_expense_and_category() -> None:
    snapshot = {"items": [], "accounts": [], "categories": [], "tags": []}
    result, tx_id = create_transaction(
        snapshot,
        {
            "tx_type": "expense",
            "amount": 7.5,
            "account_id": "acc-1",
            "account_name": "Cash",
            "category_id": "cat-expense",
            "category_name": "平账",
            "category_kind": "expense",
        },
    )

    item = next(row for row in result["items"] if row["syncId"] == tx_id)
    assert item["type"] == "expense"
    assert item["amount"] == 7.5
    assert item["categoryName"] == "平账"
    assert item["categoryKind"] == "expense"
