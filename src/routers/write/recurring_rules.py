"""Recurring rules write endpoints(§2.2 / Phase 1.5 修正版 §2.12.2
MOZE_FEATURE_GAP_SD.md）。

POST 建規則時依 `services.recurring_schedule.plan_initial_generation` 依视窗
策略批次生成 occurrence 交易(有 `end_at` 全部生成 [受安全上限保护];沒有
`end_at` 先生成默认视窗,之後由 `services.recurring_materializer` 的
"視窗續產生" loop 補滿),不再依赖旧版排程逐筆到期生成(见
recurring_materializer.py 的重构说明)。

差异化编辑端点(§2.12.2):
- `PATCH/DELETE .../occurrences/{tx_id}`:單獨編輯/刪除某一期已生成的
  occurrence,標記 `recurring_occurrence_overridden=true`,之後
  `update-from`/視窗續產生都跳過它。
- `POST .../update-from/{tx_id}`:更新規則本身字段 + 該期以後所有未
  overridden 的已生成交易(不動 `happened_at`)。
- `POST .../terminate-future`:刪除所有未發生的已生成交易(不論是否
  overridden),規則標記 `enabled=false`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from ._shared import *  # noqa: F401,F403 — 集中从 _shared 取所有 symbol
from ...models import ReadRecurringRuleProjection
from ...snapshot_mutator import create_transaction as _mutate_create_tx
from ...services import recurring_schedule

router = APIRouter()


def _actor_fields(mutate_payload: dict) -> dict:
    return {
        "__actor_user_id": mutate_payload.get("__actor_user_id"),
        "__actor_is_admin": mutate_payload.get("__actor_is_admin"),
        "__actor_in_shared_ledger": mutate_payload.get("__actor_in_shared_ledger"),
    }


def _parse_iso(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


@router.post(
    "/ledgers/{ledger_id}/recurring-rules",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def create_recurring_rule_ep(
    ledger_id: str,
    req: WriteRecurringRuleCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    # 主帳戶(§2.9 Phase 4):account_group 不能被週期性收支拿來當帳戶用。
    for field in ("account_id", "from_account_id", "to_account_id"):
        _assert_account_not_group(db, user_id=current_user.id, account_id=getattr(req, field, None), field_name=field)
    # 需求 #14(Phase 12):非轉帳規則必須帶分類,避免生成的每期交易漏分類。
    _assert_category_required(req.tx_type, req.category_id)
    # 股票定期定額(2026-09-28,docs/STOCK_HOLDINGS_SD.md §9):kind='stock_dca'
    # 必須是 transfer(交割帳戶→投資理財帳戶)、market/symbol 必填、
    # to_account_id 必須是投資理財帳戶。跟單筆買進(create_stock_trade)一樣
    # 的限制,提前在這裡擋掉,不用等到到期生成才失敗。
    if req.kind == "stock_dca":
        if req.tx_type != "transfer":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="kind=stock_dca requires tx_type=transfer",
            )
        if not req.market or not req.symbol:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="kind=stock_dca requires market and symbol",
            )
        if not req.from_account_id or not req.to_account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="kind=stock_dca requires from_account_id and to_account_id",
            )
        if req.from_account_id == req.to_account_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="from_account_id and to_account_id must differ",
            )
        _assert_account_is_investment(
            db, user_id=current_user.id, account_id=req.to_account_id, field_name="to_account_id",
        )
    # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):跟 write/transactions.py
    # 同一套校驗 + 重算 amount,transfer 帶了任一新欄位直接 400。
    _normalize_recurring_rule_fee_discount(db=db, ledger_id=ledger.id, rule_id=None, payload=payload)
    _assert_reward_rules_valid(
        db, user_id=current_user.id, account_id=req.account_id,
        reward_rule_ids=req.reward_rule_ids or [],
    )
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        # 自動扣繳(tx_type=="transfer",2026-08-02 补):这类规则代表"到期
        # 时从指定帐户真的要扣一笔钱",提前批次生成没有意义——生成时离到期
        # 还有几个月,来源帐户到时候余额是多少现在根本不知道,没法检查够
        # 不够。改成完全不预生成,交给 credit_card_reminders 同一个 15 分钟
        # loop 里的 recurring_materializer.materialize_due_transfer_rules
        # 逐笔到期才生成 + 检查来源帐户当下余额,不够就跳过并通知(见该函式
        # docstring)。一般收支类规则(expense/income)维持原本的批次预生成,
        # 不受影响——"余额够不够"这个概念对它们本来就不适用。
        if req.tx_type == "transfer":
            rule_payload = dict(mutate_payload)
            rule_payload["generated_until_at"] = None
            next_snapshot, rule_id = create_recurring_rule(snapshot, rule_payload)
            return next_snapshot, rule_id

        occurrences, generated_until_at, fully_generated = recurring_schedule.plan_initial_generation(
            start=req.next_run_at,
            end=req.end_at,
            frequency=req.frequency,
            interval=req.interval,
            advanced_rule=req.advanced_rule_json,
        )
        rule_payload = dict(mutate_payload)
        rule_payload["generated_until_at"] = generated_until_at
        # 建立当下就把 [next_run_at, end_at] 全部生成完了 → 之后不需要视窗
        # 续产生再管这条规则,标记 enabled=False 让管理页归到"已结束"分组
        # (个别 occurrence 是否已发生是另一回事,由前端按 happened_at 过滤)。
        if fully_generated:
            rule_payload["enabled"] = False
        next_snapshot, rule_id = create_recurring_rule(snapshot, rule_payload)
        actor_fields = _actor_fields(mutate_payload)
        category_name, category_kind = _resolve_category_display(
            db, user_id=current_user.id, category_id=req.category_id,
        )
        account_name = _resolve_account_display(db, user_id=current_user.id, account_id=req.account_id)
        from_account_name = _resolve_account_display(
            db, user_id=current_user.id, account_id=req.from_account_id,
        )
        to_account_name = _resolve_account_display(
            db, user_id=current_user.id, account_id=req.to_account_id,
        )
        tag_names = _resolve_tag_names(db, user_id=current_user.id, tag_ids=req.tag_ids)
        for occ in occurrences:
            tx_payload = {
                "tx_type": req.tx_type,
                # 手續費/折扣(2026-08 使用者回饋):用 mutate_payload 裡經過
                # _normalize_recurring_rule_fee_discount 重算後的權威 amount,
                # 不能用 req.amount(前端送來的原始值,沒扣手續費/折扣)。
                "amount": mutate_payload.get("amount"),
                "happened_at": occ,
                "note": req.note,
                "merchant": req.merchant,
                "category_id": req.category_id,
                "category_name": category_name,
                "category_kind": category_kind,
                "account_id": req.account_id,
                "account_name": account_name,
                "from_account_id": req.from_account_id,
                "from_account_name": from_account_name,
                "to_account_id": req.to_account_id,
                "to_account_name": to_account_name,
                "project_id": req.project_id,
                "tag_ids": req.tag_ids,
                "tags": tag_names,
                "recurring_rule_id": rule_id,
                # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):規則固定屬性,
                # 每一期(含第一期)都要繼承,不只有「建交易當下順便設週期」
                # 那個特例才有。
                "base_amount": mutate_payload.get("base_amount"),
                "fee_amount": mutate_payload.get("fee_amount"),
                "fee_label": mutate_payload.get("fee_label"),
                "discount_amount": mutate_payload.get("discount_amount"),
                "discount_label": mutate_payload.get("discount_label"),
                "reward_rule_ids": mutate_payload.get("reward_rule_ids"),
                **actor_fields,
            }
            next_snapshot, _tx_id = _mutate_create_tx(next_snapshot, tx_payload)
        return next_snapshot, rule_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_rule_create",
        mutate=_mutate,
    )


@router.patch(
    "/ledgers/{ledger_id}/recurring-rules/{rule_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_recurring_rule_ep(
    ledger_id: str,
    rule_id: str,
    req: WriteRecurringRuleUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    for field in ("account_id", "from_account_id", "to_account_id"):
        _assert_account_not_group(db, user_id=current_user.id, account_id=payload.get(field), field_name=field)
    # 需求 #14(Phase 12):使用者主動要改 category_id 時(不管是清空還是換
    # 別的值)才檢查——維持 partial update「沒帶的欄位不動」既有語意,不會
    # 因為這條規則本來就沒分類(舊資料)而擋下跟分類無關的其它欄位更新。
    # tx_type 沒帶在這次 payload 裡就查現有規則的 tx_type 判斷是否轉帳。
    if "category_id" in payload:
        effective_tx_type = payload.get("tx_type")
        if effective_tx_type is None:
            effective_tx_type = db.scalar(
                select(ReadRecurringRuleProjection.tx_type).where(
                    ReadRecurringRuleProjection.ledger_id == ledger.id,
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            )
        _assert_category_required(effective_tx_type, payload.get("category_id"))
    # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):同 create 端點,partial
    # update 慣例(key 不出現才 no-op,顯式傳 null 才清空)。
    _normalize_recurring_rule_fee_discount(db=db, ledger_id=ledger.id, rule_id=rule_id, payload=payload)
    if "reward_rule_ids" in payload:
        effective_account_id = payload.get("account_id") if "account_id" in payload else db.scalar(
            select(ReadRecurringRuleProjection.account_sync_id).where(
                ReadRecurringRuleProjection.ledger_id == ledger.id,
                ReadRecurringRuleProjection.sync_id == rule_id,
            )
        )
        _assert_reward_rules_valid(
            db, user_id=current_user.id, account_id=effective_account_id,
            reward_rule_ids=payload.get("reward_rule_ids") or [],
        )
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_rule_update",
        mutate=lambda snapshot: (update_recurring_rule(snapshot, rule_id, mutate_payload), rule_id),
    )


@router.patch(
    "/ledgers/{ledger_id}/recurring-rules/{rule_id}/occurrences/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_recurring_occurrence_ep(
    ledger_id: str,
    rule_id: str,
    tx_id: str,
    req: WriteRecurringOccurrenceUpdateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    # 主帳戶(§2.9 Phase 4):跟 create/update_recurring_rule_ep 一致,不能把
    # account_group 容器掛到單筆 occurrence 上——這支端點原本漏了這個檢查。
    _assert_account_not_group(db, user_id=current_user.id, account_id=payload.get("account_id"), field_name="account_id")
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    # 帳戶/分類顯示名稱(2026-08-16 補):這支端點的窄欄位 schema 只有
    # account_id/category_id、沒有對應的 _name 欄位(前端也只送 id),不補上
    # 的話,單筆改帳戶/分類後 read_tx_projection 的顯示名稱欄位會停在舊值、
    # 跟新的 id 脫鉤(同 create_recurring_rule_ep 用 _resolve_account_display/
    # _resolve_category_display 補 denormalize 欄位的理由)。
    if "account_id" in payload:
        mutate_payload["account_name"] = _resolve_account_display(
            db, user_id=current_user.id, account_id=payload.get("account_id"),
        )
    if "category_id" in payload:
        category_name, category_kind = _resolve_category_display(
            db, user_id=current_user.id, category_id=payload.get("category_id"),
        )
        mutate_payload["category_name"] = category_name
        mutate_payload["category_kind"] = category_kind
    # 呼叫这个端点本身就意味着"这期要跟规则批次更新脱钩",强制标记,不暴露
    # 成可选请求字段。
    mutate_payload["recurring_occurrence_overridden"] = True
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_occurrence_update",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="upsert",
    )


@router.delete(
    "/ledgers/{ledger_id}/recurring-rules/{rule_id}/occurrences/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_recurring_occurrence_ep(
    ledger_id: str,
    rule_id: str,
    tx_id: str,
    req: WriteEntityDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    return await _commit_write_fast_tx(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_occurrence_delete",
        tx_id=tx_id,
        mutate_payload=mutate_payload,
        action="delete",
    )


@router.post(
    "/ledgers/{ledger_id}/recurring-rules/{rule_id}/update-from/{tx_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def update_recurring_rule_from_ep(
    ledger_id: str,
    rule_id: str,
    tx_id: str,
    req: WriteRecurringUpdateFromRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json", exclude_unset=True)
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    for field in ("account_id", "from_account_id", "to_account_id"):
        _assert_account_not_group(db, user_id=current_user.id, account_id=payload.get(field), field_name=field)
    # 需求 #14(Phase 12):同 update_recurring_rule_ep 的檢查——「這期以後」
    # 批次改分類時也不能把非轉帳規則的分類改成空的。
    if "category_id" in payload:
        effective_tx_type = payload.get("tx_type")
        if effective_tx_type is None:
            effective_tx_type = db.scalar(
                select(ReadRecurringRuleProjection.tx_type).where(
                    ReadRecurringRuleProjection.ledger_id == ledger.id,
                    ReadRecurringRuleProjection.sync_id == rule_id,
                )
            )
        _assert_category_required(effective_tx_type, payload.get("category_id"))
    # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):同 update 端點語意,批次
    # 套用到規則本身 + 該期以後所有未 overridden 的已生成交易。
    _normalize_recurring_rule_fee_discount(db=db, ledger_id=ledger.id, rule_id=rule_id, payload=payload)
    if "reward_rule_ids" in payload:
        effective_account_id = payload.get("account_id") if "account_id" in payload else db.scalar(
            select(ReadRecurringRuleProjection.account_sync_id).where(
                ReadRecurringRuleProjection.ledger_id == ledger.id,
                ReadRecurringRuleProjection.sync_id == rule_id,
            )
        )
        _assert_reward_rules_valid(
            db, user_id=current_user.id, account_id=effective_account_id,
            reward_rule_ids=payload.get("reward_rule_ids") or [],
        )
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        items = snapshot.get("items") or []
        anchor = next((it for it in items if it.get("syncId") == tx_id), None)
        if anchor is None:
            raise KeyError("occurrence transaction not found")
        anchor_at = _parse_iso(anchor.get("happenedAt"))
        targets = [
            it for it in items
            if it.get("recurringRuleId") == rule_id
            and not it.get("recurringOccurrenceOverridden")
            and _parse_iso(it.get("happenedAt")) >= anchor_at
        ]
        actor_fields = _actor_fields(mutate_payload)
        next_snapshot = snapshot

        rule_payload = dict(actor_fields)
        for key in (
            "tx_type", "amount", "note", "category_id", "account_id",
            "from_account_id", "to_account_id", "merchant", "project_id",
            "tag_ids", "frequency", "interval", "advanced_rule_json",
            "base_amount", "fee_amount", "fee_label", "discount_amount",
            "discount_label", "reward_rule_ids",
        ):
            if key in payload:
                rule_payload[key] = payload[key]
        if len(rule_payload) > len(actor_fields):
            next_snapshot = update_recurring_rule(next_snapshot, rule_id, rule_payload)

        tx_payload = dict(actor_fields)
        for key in (
            "tx_type", "amount", "note", "category_id", "account_id",
            "from_account_id", "to_account_id", "merchant", "project_id",
            "tag_ids", "base_amount", "fee_amount", "fee_label",
            "discount_amount", "discount_label", "reward_rule_ids",
        ):
            if key in payload:
                tx_payload[key] = payload[key]
        if "tag_ids" in payload:
            # 同 create_recurring_rule_ep:tags(展示 CSV)跟 tag_ids 是两个
            # 独立字段,一起转发,否则交易列表页显示的标签名称会跟 tag_ids 脱节。
            tx_payload["tags"] = _resolve_tag_names(
                db, user_id=current_user.id, tag_ids=payload.get("tag_ids"),
            )
        if len(tx_payload) > len(actor_fields):
            for it in targets:
                next_snapshot = update_transaction(next_snapshot, it["syncId"], tx_payload)
        return next_snapshot, rule_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_rule_update_from",
        mutate=_mutate,
    )


@router.post(
    "/ledgers/{ledger_id}/recurring-rules/{rule_id}/terminate-future",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def terminate_recurring_rule_future_ep(
    ledger_id: str,
    rule_id: str,
    req: WriteEntityDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    now = _utcnow()

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        items = snapshot.get("items") or []
        future_tx = [
            it for it in items
            if it.get("recurringRuleId") == rule_id and _parse_iso(it.get("happenedAt")) > now
        ]
        actor_fields = _actor_fields(mutate_payload)
        next_snapshot = snapshot
        for it in future_tx:
            next_snapshot = delete_transaction(next_snapshot, it["syncId"])
        next_snapshot = update_recurring_rule(
            next_snapshot, rule_id, {"enabled": False, **actor_fields}
        )
        return next_snapshot, rule_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_rule_terminate_future",
        mutate=_mutate,
    )


@router.delete(
    "/ledgers/{ledger_id}/recurring-rules/{rule_id}",
    response_model=WriteCommitMeta,
    responses=_WRITE_RESPONSES,
)
async def delete_recurring_rule_ep(
    ledger_id: str,
    rule_id: str,
    req: WriteRecurringRuleDeleteRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_id: str = Header(default="web-console", alias="X-Device-ID"),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WriteCommitMeta:
    payload = req.model_dump(mode="json")
    ledger, replay = _prepare_write(
        db=db,
        current_user=current_user,
        ledger_external_id=ledger_id,
        required_roles=_OWNER_ONLY_ROLES,
        idempotency_key=idempotency_key,
        device_id=device_id,
        method=request.method,
        path=request.url.path,
        payload=payload,
    )
    if replay:
        return replay
    mutate_payload = _payload_with_actor(payload, current_user, ledger=ledger)
    now = _utcnow()

    def _mutate(snapshot: dict) -> tuple[dict, str]:
        next_snapshot = snapshot
        if req.delete_future_occurrences:
            # 同 terminate_recurring_rule_future_ep 的篩選條件:只刪「尚未
            # 發生」的那些,已發生的交易(含使用者已手動 overridden 過的)
            # 不受影響。
            items = next_snapshot.get("items") or []
            future_tx = [
                it for it in items
                if it.get("recurringRuleId") == rule_id and _parse_iso(it.get("happenedAt")) > now
            ]
            for it in future_tx:
                next_snapshot = delete_transaction(next_snapshot, it["syncId"])
        next_snapshot = delete_recurring_rule(next_snapshot, rule_id, mutate_payload)
        return next_snapshot, rule_id

    return await _commit_write(
        request=request,
        db=db,
        current_user=current_user,
        ledger=ledger,
        base_change_id=req.base_change_id,
        request_payload=payload,
        idempotency_key=idempotency_key,
        device_id=device_id,
        audit_action="web_recurring_rule_delete",
        mutate=_mutate,
    )
