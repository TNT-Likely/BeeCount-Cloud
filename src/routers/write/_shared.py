"""write.py 的共享 helper 层。

原 src/routers/write.py(1658 行)按路由拆成 6 个子模块后,所有 endpoint
都依赖的辅助函数 / 常量 / 响应表 / 写入引擎(_commit_write /
_commit_write_fast_tx / _diff_entity_list / _emit_entity_diffs /
idempotency key 机制 / normalize helper / projection upsert-deleter 派发表)
集中在这里。

子模块只管:
  - endpoint 路由定义
  - 参数 / 权限校验
  - 调 _commit_write / _prepare_write / 其它 _shared 里的 helper
  - 返回 WriteCommitMeta

修改 snapshot 写入 / idempotency / cascade 规则应当来这里改一处即可,
修改单个 entity 的 endpoint 行为在对应子模块改。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ...concurrency import lock_ledger_for_materialize
from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user, require_any_scopes, require_scopes
from ...ledger_access import (
    ROLE_EDITOR,
    ROLE_OWNER,
    get_accessible_ledger_by_external_id,
)
from ...models import (
    AuditLog,
    Ledger,
    LedgerMember,
    ReadCardRewardRuleProjection,
    ReadDebtProjection,
    ReadStockTradeProjection,
    ReadProjectProjection,
    ReadRecurringRuleProjection,
    ReadTxProjection,
    ReadTxTemplateProjection,
    SyncChange,
    SyncPushIdempotency,
    User,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
)
from ...schemas import (
    WriteAccountCreateRequest,
    WriteAccountDeleteRequest,
    WriteAccountReorderRequest,
    WriteAccountUpdateRequest,
    WriteBalanceAdjustmentRequest,
    WriteBudgetCreateRequest,
    WriteBudgetUpdateRequest,
    WriteCardPaymentRequest,
    WriteCardRewardManualPayoutRequest,
    WriteCardRewardRuleCreateRequest,
    WriteCardRewardRuleUpdateRequest,
    WriteCategoryCreateRequest,
    WriteCategoryUpdateRequest,
    WriteCommitMeta,
    WriteDebtCreateRequest,
    WriteDebtUpdateRequest,
    WriteEntityDeleteRequest,
    WriteInstallmentEarlyRepayRequest,
    WriteInstallmentPayoffRequest,
    WriteInstallmentPeriodRefundRequest,
    WriteInstallmentPeriodUpdateRequest,
    WriteInstallmentPlanCreateRequest,
    WriteInstallmentPlanUpdateRequest,
    WriteInstallmentRebalanceRequest,
    WriteLedgerCreateRequest,
    WriteLedgerMetaUpdateRequest,
    WriteProjectCategoryBudgetCreateRequest,
    WriteProjectCategoryBudgetUpdateRequest,
    WriteProjectCreateRequest,
    WriteProjectUpdateRequest,
    WriteRecurringOccurrenceUpdateRequest,
    WriteRecurringRuleCreateRequest,
    WriteRecurringRuleDeleteRequest,
    WriteRecurringRuleUpdateRequest,
    WriteRecurringUpdateFromRequest,
    WriteStatementClearConfirmationsRequest,
    WriteStockTradeCreateRequest,
    WriteStockTradeUpdateRequest,
    WriteTagCreateRequest,
    WriteTagUpdateRequest,
    WriteTransactionCreateRequest,
    WriteTransactionUpdateRequest,
    WriteTxTemplateApplyRequest,
    WriteTxTemplateCreateRequest,
    WriteTxTemplateUpdateRequest,
)
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE
from ... import projection, snapshot_builder, snapshot_cache
from ...sync_applier import USER_GLOBAL_ENTITY_TYPES
from ...snapshot_mutator import (
    create_account,
    create_budget,
    create_card_reward_rule,
    create_category,
    create_debt,
    create_installment_period,
    create_installment_plan,
    create_project,
    create_project_category_budget,
    create_recurring_rule,
    create_tag,
    create_transaction,
    create_tx_template,
    delete_account,
    delete_budget,
    delete_card_reward_rule,
    delete_category,
    delete_debt,
    delete_installment_period,
    delete_installment_plan,
    delete_project,
    delete_project_category_budget,
    delete_recurring_rule,
    delete_tag,
    delete_transaction,
    delete_tx_template,
    ensure_snapshot_v2,
    update_account,
    update_budget,
    update_card_reward_rule,
    update_category,
    update_debt,
    update_installment_period,
    update_installment_plan,
    update_project,
    update_project_category_budget,
    update_recurring_rule,
    update_tag,
    update_transaction,
    update_tx_template,
)

logger = logging.getLogger(__name__)

# router instance lives in each sub-module (ledgers.py / transactions.py / ...).
# _shared.py is helper-only, no routes.
settings = get_settings()
_WRITE_SCOPE_DEP = (
    require_any_scopes(SCOPE_WEB_WRITE, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_WEB_WRITE)
)

_TRANSACTION_WRITE_ROLES = {ROLE_OWNER, ROLE_EDITOR}
_OWNER_ONLY_ROLES = {ROLE_OWNER}
_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_403_FORBIDDEN: {
        "description": "Write role forbidden",
    },
    status.HTTP_404_NOT_FOUND: {
        "description": "Ledger or entity not found",
    },
    status.HTTP_409_CONFLICT: {
        "description": "Write conflict",
        "content": {
            "application/json": {
                "example": {
                    "error": {
                        "code": "WRITE_CONFLICT",
                        "message": "Write conflict",
                        "request_id": "req_xxx",
                    },
                    "detail": "Write conflict",
                    "latest_change_id": 12,
                    "latest_server_timestamp": "2026-02-24T12:00:00+00:00",
                }
            }
        },
    },
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# 拆成两套 dispatcher:user-global(签名 user_id)和 ledger-scope(签名 ledger_id)。
# user-global entity 在 SyncChange / projection 两层都不依附 ledger,详见
# .docs/user-global-refactor/plan.md。
_USER_PROJECTION_UPSERTERS: dict[str, Any] = {
    "account": projection.upsert_account,
    "category": projection.upsert_category,
    "tag": projection.upsert_tag,
    "card_reward_rule": projection.upsert_card_reward_rule,
}
_USER_PROJECTION_DELETERS: dict[str, Any] = {
    "account": projection.delete_account,
    "category": projection.delete_category,
    "tag": projection.delete_tag,
    "card_reward_rule": projection.delete_card_reward_rule,
}
_LEDGER_PROJECTION_UPSERTERS: dict[str, Any] = {
    "transaction": projection.upsert_tx,
    "budget": projection.upsert_budget,
    "recurring_rule": projection.upsert_recurring_rule,
    "installment_plan": projection.upsert_installment_plan,
    "installment_period": projection.upsert_installment_period,
    "debt": projection.upsert_debt,
    "project": projection.upsert_project,
    "project_category_budget": projection.upsert_project_category_budget,
    "tx_template": projection.upsert_tx_template,
    "stock_trade": projection.upsert_stock_trade,
}
_LEDGER_PROJECTION_DELETERS: dict[str, Any] = {
    "transaction": projection.delete_tx,
    "budget": projection.delete_budget,
    "recurring_rule": projection.delete_recurring_rule,
    "installment_plan": projection.delete_installment_plan,
    "installment_period": projection.delete_installment_period,
    "debt": projection.delete_debt,
    "project": projection.delete_project,
    "project_category_budget": projection.delete_project_category_budget,
    "tx_template": projection.delete_tx_template,
    "stock_trade": projection.delete_stock_trade,
}

# tx 里的 denormalized 字段 —— 当 account/category/tag rename 时,
# snapshot_mutator 会 cascade 到 items[] 里的这些字段。projection 那头
# 我们用 rename_cascade_* SQL UPDATE 处理,**不走** per-row upsert,
# 避免 10k tx 的 rename 场景跑 10k 次 ON CONFLICT DO UPDATE。
_TX_CASCADE_FIELDS = frozenset({
    "accountName", "fromAccountName", "toAccountName",
    "categoryName",
    "tags",
})


def _tx_diff_only_cascade(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    """prev/next 两个 tx dict 是否**只**在 cascade 字段上有差异。
    是 = 整行 projection upsert 可跳过,rename_cascade_* SQL 批量搞定。"""
    keys = set(prev.keys()) | set(nxt.keys())
    changed_any_cascade = False
    for k in keys:
        pv = prev.get(k)
        nv = nxt.get(k)
        if pv == nv:
            continue
        if k in _TX_CASCADE_FIELDS:
            changed_any_cascade = True
            continue
        return False
    return changed_any_cascade


def _collect_renames(
    prev_list: list[dict[str, Any]],
    next_list: list[dict[str, Any]],
) -> list[tuple[str, str, str, str | None]]:
    """返回 (sync_id, old_name, new_name, new_kind_optional) 列表。
    只取 name 真正改了的(既非新增也非删除),用来批量走 rename_cascade_*。"""
    prev_map = {e["syncId"]: e for e in prev_list if e.get("syncId")}
    out: list[tuple[str, str, str, str | None]] = []
    for e in next_list:
        sync_id = e.get("syncId")
        if not sync_id or sync_id not in prev_map:
            continue
        prev = prev_map[sync_id]
        old = (prev.get("name") or "").strip()
        new = (e.get("name") or "").strip()
        if old and new and old != new:
            out.append((sync_id, old, new, (e.get("kind") or "").strip() or None))
    return out


def _diff_entity_list(
    db: Session,
    ledger: Ledger,
    current_user: User,
    device_id: str,
    now: datetime,
    prev_list: list[dict[str, Any]],
    next_list: list[dict[str, Any]],
    entity_type: str,
    emitted_ids: list[int],
    cascade_covered: bool = False,
) -> None:
    """Emit per-entity SyncChange rows + 同事务 projection 写入。

    性能关键点:**cascade-only tx 改动走 bulk insert**(一次 executemany 把 N 条
    SyncChange 塞进去),不 per-row flush。rename_cascade_* SQL 已经刷了 projection,
    所以这些 tx 只需要一条 SyncChange 行给 mobile pull 用,不需要 change_id 回读。

    10k tx 的标签改名:从 10k 次 INSERT + flush (~800ms) 降到 1 条 executemany (~50ms)。
    """
    prev_map = {e["syncId"]: e for e in prev_list if "syncId" in e}
    next_map = {e["syncId"]: e for e in next_list if "syncId" in e}
    is_user_global = entity_type in USER_GLOBAL_ENTITY_TYPES

    # bulk 队列:(entity_type, "upsert"/"delete", sync_id, payload_json)
    # 只收 cascade-only tx —— 它们不需要 source_change_id 回读
    bulk_upsert_rows: list[dict[str, Any]] = []

    # SyncChange row 公共字段构造。user-global 不挂 ledger,user_id 是真请求方。
    def _make_change_row(action: str, sync_id: str, payload: Any) -> SyncChange:
        if is_user_global:
            return SyncChange(
                user_id=current_user.id,
                ledger_id=None,
                scope="user",
                entity_type=entity_type,
                entity_sync_id=sync_id,
                action=action,
                payload_json=payload,
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
        return SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            scope="ledger",
            entity_type=entity_type,
            entity_sync_id=sync_id,
            action=action,
            payload_json=payload,
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )

    for sync_id, entity in next_map.items():
        prev_entity = prev_map.get(sync_id)
        if prev_entity is None or entity != prev_entity:
            is_cascade_only = (
                entity_type == "transaction"
                and cascade_covered
                and prev_entity is not None
                and _tx_diff_only_cascade(prev_entity, entity)
            )
            if is_cascade_only:
                # tx 是 ledger-scope,bulk 路径下直接写 ledger 字段
                bulk_upsert_rows.append({
                    "user_id": ledger.user_id,
                    "ledger_id": ledger.id,
                    "scope": "ledger",
                    "entity_type": entity_type,
                    "entity_sync_id": sync_id,
                    "action": "upsert",
                    "payload_json": entity,
                    "updated_at": now,
                    "updated_by_device_id": device_id,
                    "updated_by_user_id": current_user.id,
                })
                continue
            # 普通路径:insert + flush 取 change_id,再走 projection upsert
            change_row = _make_change_row("upsert", sync_id, entity)
            db.add(change_row)
            db.flush()
            emitted_ids.append(change_row.change_id)
            if is_user_global:
                fn = _USER_PROJECTION_UPSERTERS.get(entity_type)
                if fn is not None:
                    fn(
                        db,
                        user_id=current_user.id,
                        source_change_id=change_row.change_id,
                        payload=entity,
                    )
            else:
                fn = _LEDGER_PROJECTION_UPSERTERS.get(entity_type)
                if fn is not None:
                    fn(
                        db,
                        ledger_id=ledger.id,
                        user_id=ledger.user_id,
                        source_change_id=change_row.change_id,
                        payload=entity,
                    )

    for sync_id in prev_map:
        if sync_id not in next_map:
            # 删 tx / category 前先收集附件 fileId(tx 从 attachments_json,
            # category 从 icon_cloud_file_id + 子分类图标)。删完 projection
            # 行后调 gc_orphan_attachments:被共享引用的 blob 保留,完全孤立
            # 的 DELETE attachment_files + unlink 物理文件。
            gc_file_ids: set[str] = set()
            if entity_type == "transaction":
                gc_file_ids = projection.collect_tx_attachment_fileids(
                    db, ledger_id=ledger.id, sync_id=sync_id,
                )
            elif entity_type == "category":
                gc_file_ids = projection.collect_category_icon_fileids(
                    db, user_id=current_user.id, sync_id=sync_id,
                )

            change_row = _make_change_row("delete", sync_id, {})
            db.add(change_row)
            db.flush()
            emitted_ids.append(change_row.change_id)
            if is_user_global:
                fn = _USER_PROJECTION_DELETERS.get(entity_type)
                if fn is not None:
                    fn(db, user_id=current_user.id, sync_id=sync_id)
            else:
                fn = _LEDGER_PROJECTION_DELETERS.get(entity_type)
                if fn is not None:
                    fn(db, ledger_id=ledger.id, sync_id=sync_id)
            if gc_file_ids:
                projection.gc_orphan_attachments(
                    db, user_id=current_user.id, file_ids=gc_file_ids,
                )

    # Bulk flush cascade-only rows
    if bulk_upsert_rows:
        from sqlalchemy import insert as sa_insert
        db.execute(sa_insert(SyncChange), bulk_upsert_rows)
        # 取新插入的最大 change_id 作 emitted_ids(给 response.new_change_id)
        new_max = db.scalar(
            select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger.id)
        )
        if new_max:
            emitted_ids.append(int(new_max))


def _emit_entity_diffs(
    db: Session,
    *,
    ledger: Ledger,
    current_user: User,
    device_id: str,
    prev: dict[str, Any] | None,
    next_snapshot: dict[str, Any],
    now: datetime,
) -> list[int]:
    """Diff prev/next snapshots and emit individual SyncChange rows for each changed entity.

    顺序很重要：先 account / category / tag（被引用方），最后 transaction（引用
    方）。mobile 在 _pull 里按 change_id ASC 逐条 apply，若 tx change 的 change_id
    比它引用的 category change 还小，`_resolveCategoryId` 在 category 更新前就
    查老名字 → 查不到 → tx.categoryId = null。典型表现：web 改分类名后 mobile
    上的相关交易分类变空。

    Projection 优化:若本次修改里 account/category/tag 有 rename,先发一次
    rename_cascade_* (SQL UPDATE,O(1) 条语句,不受 tx 数影响),然后 tx diff 时
    对"仅 cascade 字段改变"的 tx 行跳过 per-row projection upsert。
    10k tx 的 category rename 从 10k 次 ON CONFLICT 降到 1 条 UPDATE + N 个
    SyncChange 插入。
    """
    prev = prev or {}

    account_renames = _collect_renames(prev.get("accounts") or [], next_snapshot.get("accounts") or [])
    category_renames = _collect_renames(prev.get("categories") or [], next_snapshot.get("categories") or [])
    tag_renames = _collect_renames(prev.get("tags") or [], next_snapshot.get("tags") or [])
    any_rename = bool(account_renames or category_renames or tag_renames)

    emitted_ids: list[int] = []
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("accounts") or [], next_snapshot.get("accounts") or [],
                      "account", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("categories") or [], next_snapshot.get("categories") or [],
                      "category", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("tags") or [], next_snapshot.get("tags") or [],
                      "tag", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("cardRewardRules") or [], next_snapshot.get("cardRewardRules") or [],
                      "card_reward_rule", emitted_ids)

    # Rename cascade via SQL batch,放在 tx diff 之前 —— 保证 tx diff 跳过的
    # cascade 行已经被刷新过。user-global 重构后 rename_cascade_* 按 user_id
    # 跨该用户所有 ledger 刷 read_tx_projection。
    for sync_id, _old, new_name, _kind in account_renames:
        projection.rename_cascade_account(
            db, user_id=current_user.id, account_sync_id=sync_id, new_name=new_name,
        )
    for sync_id, _old, new_name, new_kind in category_renames:
        projection.rename_cascade_category(
            db, user_id=current_user.id, category_sync_id=sync_id,
            new_name=new_name, new_kind=new_kind,
        )
    for sync_id, old, new, _ in tag_renames:
        projection.rename_cascade_tag(
            db, user_id=current_user.id, tag_sync_id=sync_id,
            old_name=old, new_name=new,
        )

    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("items") or [], next_snapshot.get("items") or [],
                      "transaction", emitted_ids, cascade_covered=any_rename)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("budgets") or [], next_snapshot.get("budgets") or [],
                      "budget", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("recurringRules") or [], next_snapshot.get("recurringRules") or [],
                      "recurring_rule", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("installmentPlans") or [], next_snapshot.get("installmentPlans") or [],
                      "installment_plan", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("installmentPeriods") or [], next_snapshot.get("installmentPeriods") or [],
                      "installment_period", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("debts") or [], next_snapshot.get("debts") or [],
                      "debt", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("projects") or [], next_snapshot.get("projects") or [],
                      "project", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("projectCategoryBudgets") or [], next_snapshot.get("projectCategoryBudgets") or [],
                      "project_category_budget", emitted_ids)
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("txTemplates") or [], next_snapshot.get("txTemplates") or [],
                      "tx_template", emitted_ids)
    # 股票交易明細(2026-09-28)引用 transaction(txId)+ account,排在兩者之後。
    _diff_entity_list(db, ledger, current_user, device_id, now,
                      prev.get("stockTrades") or [], next_snapshot.get("stockTrades") or [],
                      "stock_trade", emitted_ids)
    logger.info("_emit_entity_diffs: emitted %d entity changes for ledger %s", len(emitted_ids), ledger.external_id)
    return emitted_ids


def _load_ledger_for_write(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    roles: set[str],
) -> tuple[Ledger, str]:
    """共享账本 Phase 1:role check 通过 ledger_access 自动生效。返 (ledger, role)。"""
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
        roles=roles,
    )
    if row is not None:
        # issue #31(B2):软删账本不可写。否则一次写入就"复活"它的 projection,
        # 产生 web 账本列表看不见、tag / 跨账本视图里却查得到、又没 UI 入口去删
        # 的幽灵交易。软删后无法用同 external_id 重建(create_ledger 命中既有行
        # 直接 409),所以这里跟 read 端 _is_ledger_deleted 同口径直接 404,不会
        # 误伤任何合法重建流程。late import 防循环。
        from ..read._shared import _is_ledger_deleted

        ledger = row[0]
        if _is_ledger_deleted(db, ledger_id=ledger.id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found"
            )
        return row
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ledger not found")


def _latest_snapshot_change(db: Session, ledger_id: str) -> SyncChange | None:
    return db.scalar(
        select(SyncChange)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.action == "upsert",
        )
        .order_by(SyncChange.change_id.desc())
    )


def _parse_snapshot(change: SyncChange | None) -> dict:
    if change is None:
        return ensure_snapshot_v2({})
    payload = change.payload_json
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str) or not content.strip():
        return ensure_snapshot_v2({})
    try:
        snapshot = json.loads(content)
    except json.JSONDecodeError:
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return ensure_snapshot_v2(snapshot)


def _hash_request(method: str, path: str, payload: dict) -> str:
    raw = json.dumps(
        {"method": method, "path": path, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _purge_expired_idempotency(db: Session) -> None:
    now = _utcnow()
    expired = db.scalars(
        select(SyncPushIdempotency).where(SyncPushIdempotency.expires_at < now).limit(200)
    ).all()
    for row in expired:
        db.delete(row)
    if expired:
        db.flush()


def _load_idempotent_response(
    db: Session,
    *,
    user_id: str,
    device_id: str,
    idempotency_key: str,
    request_hash: str,
) -> WriteCommitMeta | None:
    row = db.scalar(
        select(SyncPushIdempotency).where(
            SyncPushIdempotency.user_id == user_id,
            SyncPushIdempotency.device_id == device_id,
            SyncPushIdempotency.idempotency_key == idempotency_key,
        )
    )
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key reused with different payload",
        )
    payload = dict(row.response_json)
    payload["idempotency_replayed"] = True
    return WriteCommitMeta.model_validate(payload)


def _assert_refund_target_not_already_refunded(
    db: Session,
    *,
    ledger_id: str,
    refund_of_id: str,
    exclude_tx_id: str | None = None,
) -> None:
    """退款(§2.6/§2.12.3):`refund_of_id` 指向的那笔交易一旦已经被某笔退款
    交易引用过,就不能再建第二笔退款 —— 跟 installment_plans.py 单期退款的
    "period already refunded" 防呆同语意(见该文件 refund_installment_period_ep),
    这里补的是普通交易退款路径(POST/PATCH .../transactions)一直缺失的那道
    检查(c320cf5 commit message 提过但当时只实作了分期那一半)。

    exclude_tx_id:PATCH 场景下当前这笔 tx 本身可能就是已经引用了这个
    refund_of_id 的那一行(用户只是编辑其它字段,refund_of_id 原样带回),
    要排除自己,否则每次保存都会命中。

    顺带挡「退款的退款」:`refund_of_id` 指向的那笔交易如果自己也是一笔
    退款(它的 refund_of_sync_id 非空),不允许对它再建退款 —— 不然会形成
    链式退款,跟 Moze 原设计不符,web UI 也已经不出这个入口
    (`TransactionDetailDialog.tsx` 的 `isRefundTx`),这里是 API 层兜底,
    防 mobile / 直接调 API 绕过。
    """
    query = select(ReadTxProjection.sync_id).where(
        ReadTxProjection.ledger_id == ledger_id,
        ReadTxProjection.refund_of_sync_id == refund_of_id,
    )
    if exclude_tx_id is not None:
        query = query.where(ReadTxProjection.sync_id != exclude_tx_id)
    existing = db.scalar(query.limit(1))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="transaction has already been refunded",
        )

    target_refund_of = db.scalar(
        select(ReadTxProjection.refund_of_sync_id).where(
            ReadTxProjection.ledger_id == ledger_id,
            ReadTxProjection.sync_id == refund_of_id,
        )
    )
    if target_refund_of is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot create a refund of a refund transaction",
        )


def _assert_debt_exists(db: Session, *, ledger_id: str, debt_id: str) -> ReadDebtProjection:
    """借還款追蹤(§2.5 Phase 3):`debt_id` 必须指向该账本下真实存在的一笔
    欠款,否则一笔"还款交易"会挂一个查无对应债务的孤儿反查字段,读路径
    `list_debts` 反查 sum 永远算不到它。不像退款那样查重(债务允许多笔部分
    还款,不是"只能被引用一次"),这里只做存在性检查。返回 debt row(而非仅
    `None`)是给需求 #6 的自动分类/备注用——呼叫端不需要就直接忽略回傳值。"""
    debt = db.scalar(
        select(ReadDebtProjection).where(
            ReadDebtProjection.ledger_id == ledger_id,
            ReadDebtProjection.sync_id == debt_id,
        ).limit(1)
    )
    if debt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="debt not found",
        )
    return debt


def _cascade_delete_orphaned_origin_debt(
    db: Session,
    *,
    ledger: Ledger,
    tx_id: str,
    now: datetime,
    device_id: str,
    current_user: User,
) -> None:
    """借還款追蹤:刪除一筆交易時,若它是某筆欠款的起點交易
    (`origin_tx_sync_id`)且該欠款還沒有任何還款交易,這筆欠款已經沒有任何
    交易佐證(起點沒了、也沒還款),欠款管理頁面留著只會是找不到起點的空殼
    記錄,一併刪除。跟 debts.py::_assert_debt_has_no_repayments(使用者主動
    刪欠款的守衛)是同一條判斷邏輯,這裡只是反過來在刪交易時自動觸發。
    App 端對應改動見 LocalRepository.deleteTransaction。"""
    debt = db.scalar(
        select(ReadDebtProjection).where(
            ReadDebtProjection.ledger_id == ledger.id,
            ReadDebtProjection.origin_tx_sync_id == tx_id,
        )
    )
    if debt is None:
        return
    has_repayment = db.scalar(
        select(ReadTxProjection.sync_id).where(
            ReadTxProjection.ledger_id == ledger.id,
            ReadTxProjection.debt_sync_id == debt.sync_id,
        ).limit(1)
    )
    if has_repayment is not None:
        return
    db.add(
        SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="debt",
            entity_sync_id=debt.sync_id,
            action="delete",
            payload_json={},
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )
    )
    db.flush()
    projection.delete_debt(db, ledger_id=ledger.id, sync_id=debt.sync_id)


def _cascade_delete_linked_stock_trades(
    db: Session,
    *,
    ledger: Ledger,
    tx_id: str,
    now: datetime,
    device_id: str,
    current_user: User,
) -> None:
    """股票持股(2026-09-28,docs/STOCK_HOLDINGS_SD.md):刪一筆交易時,連帶刪
    掉 `tx_sync_id` 指向它的股票交易明細——買進/賣出/股利的現金流動沒了,
    股數卻還留著,持股就會跟帳戶餘額對不上。批量刪除路徑的同款邏輯在
    `snapshot_mutator.delete_transaction`;App 端對應改動見
    `LocalRepository.deleteTransaction`。"""
    trade_ids = db.scalars(
        select(ReadStockTradeProjection.sync_id).where(
            ReadStockTradeProjection.ledger_id == ledger.id,
            ReadStockTradeProjection.tx_sync_id == tx_id,
        )
    ).all()
    for trade_id in trade_ids:
        db.add(
            SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                scope="ledger",
                entity_type="stock_trade",
                entity_sync_id=trade_id,
                action="delete",
                payload_json={},
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
        )
        db.flush()
        projection.delete_stock_trade(db, ledger_id=ledger.id, sync_id=trade_id)


def _assert_project_exists(
    db: Session, *, ledger_id: str, project_id: str, tx_type: str | None,
) -> ReadProjectProjection:
    """專案(Phase 13,docs/PH13_PROJECT_SD.md §2.2):`project_id` 必须指向
    该账本下真实存在的一個專案,否則一筆交易會掛一個查無對應專案的孤兒反查
    欄位,讀路徑 `list_projects` 反查 SUM 永遠算不到它(比照 `_assert_debt_
    exists` 同款存在性檢查)。同時只支援 expense/income——transfer「轉去哪」
    沒有花在哪個專案的語意,adjustment 已經被 `_assert_valid_adjustment_tx`
    整體擋掉分類/關聯類欄位,這裡再擋一次 transfer 是因為 adjustment 檢查
    不會涵蓋 transfer 的組合。"""
    if tx_type not in ("expense", "income"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project_id can only be set on expense/income transactions",
        )
    project = db.scalar(
        select(ReadProjectProjection).where(
            ReadProjectProjection.ledger_id == ledger_id,
            ReadProjectProjection.sync_id == project_id,
        ).limit(1)
    )
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project not found",
        )
    return project


def _assert_reward_rules_valid(
    db: Session, *, user_id: str, account_id: str | None, reward_rule_ids: list[str],
) -> None:
    """信用卡紅利回饋(§2.9.5,2026-08-06 改版):`reward_rule_ids` 每個 id
    必須是使用者名下、掛在 `account_id` 這張信用卡上的真實規則 —— 使用者
    手動勾選要走哪幾條,系統這裡只做存在性 + 歸屬校驗,不做 category/金額
    匹配(那些交给 services.card_rewards 在計算當期回饋時判斷)。"""
    if not reward_rule_ids:
        return
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reward_rule_ids requires an account_id",
        )
    rows = db.execute(
        select(
            ReadCardRewardRuleProjection.sync_id,
            ReadCardRewardRuleProjection.account_sync_id,
        ).where(
            ReadCardRewardRuleProjection.user_id == user_id,
            ReadCardRewardRuleProjection.sync_id.in_(reward_rule_ids),
        )
    ).all()
    owner_by_id = {row.sync_id: row.account_sync_id for row in rows}
    for rule_id in reward_rule_ids:
        owner_account_id = owner_by_id.get(rule_id)
        if owner_account_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"card reward rule not found: {rule_id}",
            )
        if owner_account_id != account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"card reward rule does not belong to this account: {rule_id}",
            )


def _drop_reward_rules_orphaned_by_account_change(
    db: Session, *, user_id: str, account_id: str | None, reward_rule_ids: list[str],
) -> list[str]:
    """信用卡紅利回饋(2026-08-16 補,週期性收支單筆編輯帳戶更換 400 的根因
    修正):帳戶被換掉、但這次請求沒機會重新勾選回饋規則時(例如
    `WriteRecurringOccurrenceUpdateRequest` 這個窄欄位 schema 本來就沒有
    `reward_rule_ids` 欄位;一般交易編輯表單換帳戶時前端目前也不會連動清空
    已勾選項),舊帳戶底下的規則歸屬對不上新帳戶——這不是使用者故意要送
    無效組合,是換帳戶這個動作本身讓舊選擇失去意義,靜默過濾掉即可,不必
    整個操作 400 擋下。只丟「查得到但屬於別的帳戶」這種,規則 id 本身不存在
    (打錯字/已被刪除)的維持原樣讓呼叫方後續的 `_assert_reward_rules_valid`
    照樣硬擋,不吞掉真的資料錯誤。
    """
    if not reward_rule_ids or not account_id:
        return reward_rule_ids
    rows = db.execute(
        select(
            ReadCardRewardRuleProjection.sync_id,
            ReadCardRewardRuleProjection.account_sync_id,
        ).where(
            ReadCardRewardRuleProjection.user_id == user_id,
            ReadCardRewardRuleProjection.sync_id.in_(reward_rule_ids),
        )
    ).all()
    owner_by_id = {row.sync_id: row.account_sync_id for row in rows}
    return [
        rid for rid in reward_rule_ids
        if rid not in owner_by_id or owner_by_id[rid] == account_id
    ]


def _assert_valid_adjustment_tx(
    *,
    tx_type: str | None,
    account_id: str | None,
    category_id: str | None,
    category_name: str | None,
    from_account_id: str | None,
    to_account_id: str | None,
    splits: Any,
    refund_of_id: str | None,
    debt_id: str | None,
    project_id: str | None,
    reward_rule_ids: Any,
) -> None:
    """餘額調整(§2.10 Phase 5):`tx_type == "adjustment"` 只描述「這個帳戶
    餘額被直接修正了多少」,語意上不該疊加分類/轉帳對象/拆帳/退款/欠款/
    專案/紅利回饋這些屬於一般收支/轉帳交易的概念——校验成功路径(語意化端點
    `POST .../balance-adjustment`)只会产生完全干净的 payload,这里是给
    直接调用 `POST/PATCH .../transactions` 手动传 `tx_type=adjustment` 的
    调用方(未来 mobile / API 直连)兜底,create/update 两条 fast path 都要
    挂。"""
    if tx_type != "adjustment":
        return
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions require account_id",
        )
    if category_id or category_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot have a category",
        )
    if from_account_id or to_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot set from_account_id/to_account_id",
        )
    if splits:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot be split",
        )
    if refund_of_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot be a refund",
        )
    if debt_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot be linked to a debt",
        )
    if project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot be linked to a project",
        )
    if reward_rule_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="adjustment transactions cannot have reward_rule_ids",
        )


# 拆帳(§2.4 MOZE_FEATURE_GAP_SD.md Phase 2)。
_SPLIT_MIN_COUNT = 2
_SPLIT_AMOUNT_EPSILON = 0.01


def _compute_fee_discount_amount(
    tx_type: str, base_amount: float, fee_amount: float, discount_amount: float,
) -> float:
    """手續費/折扣的核心公式,`_normalize_fee_discount_amount`(交易)跟
    `_normalize_recurring_rule_fee_discount`(週期性收支規則)共用同一份,
    避免兩處各自維護一份容易改一邊漏一邊。

    公式(expense/income 方向不同,跟餘額增減直覺一致):
      expense: amount = base_amount + fee_amount − discount_amount
      income:  amount = base_amount − fee_amount + discount_amount
    呼叫端要先擋掉 transfer/adjustment(沒有明確的方向語意)。
    """
    if tx_type == "expense":
        return base_amount + fee_amount - discount_amount
    return base_amount - fee_amount + discount_amount


def _normalize_fee_discount_amount(
    *, db: Session, ledger_id: str, tx_id: str | None, payload: dict,
) -> None:
    """手續費/折扣(2026-08 使用者需求,比照 Moze record/introduction):
    `base_amount`(使用者輸入的原始金額,信用卡回饋計算的權威基準,見
    services/card_rewards.py::_reward_base_amount)/`fee_amount`/
    `discount_amount` 任一出現在 payload 時,server 端用這三者 + tx_type
    重新算出權威的 `amount`(實際入帳、驅動餘額/統計的總額),直接覆蓋掉
    payload 裡任何前端算好的 `amount`——避免前端浮點誤差或漏算讓 `amount`
    跟三個分量互相對不上,寧可 server 端單一權威來源重算一次。

    三個新欄位都不在 payload 裡時整個函式 no-op,不影響任何既有(沒用這個
    功能的)交易寫入路徑。PATCH 缺鍵的分量 fallback 現有 DB 值(partial
    update 惯例);create(tx_id=None)缺鍵 fallback 0。

    公式(expense/income 方向不同,跟餘額增減直覺一致):
      expense: amount = base_amount + fee_amount − discount_amount
      income:  amount = base_amount − fee_amount + discount_amount
    adjustment 没有明確的方向語意,不支援,帶了任一新欄位直接 400。

    transfer(2026-08-29 轉帳手續費/折損,design doc
    docs/superpowers/specs/2026-08-29-transfer-fee-discount-design.md):
    只驗證 fee_amount/discount_amount(若有帶)皆為 ≥0 的數字,**不**重算
    `amount`——transfer 的 `amount`/`to_amount` 維持客戶端算好的「本體」
    語意,fee_amount/discount_amount 只是疊加在餘額計算上的獨立 delta(見
    App 端 `LocalAccountRepository._transferOutEffect`/
    `_transferInEffect`,兩邊必須用同一套公式,不要各自發明)。
    `base_amount` 對 transfer 不使用,一律留 None(那是 expense/income 專用
    的重算基準)。
    """
    _fd_keys = ("base_amount", "fee_amount", "discount_amount")
    if tx_id is None:
        # create(POST):`req.model_dump(mode="json")` 沒用 exclude_unset,
        # 這三個 key 永遠存在(值為 None)——用「key 是否出現」判斷會對每一筆
        # 新建交易(含 transfer/adjustment)誤觸發,必須看有沒有任何一個真的
        # 帶了非 None 的值。
        if not any(payload.get(k) is not None for k in _fd_keys):
            return
    else:
        # update(PATCH):`exclude_unset=True`,key 出現即代表使用者這次有
        # 動這個分量(包含顯式傳 null 清空),要用「key 是否出現」判斷,不能
        # 只看值是否為 None,否則「PATCH {"discount_amount": null}」這種
        # 純清空操作(其它兩個分量都沒出現)會被誤判成「沒有動這三個欄位」
        # 而整個 no-op,清不掉。
        if not any(k in payload for k in _fd_keys):
            return

    existing = None
    if tx_id is not None:
        existing = db.execute(
            select(
                ReadTxProjection.tx_type,
                ReadTxProjection.amount,
                ReadTxProjection.base_amount,
                ReadTxProjection.fee_amount,
                ReadTxProjection.discount_amount,
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.sync_id == tx_id,
            )
        ).first()

    tx_type = payload.get("tx_type") if "tx_type" in payload else (existing.tx_type if existing else None)
    tx_type = tx_type or "expense"
    if tx_type not in {"expense", "income", "transfer"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fee/discount amounts are only supported for expense/income/transfer transactions",
        )

    if tx_type == "transfer":
        fee_amount = payload.get("fee_amount") if "fee_amount" in payload else (
            existing.fee_amount if existing else None
        )
        discount_amount = payload.get("discount_amount") if "discount_amount" in payload else (
            existing.discount_amount if existing else None
        )
        if (fee_amount is not None and float(fee_amount) < 0) or (
            discount_amount is not None and float(discount_amount) < 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="fee/discount amount must not be negative",
            )
        # base_amount 對 transfer 不使用;理論上前端不該帶,這裡防禦性地
        # 丟掉而不是報錯,避免跟 expense/income 共用同一個 payload 組裝路径
        # 时误挡合法请求。不重算 amount——維持客戶端算好的值。
        if "base_amount" in payload and payload["base_amount"] is not None:
            payload["base_amount"] = None
        return

    base_amount = payload.get("base_amount") if "base_amount" in payload else (
        existing.base_amount if existing else None
    )
    if base_amount is None:
        base_amount = payload.get("amount") if "amount" in payload else (
            existing.amount if existing else None
        )
    fee_amount = payload.get("fee_amount") if "fee_amount" in payload else (
        existing.fee_amount if existing else None
    )
    discount_amount = payload.get("discount_amount") if "discount_amount" in payload else (
        existing.discount_amount if existing else None
    )
    base_amount = float(base_amount or 0)
    fee_amount = float(fee_amount or 0)
    discount_amount = float(discount_amount or 0)

    if base_amount < 0 or fee_amount < 0 or discount_amount < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fee/discount/base amount must not be negative",
        )

    amount = _compute_fee_discount_amount(tx_type, base_amount, fee_amount, discount_amount)

    payload["amount"] = round(amount, 2)
    # 顯式傳 null(使用者主動清掉該分量)要保留 None,讓下游
    # snapshot_mutator.update_transaction 的清空邏輯生效,不能被這裡「重新
    # 計算」的動作覆蓋回數值,否則 PATCH {"discount_amount": null} 清不掉。
    if not ("base_amount" in payload and payload["base_amount"] is None):
        payload["base_amount"] = base_amount
    if not ("fee_amount" in payload and payload["fee_amount"] is None):
        payload["fee_amount"] = fee_amount
    if not ("discount_amount" in payload and payload["discount_amount"] is None):
        payload["discount_amount"] = discount_amount


def _normalize_recurring_rule_fee_discount(
    *, db: Session, ledger_id: str, rule_id: str | None, payload: dict,
) -> None:
    """`_normalize_fee_discount_amount` 的週期性收支規則版本(2026-08 使用者
    回饋:規則本身現在也儲存 base_amount/fee_amount/discount_amount,讓每一
    期自動產生的 occurrence 都能繼承,見 services/recurring_materializer.py)。

    結構跟交易版本完全一致,只是查 `ReadRecurringRuleProjection` 而不是
    `ReadTxProjection`;`rule_id=None` 代表建立(create),否則是更新
    (update / update-from)。"""
    _fd_keys = ("base_amount", "fee_amount", "discount_amount")
    if rule_id is None:
        if not any(payload.get(k) is not None for k in _fd_keys):
            return
    else:
        if not any(k in payload for k in _fd_keys):
            return

    existing = None
    if rule_id is not None:
        existing = db.execute(
            select(
                ReadRecurringRuleProjection.tx_type,
                ReadRecurringRuleProjection.amount,
                ReadRecurringRuleProjection.base_amount,
                ReadRecurringRuleProjection.fee_amount,
                ReadRecurringRuleProjection.discount_amount,
            ).where(
                ReadRecurringRuleProjection.ledger_id == ledger_id,
                ReadRecurringRuleProjection.sync_id == rule_id,
            )
        ).first()

    tx_type = payload.get("tx_type") if "tx_type" in payload else (existing.tx_type if existing else None)
    tx_type = tx_type or "expense"
    if tx_type not in {"expense", "income"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fee/discount amounts are only supported for expense/income recurring rules",
        )

    base_amount = payload.get("base_amount") if "base_amount" in payload else (
        existing.base_amount if existing else None
    )
    if base_amount is None:
        base_amount = payload.get("amount") if "amount" in payload else (
            existing.amount if existing else None
        )
    fee_amount = payload.get("fee_amount") if "fee_amount" in payload else (
        existing.fee_amount if existing else None
    )
    discount_amount = payload.get("discount_amount") if "discount_amount" in payload else (
        existing.discount_amount if existing else None
    )
    base_amount = float(base_amount or 0)
    fee_amount = float(fee_amount or 0)
    discount_amount = float(discount_amount or 0)

    if base_amount < 0 or fee_amount < 0 or discount_amount < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="fee/discount/base amount must not be negative",
        )

    amount = _compute_fee_discount_amount(tx_type, base_amount, fee_amount, discount_amount)

    payload["amount"] = round(amount, 2)
    if not ("base_amount" in payload and payload["base_amount"] is None):
        payload["base_amount"] = base_amount
    if not ("fee_amount" in payload and payload["fee_amount"] is None):
        payload["fee_amount"] = fee_amount
    if not ("discount_amount" in payload and payload["discount_amount"] is None):
        payload["discount_amount"] = discount_amount


def _validate_tx_splits(
    *, tx_type: str | None, amount: float | None, splits: list[dict],
) -> None:
    """拆帳金额/张数/類型校验,create 跟 update(effective 值算好后)共用。
    splits 是 request payload 的原始 dict 列表(snake_case:category_id/
    amount/note)。"""
    if tx_type not in {"expense", "income"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only expense/income transactions can be split across categories",
        )
    if len(splits) < _SPLIT_MIN_COUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"a split transaction needs at least {_SPLIT_MIN_COUNT} category entries",
        )
    total = 0.0
    for entry in splits:
        category_id = entry.get("category_id") or entry.get("categoryId")
        if not category_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="each split entry must have a category",
            )
        try:
            split_amount = float(entry.get("amount") or 0)
        except (TypeError, ValueError):
            split_amount = 0.0
        if split_amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="each split amount must be greater than zero",
            )
        total += split_amount
    if amount is None or abs(total - float(amount)) > _SPLIT_AMOUNT_EPSILON:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="split amounts must add up to the transaction amount",
        )


async def _commit_create_tx_fast(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    request_payload: dict,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    mutate_payload: dict,
) -> WriteCommitMeta:
    """Fast path:新建单笔 tx,跳过全量 snapshot build(issue #31 A1b)。

    create 不需要 diff 全表 —— 新行就是唯一变更。snapshot_mutator.create_transaction
    不依赖现有 items / tx_index,所以用空的最小 snapshot 跑它产出规范化 item,直接
    emit 一条 transaction SyncChange + projection.upsert_tx。语义跟 _commit_write
    (build→mutate→_emit_entity_diffs)对单笔 create 的结果完全一致,但把成本从
    O(账本交易数) 降到 O(1)。

    DB 工作整段丢到 threadpool(issue #31 A2),不阻塞 event loop;WS 广播在
    线程返回后于 loop 上做(仅一次小的 member 查询 + 推送)。
    """
    from ...snapshot_mutator import create_transaction as _mutate_create_tx

    def _core() -> tuple[WriteCommitMeta, bool]:
        """同步 DB 核心,返回 (response, did_replay)。在 worker thread 里跑。"""
        lock_ledger_for_materialize(db, ledger.id)
        now = _utcnow()
        splits_payload = mutate_payload.get("splits")
        refund_of_id = mutate_payload.get("refund_of_id")
        _assert_valid_adjustment_tx(
            tx_type=mutate_payload.get("tx_type"),
            account_id=mutate_payload.get("account_id"),
            category_id=mutate_payload.get("category_id"),
            category_name=mutate_payload.get("category_name"),
            from_account_id=mutate_payload.get("from_account_id"),
            to_account_id=mutate_payload.get("to_account_id"),
            splits=splits_payload,
            refund_of_id=refund_of_id,
            debt_id=mutate_payload.get("debt_id"),
            project_id=mutate_payload.get("project_id"),
            reward_rule_ids=mutate_payload.get("reward_rule_ids"),
        )
        if splits_payload:
            if refund_of_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="a refund transaction cannot itself be split",
                )
            _validate_tx_splits(
                tx_type=mutate_payload.get("tx_type"),
                amount=mutate_payload.get("amount"),
                splits=splits_payload,
            )
        if refund_of_id:
            _assert_refund_target_not_already_refunded(
                db, ledger_id=ledger.id, refund_of_id=str(refund_of_id),
            )
            # 退款分类自动归类(2026-08-04 使用者反馈):没显式选分类时,
            # 自建/复用「退款」分类(`ensure_refund_category`,跟回饋金分类
            # 同一套「sync push 等价」旁路模式),不强迫用户手动挑一个跟这笔
            # 交易毫不相关的既有分类凑数;用户如果自己选了分类,原样尊重。
            if not mutate_payload.get("category_id") and not mutate_payload.get("category_name"):
                from ...services import card_rewards as _card_rewards
                refund_tx_type = mutate_payload.get("tx_type")
                if refund_tx_type in ("income", "expense"):
                    mutate_payload["category_id"] = _card_rewards.ensure_refund_category(
                        db, user_id=ledger.user_id, kind=refund_tx_type,
                    )
                    mutate_payload["category_name"] = _card_rewards.REFUND_CATEGORY_NAME
                    mutate_payload["category_kind"] = refund_tx_type
        debt_id = mutate_payload.get("debt_id")
        if debt_id:
            debt_row = _assert_debt_exists(db, ledger_id=ledger.id, debt_id=str(debt_id))
            # 欠還款交易自動帶分類/備註(使用者反饋 #6,2026-08):沒自己填
            # 分類/備註時,依 debt.direction 自動歸類(同退款分類的既有
            # 「sync push 等价」旁路模式),使用者自己填的內容不覆蓋。
            from ...services import card_rewards as _card_rewards
            if not mutate_payload.get("category_id") and not mutate_payload.get("category_name"):
                mutate_payload["category_id"] = _card_rewards.ensure_debt_category(
                    db, user_id=ledger.user_id, direction=debt_row.direction,
                )
                mutate_payload["category_name"] = _card_rewards.debt_category_name(debt_row.direction)
                mutate_payload["category_kind"] = _card_rewards.debt_category_kind(debt_row.direction)
            if not mutate_payload.get("note"):
                mutate_payload["note"] = _card_rewards.debt_default_note(
                    direction=debt_row.direction,
                    counterparty_name=debt_row.counterparty_name,
                )
        project_id = mutate_payload.get("project_id")
        if project_id:
            _assert_project_exists(
                db, ledger_id=ledger.id, project_id=str(project_id),
                tx_type=mutate_payload.get("tx_type"),
            )
        reward_rule_ids = mutate_payload.get("reward_rule_ids")
        if reward_rule_ids:
            _assert_reward_rules_valid(
                db, user_id=ledger.user_id, account_id=mutate_payload.get("account_id"),
                reward_rule_ids=[str(v) for v in reward_rule_ids],
            )
        # 空 snapshot 跑 mutator —— 只为复用其字段规范化 + actor 标记逻辑。
        _snap, tx_id = _mutate_create_tx({"items": [], "count": 0}, mutate_payload)
        new_item = _snap["items"][0]

        change_row = SyncChange(
            user_id=ledger.user_id,
            ledger_id=ledger.id,
            entity_type="transaction",
            entity_sync_id=tx_id,
            action="upsert",
            payload_json=new_item,
            updated_at=now,
            updated_by_device_id=device_id,
            updated_by_user_id=current_user.id,
        )
        db.add(change_row)
        db.flush()
        projection.upsert_tx(
            db,
            ledger_id=ledger.id,
            user_id=ledger.user_id,
            source_change_id=change_row.change_id,
            payload=new_item,
        )

        if refund_of_id:
            # 信用卡回饋沖銷(2026-08-04 使用者反馈):这笔退款如果冲抵了一笔
            # 已经逐笔结算入帐过回饋金的消费,补一笔沖銷交易,跟退款交易同一个
            # DB transaction 原子 commit——`_qualifying_transactions` 已经把
            # 被退款的交易排除在未来结算范围外(排程还没跑到的情况),这里补的
            # 是排程已经跑过、回饋已经入帐的情况,两者合起来保证退款前/退款后
            # 结算净效果一致。
            from ...services.card_reward_payout import reverse_card_reward_payouts_for_refund
            reverse_card_reward_payouts_for_refund(
                db, ledger_id=ledger.id, user_id=ledger.user_id,
                refunded_tx_id=str(refund_of_id), now=now,
            )

        db.add(
            AuditLog(
                user_id=current_user.id,
                ledger_id=ledger.id,
                action=audit_action,
                metadata_json={
                    "ledgerId": ledger.external_id,
                    "baseChangeId": base_change_id,
                    "newChangeId": change_row.change_id,
                    "entityId": tx_id,
                },
            )
        )

        response = WriteCommitMeta(
            ledger_id=ledger.external_id,
            base_change_id=base_change_id,
            new_change_id=change_row.change_id,
            server_timestamp=now,
            idempotency_replayed=False,
            entity_id=tx_id,
        )

        request_hash = _hash_request(request.method, request.url.path, request_payload)
        if idempotency_key:
            db.add(
                SyncPushIdempotency(
                    user_id=current_user.id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response_json=response.model_dump(mode="json"),
                    created_at=now,
                    expires_at=now + timedelta(hours=24),
                )
            )

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if idempotency_key:
                replay = _load_idempotent_response(
                    db,
                    user_id=current_user.id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay, True
            raise
        return response, False

    response, did_replay = await run_in_threadpool(_core)

    if not did_replay:
        from ...websocket_manager import broadcast_to_ledger

        await broadcast_to_ledger(
            db=db,
            ws_manager=request.app.state.ws_manager,
            ledger_id=ledger.id,
            payload={
                "type": "sync_change",
                "ledgerId": ledger.external_id,
                "serverCursor": response.new_change_id,
                "serverTimestamp": response.server_timestamp.isoformat(),
            },
        )
    logger.info(
        "write.commit.create_fast ledger=%s entity=%s change_id=%d device=%s user=%s replay=%s",
        ledger.external_id, response.entity_id, response.new_change_id,
        device_id, current_user.id, did_replay,
    )
    return response


async def _commit_write_fast_tx(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    request_payload: dict,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    tx_id: str,
    mutate_payload: dict,
    action: str,  # "upsert" | "delete"
) -> WriteCommitMeta:
    """Fast path:单 tx update/delete,跳过全 snapshot build。只 SELECT 目标 tx
    (1 条 query by PK)→ 合并 payload → 写 SyncChange + projection。~10-15ms。

    DB 工作整段丢 threadpool(issue #31 A2),不阻塞 event loop;广播在线程返回后做。
    """

    def _core() -> tuple[WriteCommitMeta, bool]:
        """同步 DB 核心,返回 (response, did_replay)。在 worker thread 里跑。"""
        lock_ledger_for_materialize(db, ledger.id)
        now = _utcnow()

        # 1. 读目标 tx from projection
        tx_row = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == ledger.id,
                ReadTxProjection.sync_id == tx_id,
            )
        )
        if tx_row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

        # 2. 把 projection row → dict(mutator 认的 snapshot item 格式)
        prev_item = _projection_row_to_tx_dict(tx_row)

        # 3. actor 权限检查(复用现有逻辑)
        from ...snapshot_mutator import _assert_actor_can_modify  # 延迟 import 避免循环
        try:
            _assert_actor_can_modify(prev_item, mutate_payload)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

        # 2026-08-03 使用者反饋 #4:分期產生的交易「雖然可以編輯,但應該要
        # 連動分期的總覽頁面,不能單獨修改」——`amount`/`happened_at`/
        # `account_id` 這三個欄位改了會讓 read_installment_period_projection
        # 的對應欄位(total_amount/due_at)或帳單歸屬悄悄跟這筆 tx 脫鉤(period
        # 表不会跟著动,信用卡帳單聚合也会算到错的帐户上),必须走專門的
        # installment 端點(單期編輯/rebalance-from/提前還本/提前結清)才會
        # 同時更新兩邊。note/分類/標籤等不影響 period 排程的欄位不受此限制。
        if (
            action == "upsert"
            and prev_item.get("installmentPlanId")
            and any(f in mutate_payload for f in ("amount", "happened_at", "account_id"))
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This transaction's amount, date, and account cannot be "
                    "edited directly because it was generated by an "
                    "installment plan. Use the installment plan's own actions "
                    "(edit this period, rebalance future periods, early "
                    "repayment, or payoff) instead."
                ),
            )

        if action == "delete" and prev_item.get("installmentPlanId"):
            # 分期付款(§2.12.1 Phase 1.5):这笔交易是某个分期计画生成的一期。
            # 这条快路径只删 read_tx_projection 单张表,不会清 read_
            # installment_period_projection 里指回它的 tx_sync_id,也不会走
            # SyncChange 通知别的设备该期已被移除 —— 直接放行会留下一条
            # tx_sync_id 指向已删除交易的孤儿 period 记录(MOZE_FEATURE_GAP_SD.md
            # §2.12.1 实测发现的"删除不联动"问题)。改期/结清/终止未来分期都
            # 已经有对应端点会正确地同时清理 period + 交易,这里改成引导用户
            # 去那边操作,而不是允许产生不一致状态。
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This transaction belongs to an installment plan. "
                    "Use the installment plan's own actions (early repayment, "
                    "payoff, stop future periods, or delete the plan) instead "
                    "of deleting the transaction directly."
                ),
            )

        if action == "delete":
            # 删 tx 前收集引用的 cloudFileId,删后 GC 孤立附件(物理 blob + 行)
            tx_file_ids = projection.collect_tx_attachment_fileids(
                db, ledger_id=ledger.id, sync_id=tx_id,
            )
            change_row = SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                entity_type="transaction",
                entity_sync_id=tx_id,
                action="delete",
                payload_json={},
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
            db.add(change_row)
            db.flush()
            projection.delete_tx(db, ledger_id=ledger.id, sync_id=tx_id)
            projection.gc_orphan_attachments(
                db, user_id=ledger.user_id, file_ids=tx_file_ids,
            )
            _cascade_delete_orphaned_origin_debt(
                db, ledger=ledger, tx_id=tx_id, now=now,
                device_id=device_id, current_user=current_user,
            )
            _cascade_delete_linked_stock_trades(
                db, ledger=ledger, tx_id=tx_id, now=now,
                device_id=device_id, current_user=current_user,
            )
        else:
            # 退款(§2.6):显式改 refund_of_id(非空)时才查重 —— 没传该 key
            # (exclude_unset)代表这次 PATCH 不动这个字段,不用重新校验;排除
            # 自己这行,否则编辑一笔已经是退款的交易(refund_of_id 原样带回)
            # 每次保存都会被自己命中。
            refund_of_id = mutate_payload.get("refund_of_id")
            if refund_of_id:
                _assert_refund_target_not_already_refunded(
                    db, ledger_id=ledger.id, refund_of_id=str(refund_of_id),
                    exclude_tx_id=tx_id,
                )
            # 借還款追蹤(§2.5 Phase 3):同款语义,显式改 debt_id(非空)时才
            # 校验存在性 —— exclude_unset 没传该 key 代表这次 PATCH 不动它。
            debt_id = mutate_payload.get("debt_id")
            if debt_id:
                _assert_debt_exists(db, ledger_id=ledger.id, debt_id=str(debt_id))
            # 專案(Phase 13):同款语义,显式改 project_id(非空)时才校验存在性
            # 与 tx_type 限制 —— exclude_unset 没传该 key 代表这次 PATCH 不动它。
            project_id = mutate_payload.get("project_id")
            if project_id:
                _assert_project_exists(
                    db, ledger_id=ledger.id, project_id=str(project_id),
                    tx_type=mutate_payload.get("tx_type") or prev_item.get("type"),
                )
            # Upsert:merge payload 到 prev_item
            from ...snapshot_mutator import update_transaction
            # 构造最小 snapshot 让 mutator 跑逻辑(只有 1 个 item)
            minimal_snap = {"items": [prev_item], "count": 1}
            minimal_snap = update_transaction(minimal_snap, tx_id, mutate_payload)
            new_item = minimal_snap["items"][0]

            # 餘額調整(§2.10 Phase 5):在 merge 后的最终状态上校验(同拆帳/
            # 紅利回饋同一理由)—— 单独改其它字段没带 tx_type 时,payload 里
            # 也不会出现 "tx_type" key,new_item["type"] 沿用旧值,校验对
            # 「维持是/不是 adjustment」两种情况都成立。
            _assert_valid_adjustment_tx(
                tx_type=new_item.get("type"),
                account_id=new_item.get("accountId"),
                category_id=new_item.get("categoryId"),
                category_name=new_item.get("categoryName"),
                from_account_id=new_item.get("fromAccountId"),
                to_account_id=new_item.get("toAccountId"),
                splits=new_item.get("splits"),
                refund_of_id=new_item.get("refundOfId"),
                debt_id=new_item.get("debtId"),
                project_id=new_item.get("projectId"),
                reward_rule_ids=new_item.get("rewardRuleIds"),
            )
            # 專案(Phase 13):同上,在 merge 后的最终状态上再擋一次
            # tx_type 限制 —— 涵蓋「project_id 沒變,但這次 PATCH 把 tx_type
            # 改成 transfer」這種 adjustment 檢查涵蓋不到的組合。
            if new_item.get("projectId") and new_item.get("type") not in ("expense", "income"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="project_id can only be set on expense/income transactions",
                )

            # 退款分类自动归类(2026-08-04 使用者反馈):跟 create 快路径同一套
            # 逻辑,搬到 merge 后的最终状态判断——`prev_item` 原本不是退款、
            # 这次 PATCH 才把它变成退款(`refundOfId` 从空变非空)时才补,不然
            # 每次编辑一笔已经是退款的交易(`refundOfId` 原样带回)都会重新
            # 触发,把用户后来自己改的分类覆盖回「退款」。
            is_newly_refund = bool(new_item.get("refundOfId")) and not prev_item.get("refundOfId")
            if is_newly_refund and not new_item.get("categoryId") and not new_item.get("categoryName"):
                from ...services import card_rewards as _card_rewards
                refund_tx_type = new_item.get("type")
                if refund_tx_type in ("income", "expense"):
                    new_item["categoryId"] = _card_rewards.ensure_refund_category(
                        db, user_id=ledger.user_id, kind=refund_tx_type,
                    )
                    new_item["categoryName"] = _card_rewards.REFUND_CATEGORY_NAME
                    new_item["categoryKind"] = refund_tx_type

            # 拆帳(§2.4):在 merge 后的最终状态上校验(而不是只看这次 PATCH
            # 带的字段)—— 单独改 amount 不改 splits 时,也要保证新 amount
            # 还能被既有 splits 加总对上。
            if new_item.get("splits"):
                if new_item.get("refundOfId"):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="a refund transaction cannot itself be split",
                    )
                _validate_tx_splits(
                    tx_type=new_item.get("type"),
                    amount=new_item.get("amount"),
                    splits=new_item["splits"],
                )

            # 信用卡紅利回饋(§2.9.5,2026-08-06 改版):在 merge 后的最终状态上
            # 校验(同拆帳同一理由)—— 单独改 account_id 不改 rewardRuleIds
            # 时,也要保证既有勾选的规则仍然归属新的 account_id。
            if new_item.get("rewardRuleIds"):
                if new_item.get("accountId") != prev_item.get("accountId"):
                    # 2026-08-16 補:帳戶真的變了才需要过滤 —— 没变的话舊
                    # rewardRuleIds 本来就还归属同一个 account_id,不用查一次。
                    filtered_ids = _drop_reward_rules_orphaned_by_account_change(
                        db, user_id=ledger.user_id, account_id=new_item.get("accountId"),
                        reward_rule_ids=[str(v) for v in new_item["rewardRuleIds"]],
                    )
                    if filtered_ids:
                        new_item["rewardRuleIds"] = filtered_ids
                    else:
                        new_item.pop("rewardRuleIds", None)
                if new_item.get("rewardRuleIds"):
                    _assert_reward_rules_valid(
                        db, user_id=ledger.user_id, account_id=new_item.get("accountId"),
                        reward_rule_ids=[str(v) for v in new_item["rewardRuleIds"]],
                    )

            # 信用卡紅利回饋事後修改重算(§2.9.5.4 補強,Phase 8 #5,2026-08
            # 使用者反饋):happened_at/amount/category_id/account_id 這四個
            # 欄位會影響回饋計算,合併後任一實際變動時,把這筆交易先前已經
            # 逐筆結算入帳的回饋金沖銷(刪除)掉,讓下一輪排程用新欄位值重新
            # 計算補發——note/商店等不影響計算的欄位編輯不觸發,避免使用者
            # 隨手改備註就把回饋金搞亂。
            reward_calc_impacted = (
                prev_item.get("happenedAt") != new_item.get("happenedAt")
                or prev_item.get("amount") != new_item.get("amount")
                or prev_item.get("categoryId") != new_item.get("categoryId")
                or prev_item.get("accountId") != new_item.get("accountId")
            )
            if reward_calc_impacted:
                from ...services.card_reward_payout import reverse_card_reward_payouts_for_edit
                reverse_card_reward_payouts_for_edit(
                    db, ledger_id=ledger.id, user_id=ledger.user_id,
                    edited_tx_id=tx_id, now=now,
                )

            change_row = SyncChange(
                user_id=ledger.user_id,
                ledger_id=ledger.id,
                entity_type="transaction",
                entity_sync_id=tx_id,
                action="upsert",
                payload_json=new_item,
                updated_at=now,
                updated_by_device_id=device_id,
                updated_by_user_id=current_user.id,
            )
            db.add(change_row)
            db.flush()
            projection.upsert_tx(
                db,
                ledger_id=ledger.id,
                user_id=ledger.user_id,
                source_change_id=change_row.change_id,
                payload=new_item,
            )

            if is_newly_refund:
                # 信用卡回饋沖銷:跟 create 快路径同一套逻辑,见该处注释。
                from ...services.card_reward_payout import reverse_card_reward_payouts_for_refund
                reverse_card_reward_payouts_for_refund(
                    db, ledger_id=ledger.id, user_id=ledger.user_id,
                    refunded_tx_id=str(new_item.get("refundOfId")), now=now,
                )

        new_change_id = change_row.change_id

        db.add(
            AuditLog(
                user_id=current_user.id,
                ledger_id=ledger.id,
                action=audit_action,
                metadata_json={
                    "ledgerId": ledger.external_id,
                    "baseChangeId": base_change_id,
                    "newChangeId": new_change_id,
                    "entityId": tx_id,
                },
            )
        )

        response = WriteCommitMeta(
            ledger_id=ledger.external_id,
            base_change_id=base_change_id,
            new_change_id=new_change_id,
            server_timestamp=now,
            idempotency_replayed=False,
            entity_id=tx_id,
        )

        request_hash = _hash_request(request.method, request.url.path, request_payload)
        if idempotency_key:
            db.add(
                SyncPushIdempotency(
                    user_id=current_user.id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response_json=response.model_dump(mode="json"),
                    created_at=now,
                    expires_at=now + timedelta(hours=24),
                )
            )

        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if idempotency_key:
                replay = _load_idempotent_response(
                    db,
                    user_id=current_user.id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay, True
            raise exc
        return response, False

    response, did_replay = await run_in_threadpool(_core)

    if not did_replay:
        # 共享账本:fan-out 给所有 LedgerMember(非只 owner)。否则 web/Owner 写
        # tx,Editor 端 mobile 收不到 sync_change WS,要重启 app 才能看到新数据。
        from ...websocket_manager import broadcast_to_ledger
        await broadcast_to_ledger(
            db=db,
            ws_manager=request.app.state.ws_manager,
            ledger_id=ledger.id,
            payload={
                "type": "sync_change",
                "ledgerId": ledger.external_id,
                "serverCursor": response.new_change_id,
                "serverTimestamp": response.server_timestamp.isoformat(),
            },
        )
    logger.info(
        "write.commit.fast action=%s ledger=%s entity=%s change_id=%d device=%s user=%s replay=%s",
        audit_action, ledger.external_id, tx_id, response.new_change_id, device_id, current_user.id, did_replay,
    )
    return response


def _projection_row_to_tx_dict(row: ReadTxProjection) -> dict[str, Any]:
    """projection row → snapshot item dict,跟 snapshot_builder.build 的格式一致。"""
    from ...snapshot_builder import _to_iso_utc
    item: dict[str, Any] = {
        "syncId": row.sync_id,
        "type": row.tx_type,
        "amount": row.amount,
        "happenedAt": _to_iso_utc(row.happened_at),
    }
    if row.note is not None:
        item["note"] = row.note
    if row.merchant is not None:
        item["merchant"] = row.merchant
    if row.category_sync_id:
        item["categoryId"] = row.category_sync_id
    if row.category_name:
        item["categoryName"] = row.category_name
    if row.category_kind:
        item["categoryKind"] = row.category_kind
    if row.account_sync_id:
        item["accountId"] = row.account_sync_id
    if row.account_name:
        item["accountName"] = row.account_name
    if row.from_account_sync_id:
        item["fromAccountId"] = row.from_account_sync_id
    if row.from_account_name:
        item["fromAccountName"] = row.from_account_name
    if row.to_account_sync_id:
        item["toAccountId"] = row.to_account_sync_id
    if row.to_account_name:
        item["toAccountName"] = row.to_account_name
    if row.tags_csv:
        item["tags"] = row.tags_csv
    if row.tag_sync_ids_json:
        try:
            tag_ids = json.loads(row.tag_sync_ids_json)
            if isinstance(tag_ids, list) and tag_ids:
                item["tagIds"] = tag_ids
        except json.JSONDecodeError:
            pass
    if row.attachments_json:
        try:
            atts = json.loads(row.attachments_json)
            if isinstance(atts, list) and atts:
                item["attachments"] = atts
        except json.JSONDecodeError:
            pass
    if row.tx_index:
        item["txIndex"] = row.tx_index
    if row.created_by_user_id:
        item["createdByUserId"] = row.created_by_user_id
    # 账单标记(.docs/transaction-flags):带上当前值,update merge 不传该字段时
    # 保留原值,且新 SyncChange payload 用 camelCase 携带它们。
    item["excludeFromStats"] = bool(row.exclude_from_stats)
    item["excludeFromBudget"] = bool(row.exclude_from_budget)
    # v30 交易级多币种:必须带上两字段(与 snapshot_builder.build 对齐)。这是
    # web PATCH update_tx 快路径的 prev_item —— 漏了它,mutator 的 rescale
    # 守卫(nativeAmount in item)永不触发,upsert 会把外币交易的 currency_code/
    # native_amount 写成 NULL,所有账本维度 coalesce 回退 amount → 折算被抹掉
    # 并同步给 mobile。NULL 不产生 key(统计端 COALESCE 兜底)。
    if row.currency_code is not None:
        item["currencyCode"] = row.currency_code
    if row.native_amount is not None:
        item["nativeAmount"] = row.native_amount
    # 手續費/折扣(2026-08 使用者需求):同上,fast path 的 prev_item 也必须
    # 带上,否则 PATCH 只改其它字段时,update_transaction 的 mapping 循环/
    # 显式 "key" in payload 判断读不到旧值,会把已设置的手續費/折扣静默清空。
    if row.base_amount is not None:
        item["baseAmount"] = row.base_amount
    if row.fee_amount is not None:
        item["feeAmount"] = row.fee_amount
    if row.fee_label is not None:
        item["feeLabel"] = row.fee_label
    if row.discount_amount is not None:
        item["discountAmount"] = row.discount_amount
    if row.discount_label is not None:
        item["discountLabel"] = row.discount_label
    # 退款(§2.6):fast path 的 prev_item 也要带上,否则 PATCH 走
    # update_transaction 时读不到 payload.get("refund_of_id") in item 的旧值,
    # 未显式改 refund_of_id 的 update 请求会把关联静默丢掉。
    if row.refund_of_sync_id is not None:
        item["refundOfId"] = row.refund_of_sync_id
    # 週期性收支(§2.12.2 Phase 1.5):同样道理,fast path 编辑一个 occurrence
    # 的其它字段(金额/备注)时,不带上这两个会让反查字段/overridden 标记
    # 被静默清空。
    if row.recurring_rule_sync_id is not None:
        item["recurringRuleId"] = row.recurring_rule_sync_id
    if row.recurring_occurrence_overridden:
        item["recurringOccurrenceOverridden"] = True
    # 分期付款(§2.12.1 Phase 1.5):同理,fast path 编辑一笔分期期数交易的
    # 其它字段时,不带上这个会让 installmentPlanId 反查在这次 upsert 后被
    # 静默清空(merge 逻辑只保留 prev_item 里已有的 key)。
    if row.installment_plan_sync_id is not None:
        item["installmentPlanId"] = row.installment_plan_sync_id
    # 借還款追蹤(§2.5 Phase 3):同理,fast path 编辑一笔还款交易的其它字段
    # 时,不带上这个会让 debtId 反查在这次 upsert 后被静默清空。
    if row.debt_sync_id is not None:
        item["debtId"] = row.debt_sync_id
    # 專案(Phase 13):同理,fast path 编辑一笔已掛專案交易的其它字段时,
    # 不带上这个会让 projectId 反查在这次 upsert 后被静默清空(CLAUDE.md
    # 記過的既有 bug 模式,見 `_shared.py::_projection_row_to_tx_dict` docstring)。
    if row.project_sync_id is not None:
        item["projectId"] = row.project_sync_id
    # 拆帳(§2.4):同理,fast path 编辑一笔拆帳交易的其它字段(备注/金额)时,
    # 不带上旧 splits 会让 update_transaction 因为 payload 没传 "splits" key
    # 而保留 prev_item 里的值 —— 前提是这里真的把它塞进 prev_item。
    if row.splits_json:
        try:
            splits = json.loads(row.splits_json)
            if isinstance(splits, list) and splits:
                item["splits"] = splits
        except json.JSONDecodeError:
            pass
    # 信用卡紅利回饋(§2.9.5,2026-08-06 改版):同理,fast path 编辑一笔已
    # 勾选回饋规则的交易的其它字段(金额/备注)时,不带上这个会让
    # rewardRuleIds 在这次 upsert 后被静默清空(既有 bug,2026-08-04 补上,
    # 跟 snapshot_builder.py 那处漏 SELECT 是同一类问题)。
    if row.reward_rule_sync_ids_json:
        try:
            reward_rule_ids = json.loads(row.reward_rule_sync_ids_json)
            if isinstance(reward_rule_ids, list) and reward_rule_ids:
                item["rewardRuleIds"] = reward_rule_ids
        except json.JSONDecodeError:
            pass
    # 信用卡紅利回饋自動入帳(§2.9.5.4 補強):同理,fast path 编辑一笔回饋
    # 入帳交易的其它字段时,不带上这个会让 rewardSourceTxId 反查被静默清空。
    if row.reward_source_tx_sync_id is not None:
        item["rewardSourceTxId"] = row.reward_source_tx_sync_id
    # 延後入帳/對帳模式(§2.10 Phase 5,2026-08-09 改版):同理,fast path
    # 编辑一笔已標記延後入帳/已確認的交易的其它字段时(例如只改 reconciled_at
    # 却不带 deferred_posting_at,或反过来),不带上这两个会让另一个字段在
    # 这次 upsert 后被静默清空——`projection.upsert_tx` 对这两个字段的处理
    # 明确写着「缺键保留由上游 merge_with_existing 负责」,但 fast path 是
    # 直接拿这个函式的返回值当 upsert payload,并不会经过
    # `sync_applier.apply_change_to_projection` 那层「跟 DB 既有值合并」,
    # 所以「上游」指的就是这里——漏了就真的会被冲成 NULL,交易因此从它
    # 应该归属的对帳週期消失(2026-08 使用者反饋)。
    if row.deferred_posting_at is not None:
        item["deferredPostingAt"] = _to_iso_utc(row.deferred_posting_at)
    if row.reconciled_at is not None:
        item["reconciledAt"] = _to_iso_utc(row.reconciled_at)
    return item


async def _commit_write(
    *,
    request: Request,
    db: Session,
    current_user: User,
    ledger: Ledger,
    base_change_id: int,
    request_payload: dict,
    idempotency_key: str | None,
    device_id: str,
    audit_action: str,
    mutate: Callable[[dict], tuple[dict, str | None]],
) -> WriteCommitMeta:
    # DB 工作整段丢 threadpool(issue #31 A2),不阻塞 event loop;两类 WS 广播
    # 在线程返回后于 loop 上做。_core 返回 (response, did_replay, user_global_events)。
    def _core() -> tuple[WriteCommitMeta, bool, list]:
        # Serialize concurrent writers on the same ledger。方案 B 后不再写 snapshot,
        # 但依然锁 —— 防止 rename cascade 的 SQL UPDATE 和 tx upsert 交叉跑。
        lock_ledger_for_materialize(db, ledger.id)

        # strict_base_change_id 语义转换:原先比 latest ledger_snapshot.change_id,
        # 方案 B 后 snapshot 不再写,改比 ledger 上任意 entity 的最新 change_id
        # (更严格 —— 连 tx 级修改都会触发 409)。默认关闭的 feature flag,生产用不到。
        if get_settings().strict_base_change_id:
            latest_any_change_id = snapshot_builder.latest_change_id(db, ledger.id)
            if base_change_id != latest_any_change_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Write conflict",
                        "latest_change_id": latest_any_change_id,
                    },
                )

        # 从 projection 按需构建当前状态给 mutator 吃。这个 snapshot dict 不写回 DB ——
        # 只是 mutator 内部用来查当前实体、做 duplicate/actor 校验。
        snapshot = snapshot_builder.build(db, ledger)
        # Shallow-per-entity copy for diffing(mutator 会原地改 items[i] 等)
        prev_snapshot = {**snapshot}
        for _k in (
            "items", "accounts", "categories", "tags", "budgets",
            "recurringRules", "installmentPlans", "installmentPeriods",
            "debts", "txTemplates", "projectCategoryBudgets", "stockTrades",
        ):
            arr = snapshot.get(_k)
            if isinstance(arr, list):
                prev_snapshot[_k] = [dict(e) if isinstance(e, dict) else e for e in arr]
        try:
            next_snapshot, entity_id = mutate(snapshot)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found") from None
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        now = _utcnow()
        # 不再写 ledger_snapshot 行。emit 个体 SyncChange + 同事务 projection 写入,
        # new_change_id 用 emit 出来的最后一条 SyncChange 的 change_id。
        emitted_change_ids = _emit_entity_diffs(
            db,
            ledger=ledger,
            current_user=current_user,
            device_id=device_id,
            prev=prev_snapshot,
            next_snapshot=next_snapshot,
            now=now,
        )
        # 无变化 → 用当前 max change_id(幂等/只是触发写但没真修改的场景)
        new_change_id = max(emitted_change_ids) if emitted_change_ids else (
            snapshot_builder.latest_change_id(db, ledger.id)
        )

        db.add(
            AuditLog(
                user_id=current_user.id,
                ledger_id=ledger.id,
                action=audit_action,
                metadata_json={
                    "ledgerId": ledger.external_id,
                    "baseChangeId": base_change_id,
                    "newChangeId": new_change_id,
                    "entityId": entity_id,
                },
            )
        )

        response = WriteCommitMeta(
            ledger_id=ledger.external_id,
            base_change_id=base_change_id,
            new_change_id=new_change_id,
            server_timestamp=now,
            idempotency_replayed=False,
            entity_id=entity_id,
        )

        request_hash = _hash_request(request.method, request.url.path, request_payload)
        if idempotency_key:
            db.add(
                SyncPushIdempotency(
                    user_id=current_user.id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    response_json=response.model_dump(mode="json"),
                    created_at=now,
                    expires_at=now + timedelta(hours=24),
                )
            )

        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if idempotency_key:
                replay = _load_idempotent_response(
                    db,
                    user_id=current_user.id,
                    device_id=device_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay, True, []
            raise exc

        logger.info(
            "write.commit action=%s ledger=%s entity=%s change_id=%d device=%s user=%s",
            audit_action,
            ledger.external_id,
            entity_id,
            response.new_change_id,
            device_id,
            current_user.id,
        )
        # §7 共享账本:user-global 实体(category/account/tag)的 shared_resource
        # fan-out 输入。纯 diff(sync),在线程里算好,出去再 await 推送。
        user_global_events = _diff_user_global_for_shared_resource(
            prev=prev_snapshot, next=next_snapshot
        )
        return response, False, user_global_events

    response, did_replay, user_global_events = await run_in_threadpool(_core)
    if did_replay:
        return response

    # 共享账本:fan-out 给所有 LedgerMember(非只 owner)。否则 web/Owner 写
    # tx/budget/category/...,Editor 端 mobile 收不到 sync_change WS,要重启
    # app 才能看到新数据。
    from ...websocket_manager import broadcast_to_ledger
    await broadcast_to_ledger(
        db=db,
        ws_manager=request.app.state.ws_manager,
        ledger_id=ledger.id,
        payload={
            "type": "sync_change",
            "ledgerId": ledger.external_id,
            "serverCursor": response.new_change_id,
            "serverTimestamp": response.server_timestamp.isoformat(),
        },
    )

    # Editor 的 /sync/pull 只返回自己 scope=user 的变更,Owner 的 user-global
    # 变更永远拉不到 — 必须靠这条 WS 事件主动推。mobile push 路径早有此逻辑。
    if user_global_events:
        await _broadcast_shared_resource_events(
            request=request,
            db=db,
            owner_user_id=current_user.id,
            events=user_global_events,
        )

    return response


def _diff_user_global_for_shared_resource(
    *,
    prev: dict[str, Any],
    next: dict[str, Any],
) -> list[dict[str, Any]]:
    """Diff prev/next snapshots for user-global entities (category/account/tag)
    → 返回 shared_resource_change WS event payload 列表(resource_type /
    action / sync_id / payload)。
    """
    out: list[dict[str, Any]] = []
    for key, resource_type in (
        ("categories", "category"),
        ("accounts", "account"),
        ("tags", "tag"),
    ):
        prev_map = {e["syncId"]: e for e in (prev.get(key) or []) if "syncId" in e}
        next_map = {e["syncId"]: e for e in (next.get(key) or []) if "syncId" in e}
        for sid, entity in next_map.items():
            prev_e = prev_map.get(sid)
            if prev_e is None or entity != prev_e:
                out.append({
                    "resource_type": resource_type,
                    "action": "upsert",
                    "sync_id": sid,
                    "payload": entity,
                })
        for sid in prev_map:
            if sid not in next_map:
                out.append({
                    "resource_type": resource_type,
                    "action": "delete",
                    "sync_id": sid,
                    "payload": {"syncId": sid},
                })
    return out


async def _broadcast_shared_resource_events(
    *,
    request: Request,
    db: Session,
    owner_user_id: str,
    events: list[dict[str, Any]],
) -> None:
    """对 owner 作为 owner 的所有共享账本(member_count > 1),给非 owner member
    推送 shared_resource_change 事件。复刻 sync/push.py:356-381 的逻辑。"""
    from ...models import Ledger as _L, LedgerMember as _LM
    from sqlalchemy import func as _func
    from ...ledger_access import list_ledger_members

    ws_manager = request.app.state.ws_manager
    rows = db.execute(
        select(_L.id, _L.external_id)
        .join(_LM, _LM.ledger_id == _L.id)
        .where(_L.user_id == owner_user_id)
        .group_by(_L.id, _L.external_id)
        .having(_func.count(_LM.user_id) > 1)
    ).all()
    for ledger_id, ledger_external_id in rows:
        for member_user_id, role in list_ledger_members(db, ledger_id=ledger_id):
            if role == "owner":
                continue
            for ev in events:
                await ws_manager.broadcast_to_user(member_user_id, {
                    "type": "shared_resource_change",
                    "ledgerId": ledger_external_id,
                    "resourceType": ev["resource_type"],
                    "action": ev["action"],
                    "payload": ev["payload"],
                })


def _prepare_write(
    *,
    db: Session,
    current_user: User,
    ledger_external_id: str,
    required_roles: set[str],
    idempotency_key: str | None,
    device_id: str,
    method: str,
    path: str,
    payload: dict,
) -> tuple[Ledger, WriteCommitMeta | None]:
    ledger, _ = _load_ledger_for_write(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
        roles=required_roles,
    )
    if not idempotency_key:
        return ledger, None
    _purge_expired_idempotency(db)
    replay = _load_idempotent_response(
        db,
        user_id=current_user.id,
        device_id=device_id,
        idempotency_key=idempotency_key,
        request_hash=_hash_request(method, path, payload),
    )
    return ledger, replay


def _normalize_currency(raw: str | None) -> str:
    value = (raw or "CNY").strip().upper()
    if not value:
        return "CNY"
    return value[:16]


def _normalize_ledger_name(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ledger name is required")
    return value[:255]


def _payload_with_actor(
    payload: dict,
    current_user: User,
    *,
    ledger: Ledger | None = None,
) -> dict:
    merged = dict(payload)
    merged["__actor_user_id"] = current_user.id
    merged["__actor_is_admin"] = bool(current_user.is_admin)
    # 共享账本场景:_prepare_write 已经在 endpoint 层按 LedgerMember role
    # 卡过权限,通过到这里的 caller 必然是合法成员(Owner 或 Editor),
    # snapshot_mutator 的 _assert_can_modify_entity "严格按创建人匹配"
    # 在共享账本下应该让任何合法成员都跳过(Phase 1 决策:LedgerMember
    # 里所有人都能改账本内所有 tx)。
    #
    # 原老条件 `ledger.user_id != current_user.id` 错了:Owner 编辑
    # Editor 创建的 tx 时,Owner == ledger.user_id 但 created_by != Owner,
    # 严格 check 触发 → 403 "write role forbidden: entity owner mismatch"。
    #
    # 单人账本(无其它 member):tx.created_by 自然等于 actor,严格 check
    # 本来就不会 fire,设了 flag 也无副作用。所以无条件设是安全的。
    if ledger is not None:
        merged["__actor_in_shared_ledger"] = True
    return merged


def _resolve_category_display(
    db: Session, *, user_id: str, category_id: str | None,
) -> tuple[str | None, str | None]:
    """§2.12.1/§2.12.2:分期/週期性收支的写入口只拿得到 category_id(请求
    schema 没有 category_name 字段,不像一般建交易那样前端会一起送 name),
    但 create_transaction 的 denormalize 只在 payload 带 category_name 时才写
    read_tx_projection.category_name —— 缺了它,生成的每期交易在列表/详情页
    分类栏会显示空白。这里查一次 UserCategoryProjection 补上 (name, kind)。
    """
    if not category_id:
        return None, None
    row = db.execute(
        select(UserCategoryProjection.name, UserCategoryProjection.kind).where(
            UserCategoryProjection.user_id == user_id,
            UserCategoryProjection.sync_id == category_id,
        )
    ).first()
    if row is None:
        return None, None
    return row[0], row[1]


def _resolve_tag_names(
    db: Session, *, user_id: str, tag_ids: list[str] | None,
) -> list[str]:
    """Phase 24:週期性收支的 merchant/tag_ids 转发给逐笔交易时,
    `update_transaction` 的 `tags`(展示用 CSV,驱动 `tags_list`)与
    `tag_ids`(驱动筛选/勾选)是两个独立字段,必须一起给,否则交易列表页
    显示的标签名称会跟实际 tag_ids 脱节。这里查一次 UserTagProjection
    补上名称列表。"""
    if not tag_ids:
        return []
    rows = db.execute(
        select(UserTagProjection.sync_id, UserTagProjection.name).where(
            UserTagProjection.user_id == user_id,
            UserTagProjection.sync_id.in_(tag_ids),
        )
    ).all()
    name_by_id = {r[0]: (r[1] or "") for r in rows}
    return [name_by_id[t] for t in tag_ids if t in name_by_id and name_by_id[t]]


def _resolve_account_display(
    db: Session, *, user_id: str, account_id: str | None,
) -> str | None:
    """同 `_resolve_category_display`,但给 account_id → account_name。"""
    if not account_id:
        return None
    return db.scalar(
        select(UserAccountProjection.name).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )


def _assert_account_not_group(
    db: Session, *, user_id: str, account_id: str | None, field_name: str = "account_id",
) -> None:
    """主帳戶(§2.9 Phase 4,2026-08-02 改版):`account_type == "account_group"`
    是純管理容器,不能被一般交易/週期性收支/分期付款拿來當 from/to/account
    的目標 —— 使用者反馈"主帳戶不可以當作一個獨立的帳戶"。只有信用卡繳款
    端點(`routers/write/accounts.py::card_payment_ep`)内部产生的「溢繳結轉」
    那一笔特例转账,是直接调用 `snapshot_mutator.create_transaction`(不经过
    这里),所以不受这条限制影响。找不到帳戶(未知 id/字串)时不拦——维持
    现有"account_id 基本不做存在性校验"的宽松行为,只挡"确定是 group"这种。
    """
    if not account_id:
        return
    account_type = db.scalar(
        select(UserAccountProjection.account_type).where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.sync_id == account_id,
        )
    )
    if account_type == "account_group":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} refers to an account_group, which cannot hold transactions directly",
        )


def _assert_category_required(
    tx_type: str | None, category_id: str | None, *, field_name: str = "category_id",
) -> None:
    """需求 #14(2026-08 使用者回饋改善 SD Phase 12):系統自動產生交易不可
    漏分類。週期性收支(非轉帳)、分期付款(恒為 expense,無轉帳語意)建立/
    編輯時,分類為必填,避免產生的每期交易落地時分類欄位是空的、報表出現
    Uncategorized(呼應 CLAUDE.md「系統自動產生交易不可漏分類」鐵律的第三個
    案例——退款/信用卡回饋/欠還款已經有 `ensure_*_category` 自動歸類的模式,
    這裡直接改成前端強制必選,不是自動歸類,因為使用者本來就該手動指定這筆
    週期性收支/分期付款要記在哪個分類下,不適合系統代猜)。轉帳類型的週期
    規則沒有分類語意,維持允許為空。"""
    if tx_type == "transfer":
        return
    if not category_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required",
        )


def _assert_transfer_to_amount_valid(
    db: Session, *, user_id: str, ledger_id: str, tx_id: str | None, payload: dict,
) -> None:
    """跨幣別轉帳(2026-08):轉出(`amount`)/轉入(`to_amount`)帳戶幣別不同時,
    `to_amount` 必須有值——沒有的話轉入帳戶的餘額增減會沿用轉出帳戶的原幣
    金額,兩邊帳本的錢對不上。幣別相同(含任一帳戶查不到幣別的寬鬆情形)則
    不要求,`to_amount` 為 None 時下游一律 `COALESCE(to_amount, amount)` 回退。

    create(tx_id=None):`payload` 是完整 model_dump,直接讀。
    update(tx_id 給值):`payload` 是 `exclude_unset=True` 的差量——
    tx_type/from_account_id/to_account_id/to_amount 一個都沒出現時整個
    no-op(這次 PATCH 沒動轉帳的任何一端,沿用既有已驗證過的狀態);
    出現任一個時,缺的欄位 fallback 現有 DB 值(partial update 慣例,同
    `_normalize_fee_discount_amount`)。
    """
    _relevant_keys = ("tx_type", "from_account_id", "to_account_id", "to_amount")
    existing = None
    if tx_id is not None:
        if not any(k in payload for k in _relevant_keys):
            return
        existing = db.execute(
            select(
                ReadTxProjection.tx_type,
                ReadTxProjection.from_account_sync_id,
                ReadTxProjection.to_account_sync_id,
                ReadTxProjection.to_amount,
            ).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.sync_id == tx_id,
            )
        ).first()

    def _eff(key: str, existing_attr: str | None = None):
        if key in payload:
            return payload.get(key)
        return getattr(existing, existing_attr or key) if existing else None

    tx_type = _eff("tx_type")
    if tx_type != "transfer":
        return
    from_account_id = _eff("from_account_id", "from_account_sync_id")
    to_account_id = _eff("to_account_id", "to_account_sync_id")
    to_amount = _eff("to_amount")
    if not from_account_id or not to_account_id or to_amount is not None:
        return
    currencies = dict(
        db.execute(
            select(UserAccountProjection.sync_id, UserAccountProjection.currency).where(
                UserAccountProjection.user_id == user_id,
                UserAccountProjection.sync_id.in_([from_account_id, to_account_id]),
            )
        ).all()
    )
    from_currency = (currencies.get(from_account_id) or "").strip().upper()
    to_currency = (currencies.get(to_account_id) or "").strip().upper()
    if from_currency and to_currency and from_currency != to_currency:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="to_amount is required when from/to accounts have different currencies",
        )


def _assert_can_modify_entity(
    *,
    db: Session,  # noqa: ARG001 — retained for signature compat
    ledger: Ledger,
    current_user: User,
    entity_sync_id: str,  # noqa: ARG001 — retained for signature compat
) -> None:
    """共享账本前的 owner-only check。endpoint 已经走过
    ``_prepare_write(required_roles=...)`` 的 LedgerMember role check,
    Owner / Editor 都放行。这个 helper 在共享账本场景错杀 Editor → 403。
    保留函数签名(被 transactions.py 旧调用点引用),内部改成 no-op。
    """
    _ = ledger
    _ = current_user
    return None



__all__ = [
    'hashlib',
    'json',
    'logging',
    'Callable',
    'datetime',
    'timedelta',
    'timezone',
    'Any',
    'uuid4',
    'APIRouter',
    'Depends',
    'Header',
    'HTTPException',
    'Request',
    'status',
    'func',
    'select',
    'delete',
    'IntegrityError',
    'Session',
    'lock_ledger_for_materialize',
    'get_settings',
    'get_db',
    'get_current_user',
    'require_any_scopes',
    'require_scopes',
    'ROLE_EDITOR',
    'ROLE_OWNER',
    'get_accessible_ledger_by_external_id',
    'AuditLog',
    'Ledger',
    'LedgerMember',
    'ReadCardRewardRuleProjection',
    'ReadDebtProjection',
    'ReadProjectProjection',
    'ReadTxProjection',
    'ReadTxTemplateProjection',
    'SyncChange',
    'SyncPushIdempotency',
    'User',
    'UserAccountProjection',
    'UserCategoryProjection',
    'WriteAccountCreateRequest',
    'WriteAccountDeleteRequest',
    'WriteAccountReorderRequest',
    'WriteAccountUpdateRequest',
    'WriteBalanceAdjustmentRequest',
    'WriteCardPaymentRequest',
    'WriteCardRewardManualPayoutRequest',
    'WriteCardRewardRuleCreateRequest',
    'WriteCardRewardRuleUpdateRequest',
    'WriteBudgetCreateRequest',
    'WriteBudgetUpdateRequest',
    'WriteCategoryCreateRequest',
    'WriteCategoryUpdateRequest',
    'WriteCommitMeta',
    'WriteDebtCreateRequest',
    'WriteStockTradeCreateRequest',
    'WriteStockTradeUpdateRequest',
    'ReadStockTradeProjection',
    '_cascade_delete_linked_stock_trades',
    'WriteDebtUpdateRequest',
    'WriteEntityDeleteRequest',
    'WriteInstallmentEarlyRepayRequest',
    'WriteInstallmentPayoffRequest',
    'WriteInstallmentPeriodRefundRequest',
    'WriteInstallmentPeriodUpdateRequest',
    'WriteInstallmentPlanCreateRequest',
    'WriteInstallmentPlanUpdateRequest',
    'WriteInstallmentRebalanceRequest',
    'WriteLedgerCreateRequest',
    'WriteLedgerMetaUpdateRequest',
    'WriteProjectCategoryBudgetCreateRequest',
    'WriteProjectCategoryBudgetUpdateRequest',
    'WriteProjectCreateRequest',
    'WriteProjectUpdateRequest',
    'WriteStatementClearConfirmationsRequest',
    'WriteRecurringOccurrenceUpdateRequest',
    'WriteRecurringRuleCreateRequest',
    'WriteRecurringRuleDeleteRequest',
    'WriteRecurringRuleUpdateRequest',
    'WriteRecurringUpdateFromRequest',
    'WriteTagCreateRequest',
    'WriteTagUpdateRequest',
    'WriteTransactionCreateRequest',
    'WriteTransactionUpdateRequest',
    'WriteTxTemplateApplyRequest',
    'WriteTxTemplateCreateRequest',
    'WriteTxTemplateUpdateRequest',
    'SCOPE_APP_WRITE',
    'SCOPE_WEB_WRITE',
    'projection',
    'snapshot_builder',
    'snapshot_cache',
    'create_account',
    'create_budget',
    'create_card_reward_rule',
    'create_category',
    'create_debt',
    'create_installment_period',
    'create_installment_plan',
    'create_project',
    'create_project_category_budget',
    'create_recurring_rule',
    'create_tag',
    'create_transaction',
    'create_tx_template',
    'delete_account',
    'delete_budget',
    'delete_card_reward_rule',
    'delete_category',
    'delete_debt',
    'delete_installment_period',
    'delete_installment_plan',
    'delete_project',
    'delete_project_category_budget',
    'delete_recurring_rule',
    'delete_tag',
    'delete_transaction',
    'delete_tx_template',
    'ensure_snapshot_v2',
    'update_account',
    'update_budget',
    'update_card_reward_rule',
    'update_category',
    'update_debt',
    'update_installment_period',
    'update_installment_plan',
    'update_project',
    'update_project_category_budget',
    'update_recurring_rule',
    'update_tag',
    'update_transaction',
    'update_tx_template',
    'logger',
    'settings',
    '_WRITE_SCOPE_DEP',
    '_TRANSACTION_WRITE_ROLES',
    '_OWNER_ONLY_ROLES',
    '_WRITE_RESPONSES',
    '_utcnow',
    '_assert_debt_exists',
    '_assert_project_exists',
    '_assert_reward_rules_valid',
    '_assert_account_not_group',
    '_assert_category_required',
    '_assert_transfer_to_amount_valid',
    '_assert_valid_adjustment_tx',
    '_normalize_fee_discount_amount',
    '_normalize_recurring_rule_fee_discount',
    '_USER_PROJECTION_UPSERTERS',
    '_USER_PROJECTION_DELETERS',
    '_LEDGER_PROJECTION_UPSERTERS',
    '_LEDGER_PROJECTION_DELETERS',
    '_TX_CASCADE_FIELDS',
    '_tx_diff_only_cascade',
    '_collect_renames',
    '_diff_entity_list',
    '_emit_entity_diffs',
    '_load_ledger_for_write',
    '_latest_snapshot_change',
    '_parse_snapshot',
    '_hash_request',
    '_purge_expired_idempotency',
    '_load_idempotent_response',
    '_commit_write_fast_tx',
    '_commit_create_tx_fast',
    '_projection_row_to_tx_dict',
    '_commit_write',
    '_prepare_write',
    '_normalize_currency',
    '_normalize_ledger_name',
    '_payload_with_actor',
    '_resolve_category_display',
    '_resolve_account_display',
    '_resolve_tag_names',
    '_assert_can_modify_entity',
]
