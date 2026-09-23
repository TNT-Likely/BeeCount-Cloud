"""交易类型的跨写入/读端共享常量。"""

BALANCE_ADJUSTMENT_TRANSACTION_TYPE = "balance_adjustment"
BALANCE_ADJUSTMENT_TAG_NAME = "平账"


def is_balance_adjustment_transaction(tx_type: str | None) -> bool:
    return tx_type == BALANCE_ADJUSTMENT_TRANSACTION_TYPE
