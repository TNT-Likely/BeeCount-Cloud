"""交易类型决定有效账户字段,防止 partial merge 恢复旧转账关联。

0021 起同表治理优惠/转账手续费/合并付款三个扩展字段的类型归属:
- discount(优惠)/ paymentLegs(合并付款)仅 expense 有意义;
- transferFee(转账手续费)仅 transfer 有意义。
income 三者皆无意义。按账户字段同款模式显式置 None —— 既让投影列落
NULL,也让 sync 事件携带显式 null 通知旧字段设备清理。
"""
from __future__ import annotations

from typing import Any


def normalize_transaction_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    kind = result.get("txType") or result.get("tx_type") or result.get("type") or "expense"
    inactive = (
        ("accountId", "accountName") if kind == "transfer"
        else ("fromAccountId", "fromAccountName", "toAccountId", "toAccountName")
    )
    # 显式 null 也写入同步事件,告诉已保存旧字段的设备清理关联。
    for key in inactive:
        result[key] = None
    # 0021:扩展字段按类型归属清理(语义同上)。
    # 0024:成员分摊(memberShares)原本仅 expense 有意义(「谁花的」),但收入同样
    # 需要「谁的收入算到谁头上」,因此 income 不再清 memberShares;transfer(内部
    # 转账)对分摊无意义,仍由下方 transfer 分支清理。
    if kind == "expense":
        result["transferFee"] = None
    elif kind == "transfer":
        result["discount"] = None
        result["paymentLegs"] = None
        result["memberShares"] = None
    else:  # income
        result["discount"] = None
        result["transferFee"] = None
        result["paymentLegs"] = None
        # 收入支持成员分摊(0024 扩展):保留 memberShares,不清空。
    return result
