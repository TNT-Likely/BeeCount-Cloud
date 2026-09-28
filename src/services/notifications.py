"""通知中心写入 helper(MOZE_FEATURE_GAP_SD.md §2.1，Phase 0 地基）。

后续 recurring 到期提醒 / budget 超支提醒 / 信用卡繳款日提醒等功能，各自在
自己的业务逻辑里调用 `create_notification()` 落一行记录即可，故意不做成
集中调度的 job，避免功能之间产生耦合。
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Ledger, Notification

NotificationCategory = Literal[
    "reminder", "budget_alert", "card_due", "card_reward", "system", "debt_unsettled", "dividend",
]


def resolve_ledger_external_id(db: Session, ledger_id: str) -> str | None:
    """把 projection 上存的 `ledger_id`(内部 PK,`ledgers.id` FK)换成前端
    路由/查询参数统一使用的 `external_id`——所有 notification payload 里的
    `ledgerId` 字段都要存这个,不能直接塞内部 PK。2026-08-04 发现的既有
    bug:`credit_card_reminders`/`credit_card_autopay`/`debt_reminders`/
    `recurring_materializer` 四处都曾经直接把内部 `ledger_id` 塞进
    payload,导致前端凭 `ledgerId` 查 `/read/workspace/accounts` 时
    (该端点的 `ledger_id` 查询参数比对的是 `external_id`)永远查不到任何
    账本,静默返回空列表——点「查看详情」看起来毫无反应。"""
    return db.scalar(select(Ledger.external_id).where(Ledger.id == ledger_id))


def create_notification(
    db: Session,
    *,
    user_id: str,
    category: NotificationCategory,
    title: str,
    body: str | None = None,
    payload: dict | None = None,
    priority: int = 0,
) -> Notification:
    """插入一条通知记录。不 commit —— 调用方通常在自己的事务里跟业务写入一起提交。

    `priority` 越大排序越靠前(在列表里排在最上面,不受之后新建立的通知擠動)。
    目前约定:2=欠款(debt_reminders/debt_unsettled_notifications)、
    1=信用卡帐单(credit_card_reminders)、0=其余(预设值)。"""
    notification = Notification(
        user_id=user_id,
        category=category,
        title=title,
        body=body,
        payload_json=payload,
        priority=priority,
    )
    db.add(notification)
    return notification
