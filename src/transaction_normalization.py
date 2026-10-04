"""交易类型决定有效账户字段，防止 partial merge 恢复旧转账关联。"""
from __future__ import annotations

from typing import Any


def normalize_transaction_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    kind = result.get("txType") or result.get("tx_type") or result.get("type") or "expense"
    inactive = (
        ("accountId", "accountName") if kind == "transfer"
        else ("fromAccountId", "fromAccountName", "toAccountId", "toAccountName")
    )
    # 显式 null 也写入同步事件，告诉已保存旧字段的设备清理关联。
    for key in inactive:
        result[key] = None
    return result
