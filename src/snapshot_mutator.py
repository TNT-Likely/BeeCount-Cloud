from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .services.securities.trade_fees import stock_gross

logger = logging.getLogger(__name__)


def _new_sync_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


_LEGACY_SYNC_ID_PATTERN = re.compile(r"^(tx|acc|cat|tag)_(\d+)_([A-Za-z0-9]+)$")


def _to_iso8601(raw: object) -> str:
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc).isoformat()
    if isinstance(raw, str) and raw.strip():
        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _date_only_iso8601(raw: object) -> str:
    """借還款到期日(due_at)只存日期,不存時分 —— 先借 `_to_iso8601` 做完整
    的寬鬆時間解析(接受 datetime / 帶時分的字串 / 純日期字串),轉成 UTC
    後 truncate 到當天零點。伺服器端兜底,不完全依賴前端只送日期。"""
    parsed = datetime.fromisoformat(_to_iso8601(raw))
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc).isoformat()


def _to_float(raw: object) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return 0.0
    return 0.0


def _to_optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _to_optional_int(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None
    return None


def _to_optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


# 扩展字段映射:web/server payload(snake_case) → snapshot(camelCase 跟 mobile
# lib/data/db.dart Account 字段名对齐)。`apply` 只处理 payload 里 explicitly
# 提供的 key —— 这样 update 时不传该字段就不动它,跟旧字段(name/account_type
# /currency/initial_balance)的语义一致。
_ACCOUNT_OPTIONAL_FIELD_MAP: tuple[tuple[str, str, str], ...] = (
    # (payload_key, snapshot_key, kind)
    ("note", "note", "str"),
    ("credit_limit", "creditLimit", "float"),
    ("billing_day", "billingDay", "int"),
    ("payment_due_day", "paymentDueDay", "int"),
    ("bank_name", "bankName", "str"),
    ("card_last_four", "cardLastFour", "str"),
    # 主帳戶(合併帳單,§2.9 Phase 4):子卡指向主卡 syncId;空字串/None=解除掛靠。
    ("parent_account_id", "parentAccountId", "str"),
    # 账户隐藏(issue #240):Web create/update 请求体带 hidden(bool)时才写;
    # 不带 key → 保留原值(不冲掉已有隐藏标记,契约对齐 mobile push 的 merge
    # 缺键保留语义)。
    ("hidden", "hidden", "bool"),
    # 自動扣繳(§2.9,2026-08-04 改版):開關 + 來源帳戶 syncId。
    ("auto_pay_enabled", "autoPayEnabled", "bool"),
    ("auto_pay_from_account_id", "autoPayFromAccountId", "str"),
    # 帳戶頭像(2026-08-02 補強):空字串/None=移除頭像。
    ("avatar_cloud_file_id", "avatarCloudFileId", "str"),
    ("avatar_cloud_sha256", "avatarCloudSha256", "str"),
    # SwipeSmart 卡片對照(Phase 14):空字串/None=解除對照。
    ("swipesmart_card_id", "swipesmartCardId", "str"),
    # 納入總餘額(Phase 18):Web create/update 请求体带 include_in_total(bool)
    # 时才写;不带 key → 保留原值(不冲掉已有设置,契约对齐 hidden 的既有语义)。
    ("include_in_total", "includeInTotal", "bool"),
    # 帳戶清單拖曳排序(2026-09-05):跟 mobile Accounts.sortOrder 對齊。
    ("sort_order", "sortOrder", "int"),
)


def _assert_valid_account_parent(
    accounts: list[dict], account_id: str, parent_id: str | None, *, own_type: str | None = None,
) -> None:
    """主帳戶(合併帳單,§2.9 Phase 4,2026-08-02 改版):主帳戶是純管理概念
    (`type == "account_group"`),不是可以自己記交易的獨立帳戶 —— 使用者
    反饋原本設計把「隨便一張信用卡」拿來當主卡不對,銀行/機構本身才是
    「主帳戶」,子帳戶(不限信用卡,銀行帳戶未來也適用)掛靠的目標必須是
    這種群組類型。規則:不能指向自己;目標必須是同一使用者底下真實存在、
    且 `type == "account_group"` 的帳戶;群組帳戶自己不能再掛靠別的群組
    (不支援巢狀);不能形成循環。"""
    if parent_id is None:
        return
    if parent_id == account_id:
        raise ValueError("write validation failed: account cannot be its own parent")
    by_id = {str(row.get("syncId")): row for row in accounts}
    if parent_id not in by_id:
        raise ValueError("write validation failed: parent account not found")
    if by_id[parent_id].get("type") != "account_group":
        raise ValueError("write validation failed: parent account must be an account_group")
    resolved_own_type = own_type if own_type is not None else by_id.get(account_id, {}).get("type")
    if resolved_own_type == "account_group":
        raise ValueError("write validation failed: an account_group cannot itself have a parent (no nested groups)")
    seen = {account_id}
    cursor = parent_id
    for _ in range(len(accounts) + 1):
        if cursor in seen:
            raise ValueError("write validation failed: parent account chain forms a cycle")
        seen.add(cursor)
        next_parent = by_id.get(cursor, {}).get("parentAccountId")
        if not next_parent:
            break
        cursor = str(next_parent)


def _assert_valid_auto_pay_source(
    accounts: list[dict], account_id: str, source_id: str | None,
) -> None:
    """自動扣繳來源帳戶(§2.9,2026-08-04 改版):必須是同一使用者底下真實
    存在的帳戶,不能是這張卡自己(自己扣自己沒有意義),也不能是任何
    account_group(群組沒有自己的資金,不能拿來當扣款來源——跟
    `_assert_account_not_group` 校驗一般交易的 from_account_id 同一個
    道理)。"""
    if not source_id:
        return
    if source_id == account_id:
        raise ValueError(
            "write validation failed: auto_pay_from_account_id must differ from the card itself"
        )
    by_id = {str(row.get("syncId")): row for row in accounts}
    if source_id not in by_id:
        raise ValueError("write validation failed: auto_pay_from_account_id not found")
    if by_id[source_id].get("type") == "account_group":
        raise ValueError(
            "write validation failed: auto_pay_from_account_id cannot be an account_group"
        )


def _apply_account_optional_fields(account: dict, payload: dict) -> None:
    """payload 里如果带这些 key 就写到 snapshot,空字符串 / None 视作 null。

    update 路径调用同一函数:`payload` 不带某 key → 保留原值。带 key 但 value
    是 None / 空串 → 显式清空(对应 mobile 编辑时把 note 清掉的场景)。
    """
    for payload_key, snapshot_key, kind in _ACCOUNT_OPTIONAL_FIELD_MAP:
        if payload_key not in payload:
            continue
        raw = payload.get(payload_key)
        if kind == "float":
            account[snapshot_key] = _to_optional_float(raw)
        elif kind == "int":
            account[snapshot_key] = _to_optional_int(raw)
        elif kind == "bool":
            # hidden 是投影里的 NOT NULL 布尔列,没有"清空"语义 —— 显式带
            # key 但 value 是 None 时按 False(未隐藏)处理,不留 null。
            account[snapshot_key] = bool(raw) if raw is not None else False
        else:
            account[snapshot_key] = _to_optional_str(raw)
    # 股票持股(2026-09-28):投資理財帳戶費用設定是整包物件,不走上面的純量
    # 映射表。帶 key 且是 dict → 整包取代;帶 key 但 None/非 dict → 清空。
    if "investment_settings" in payload:
        raw_settings = payload.get("investment_settings")
        if isinstance(raw_settings, dict):
            account["investmentSettings"] = normalize_investment_settings(raw_settings)
        else:
            account.pop("investmentSettings", None)


# 投資理財帳戶費用設定(wire `investmentSettings`)允許的 key 與型別。對齊
# App lib/models/investment_settings.dart;未知 key 丟棄,避免前端塞任意資料
# 進 sync payload。
_INVESTMENT_SETTING_FLOAT_KEYS = (
    "feeRate", "feeDiscount", "feeMin", "sellTaxRate", "etfSellTaxRate", "bondEtfSellTaxRate",
    "dividendFeeFixed", "dividendFeeRate", "dividendWithholdingRate",
    "nhiSupplementRate", "nhiThreshold",
)
_INVESTMENT_SETTING_STR_KEYS = ("market", "settlementAccountId")
_INVESTMENT_SETTING_BOOL_KEYS = ("reinvestDividends", "pnlAfterSellCosts")


def normalize_investment_settings(raw: dict) -> dict:
    out: dict[str, object] = {}
    for key in _INVESTMENT_SETTING_FLOAT_KEYS:
        if key in raw:
            value = _to_optional_float(raw.get(key))
            if value is not None:
                if value < 0:
                    raise ValueError(f"write validation failed: {key} must be >= 0")
                out[key] = value
    for key in _INVESTMENT_SETTING_STR_KEYS:
        if key in raw:
            value = _to_optional_str(raw.get(key))
            if value is not None:
                out[key] = value.upper() if key == "market" else value
    for key in _INVESTMENT_SETTING_BOOL_KEYS:
        if key in raw and raw.get(key) is not None:
            out[key] = bool(raw.get(key))
    return out


def _ensure_list(snapshot: dict, key: str) -> list[dict]:
    raw = snapshot.get(key)
    if not isinstance(raw, list):
        raw = []
    out: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    snapshot[key] = out
    return out


def _ensure_sync_id(items: list[dict], prefix: str) -> None:
    for item in items:
        sync_id = item.get("syncId")
        if not isinstance(sync_id, str) or not sync_id.strip():
            item["syncId"] = _new_sync_id(prefix)


def ensure_snapshot_v2(snapshot: dict | None) -> dict:
    target = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    target["ledgerName"] = str(target.get("ledgerName") or "Untitled")
    target["currency"] = str(target.get("currency") or "CNY")

    items = _ensure_list(target, "items")
    accounts = _ensure_list(target, "accounts")
    categories = _ensure_list(target, "categories")
    tags = _ensure_list(target, "tags")

    _ensure_sync_id(items, "tx")
    _ensure_sync_id(accounts, "acc")
    _ensure_sync_id(categories, "cat")
    _ensure_sync_id(tags, "tag")

    for item in items:
        item["type"] = str(item.get("type") or "expense")
        item["amount"] = _to_float(item.get("amount"))
        item["happenedAt"] = _to_iso8601(item.get("happenedAt"))
    for account in accounts:
        account["name"] = str(account.get("name") or "").strip()
        account["type"] = str(account.get("type") or "") or None
        account["currency"] = str(account.get("currency") or "") or None
        if "initialBalance" in account:
            account["initialBalance"] = _to_float(account.get("initialBalance"))
    for category in categories:
        category["name"] = str(category.get("name") or "").strip()
        category["kind"] = str(category.get("kind") or "expense").strip()
    for tag in tags:
        tag["name"] = str(tag.get("name") or "").strip()

    target["count"] = len(items)
    return target


def _legacy_sync_id(sync_id: str) -> tuple[str, int] | None:
    match = _LEGACY_SYNC_ID_PATTERN.fullmatch(sync_id.strip())
    if match is None:
        return None
    prefix, index, _suffix = match.groups()
    return prefix, int(index)


def _find_by_sync_id(
    items: list[dict], sync_id: str, *, expected_prefix: str | None = None
) -> tuple[int, dict]:
    normalized_id = sync_id.strip()
    for idx, item in enumerate(items):
        if str(item.get("syncId")) == normalized_id:
            return idx, item

    legacy = _legacy_sync_id(normalized_id)
    if legacy is not None:
        prefix, legacy_index = legacy
        if (expected_prefix is None or prefix == expected_prefix) and 0 <= legacy_index < len(items):
            fallback_item = items[legacy_index]
            fallback_item["syncId"] = normalized_id
            return legacy_index, fallback_item
    raise KeyError("entity not found")


def _actor_user_id(payload: dict) -> str | None:
    raw = payload.get("__actor_user_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _actor_is_admin(payload: dict) -> bool:
    # 单用户隔离:admin 不再拥有"跨用户改别人账本"的权限,这个 helper 保留
    # 只是为了老代码调用点不报错,恒返回 False。
    _ = payload
    return False


def _assert_actor_can_modify(item: dict, payload: dict) -> None:
    actor_user_id = _actor_user_id(payload)
    if actor_user_id is None:
        return
    if _actor_is_admin(payload):
        return
    # 共享账本:caller 是该账本的 Owner / Editor(_TRANSACTION_WRITE_ROLES
    # 已在 endpoint 层放行)→ 可以改任何 member 的 tx / category / tag /
    # account / budget。__actor_in_shared_ledger 由 _payload_with_actor
    # 注入,基于 caller 在 LedgerMember 表的存在性 + role。
    if payload.get("__actor_in_shared_ledger") is True:
        return
    created_by = item.get("createdByUserId")
    if isinstance(created_by, str) and created_by.strip() and created_by.strip() != actor_user_id:
        raise PermissionError("write role forbidden: entity owner mismatch")


def _mark_entity_actor(item: dict, payload: dict, *, create: bool) -> None:
    actor_user_id = _actor_user_id(payload)
    if actor_user_id is None:
        return
    if create:
        item["createdByUserId"] = actor_user_id
    elif not isinstance(item.get("createdByUserId"), str) or not str(item.get("createdByUserId")).strip():
        item["createdByUserId"] = actor_user_id
    item["updatedByUserId"] = actor_user_id


def _normalize_tx_tags(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        tags = [part.strip() for part in raw.split(",") if part.strip()]
        if not tags:
            return None
        return ",".join(dict.fromkeys(tags))
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            value = str(item).strip()
            if value:
                parts.append(value)
        if not parts:
            return None
        return ",".join(dict.fromkeys(parts))
    return None


def _normalize_tx_splits(raw: object) -> list[dict] | None:
    """拆帳(§2.4):request payload 的 `splits`(snake_case dict 列表,
    `category_id`/`category_name`/`amount`/`note`)→ item 里存的 camelCase
    dict 列表。空/非法输入一律 None(= 不产生 splits key,等同没拆帳)。"""
    if not isinstance(raw, list) or not raw:
        return None
    normalized: list[dict] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        category_id = entry.get("category_id") or entry.get("categoryId")
        if not category_id:
            continue
        split_item: dict[str, object] = {
            "categoryId": str(category_id),
            "amount": _to_float(entry.get("amount")),
            "sortOrder": idx,
        }
        category_name = entry.get("category_name") or entry.get("categoryName")
        if category_name:
            split_item["categoryName"] = str(category_name)
        note = entry.get("note")
        if note:
            split_item["note"] = str(note)
        normalized.append(split_item)
    return normalized or None


def _sort_transactions(snapshot: dict) -> None:
    items = _ensure_list(snapshot, "items")
    items.sort(key=lambda item: _to_iso8601(item.get("happenedAt")), reverse=True)


def create_transaction(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    tx_type = str(payload.get("tx_type") or "expense")
    # 餘額調整(§2.10 Phase 5):第四種 tx_type,只在这里 + update_transaction
    # 放行——recurring_rule/tx_template 不该有「週期性」或「範本」餘額調整
    # 这种没有业务意义的组合,那两处的白名单保持原样不加。
    if tx_type not in {"expense", "income", "transfer", "adjustment"}:
        raise ValueError("write validation failed: invalid transaction type")

    tx_id = _new_sync_id("tx")
    item: dict[str, object] = {
        "syncId": tx_id,
        "type": tx_type,
        "amount": _to_float(payload.get("amount")),
        "happenedAt": _to_iso8601(payload.get("happened_at")),
    }
    # 交易级多币种(0018):Web 币种录入显式传入才写;不传不产生 key
    # (upsert 落 NULL → 统计 COALESCE 回退,旧行为)。
    if payload.get("currency_code") is not None:
        item["currencyCode"] = str(payload.get("currency_code")).upper()
    if payload.get("native_amount") is not None:
        item["nativeAmount"] = _to_float(payload.get("native_amount"))
    # 跨幣別轉帳(2026-08):轉入帳戶自身幣別的金額,顯式傳入才寫;不傳不產生
    # key(upsert 落 NULL → COALESCE(to_amount, amount) 回退,同幣種轉帳的
    # 舊行為)。write/_shared.py::_assert_transfer_to_amount_valid 已擋掉
    # 「轉出/轉入幣別不同卻不帶 to_amount」的情形。
    if payload.get("to_amount") is not None:
        item["toAmount"] = _to_float(payload.get("to_amount"))
    # 手續費/折扣(2026-08 使用者需求):base_amount 是使用者輸入的原始金額
    # (回饋計算權威基準),只在 write/_shared.py::_normalize_fee_discount_amount
    # 認定「這筆交易有用到這個功能」時才會出現在 payload,不傳 → 不產生 key
    # (旧行为,统计/回饋 COALESCE 回退 amount)。
    if payload.get("base_amount") is not None:
        item["baseAmount"] = _to_float(payload.get("base_amount"))
    if payload.get("fee_amount") is not None:
        item["feeAmount"] = _to_float(payload.get("fee_amount"))
    if payload.get("fee_label") is not None:
        item["feeLabel"] = str(payload.get("fee_label"))
    if payload.get("discount_amount") is not None:
        item["discountAmount"] = _to_float(payload.get("discount_amount"))
    if payload.get("discount_label") is not None:
        item["discountLabel"] = str(payload.get("discount_label"))
    if payload.get("note") is not None:
        item["note"] = str(payload.get("note"))
    if payload.get("merchant") is not None:
        item["merchant"] = str(payload.get("merchant"))
    if payload.get("category_name") is not None:
        item["categoryName"] = str(payload.get("category_name"))
    if payload.get("category_kind") is not None:
        item["categoryKind"] = str(payload.get("category_kind"))
    if payload.get("category_id") is not None:
        item["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_name") is not None:
        item["accountName"] = str(payload.get("account_name"))
    if payload.get("account_id") is not None:
        item["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_name") is not None:
        item["fromAccountName"] = str(payload.get("from_account_name"))
    if payload.get("from_account_id") is not None:
        item["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_name") is not None:
        item["toAccountName"] = str(payload.get("to_account_name"))
    if payload.get("to_account_id") is not None:
        item["toAccountId"] = str(payload.get("to_account_id"))
    tags = _normalize_tx_tags(payload.get("tags"))
    if tags is not None:
        item["tags"] = tags
    tag_ids_raw = payload.get("tag_ids")
    if isinstance(tag_ids_raw, list):
        tag_ids: list[str] = []
        for raw in tag_ids_raw:
            value = str(raw).strip()
            if value and value not in tag_ids:
                tag_ids.append(value)
        if tag_ids:
            item["tagIds"] = tag_ids
    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        item["attachments"] = attachments
    # 账单标记(.docs/transaction-flags):snapshot 用 camelCase,跟 mobile
    # serializer + projection.upsert_tx(读 excludeFromStats)对齐。create 默认 False。
    item["excludeFromStats"] = bool(payload.get("exclude_from_stats"))
    item["excludeFromBudget"] = bool(payload.get("exclude_from_budget"))
    if payload.get("refund_of_id") is not None:
        item["refundOfId"] = str(payload.get("refund_of_id"))
    if payload.get("recurring_rule_id") is not None:
        item["recurringRuleId"] = str(payload.get("recurring_rule_id"))
    if payload.get("debt_id") is not None:
        item["debtId"] = str(payload.get("debt_id"))
    if payload.get("project_id") is not None:
        item["projectId"] = str(payload.get("project_id"))
    if payload.get("deferred_posting_at") is not None:
        item["deferredPostingAt"] = _to_iso8601(payload.get("deferred_posting_at"))
    reward_rule_ids_raw = payload.get("reward_rule_ids")
    if isinstance(reward_rule_ids_raw, list):
        reward_rule_ids: list[str] = []
        for raw in reward_rule_ids_raw:
            value = str(raw).strip()
            if value and value not in reward_rule_ids:
                reward_rule_ids.append(value)
        if reward_rule_ids:
            item["rewardRuleIds"] = reward_rule_ids
    splits = _normalize_tx_splits(payload.get("splits"))
    if splits is not None:
        item["splits"] = splits
    _mark_entity_actor(item, payload, create=True)

    _ensure_list(target, "items").append(item)
    # 跳过 _sort_transactions(方案 B):snapshot 不写回,排序徒劳
    target["count"] = len(_ensure_list(target, "items"))
    return target, tx_id


def rescale_native_amount(
    old_amount: float, old_native: float, new_amount: float
) -> float:
    """L14 唯一权威实现:amount 变化时 nativeAmount 的联动规则。

    - 同币种/未折算(old_native == old_amount,隐含汇率 1)→ 跟随新 amount
    - 外币 → 按该笔隐含汇率等比缩放(old_native / old_amount * new_amount),
      保持记账时汇率不漂移
    - old_amount == 0 无法推汇率 → 退化 = 新 amount(1:1,App L11 可捞回)

    调用方:本文件 update_transaction(Web 写路径)与 sync_applier.
    _sync_native_amount_after_merge(旧 App push 路径)。App 端 sync apply 的
    「缺键退化 1:1」是有意的另一规则(旧客户端场景宁可退化让 L11 捞),不共用。
    """
    if old_amount == 0.0 or old_native == old_amount:
        return new_amount
    return old_native / old_amount * new_amount


def update_transaction(snapshot: dict, tx_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    items = _ensure_list(target, "items")
    _, item = _find_by_sync_id(items, tx_id, expected_prefix="tx")
    _assert_actor_can_modify(item, payload)

    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer", "adjustment"}:
            raise ValueError("write validation failed: invalid transaction type")
        item["type"] = tx_type
    if "amount" in payload:
        new_amount = _to_float(payload.get("amount"))
        # 交易级多币种(L14,.docs/multi-currency-ledger):item 带折算快照
        # (nativeAmount,新 App 记的交易都有)时,改 amount 必须联动,否则
        # 账本统计(读 native_amount)会一直显示旧金额。规则:
        #   同币种/未折算(old_native == old_amount)→ 跟随新 amount;
        #   外币 → 按该笔隐含汇率等比缩放(保持记账时汇率);
        #   old_amount == 0 无法推汇率 → 退化 = 新 amount(1:1)。
        # item 无该 key(旧 App 记的存量交易)→ 不产生,upsert 落 NULL,
        # 统计端 COALESCE 回退新 amount。payload 显式带 native_amount 时
        # 以传入为准(下方统一写入),跳过联动。
        if "nativeAmount" in item and payload.get("native_amount") is None:
            old_amount = _to_float(item.get("amount"))
            old_native = _to_float(item.get("nativeAmount"))
            if new_amount != old_amount:
                item["nativeAmount"] = rescale_native_amount(
                    old_amount, old_native, new_amount)
        # 跨幣別轉帳(2026-08):item 帶 toAmount(轉入帳戶幣別跟轉出不同的
        # 既有轉帳)時,改 amount(轉出金額)要照同一個隱含匯率等比縮放
        # toAmount,規則跟上面 nativeAmount 完全一致——沿用同一個
        # rescale_native_amount(舊名沒改,函式本身就是通用的 old/new 縮放,
        # 不是 native_amount 專屬)。payload 顯式帶 to_amount 時以傳入為準
        # (下方統一寫入),跳過聯動。
        if "toAmount" in item and payload.get("to_amount") is None:
            old_amount = _to_float(item.get("amount"))
            old_to = _to_float(item.get("toAmount"))
            if new_amount != old_amount:
                item["toAmount"] = rescale_native_amount(
                    old_amount, old_to, new_amount)
        item["amount"] = new_amount
    if payload.get("native_amount") is not None:
        # 显式传入优先(Web 折算录入);None = 不变。
        item["nativeAmount"] = _to_float(payload.get("native_amount"))
    if payload.get("to_amount") is not None:
        # 跨幣別轉帳(2026-08):顯式傳入優先;None = 不變。
        item["toAmount"] = _to_float(payload.get("to_amount"))
    # 手續費/折扣(2026-08 使用者需求):"key" in payload 才动作(PATCH 缺键
    # 保留既有值,同 amount/nativeAmount 惯例);显式传 None = 使用者关掉这个
    # 功能,清掉该 key(upsert 落 NULL,回饋計算/顯示 fallback 回 amount)。
    if "base_amount" in payload:
        if payload.get("base_amount") is None:
            item.pop("baseAmount", None)
        else:
            item["baseAmount"] = _to_float(payload.get("base_amount"))
    if "fee_amount" in payload:
        if payload.get("fee_amount") is None:
            item.pop("feeAmount", None)
        else:
            item["feeAmount"] = _to_float(payload.get("fee_amount"))
    if "discount_amount" in payload:
        if payload.get("discount_amount") is None:
            item.pop("discountAmount", None)
        else:
            item["discountAmount"] = _to_float(payload.get("discount_amount"))
    if payload.get("currency_code") is not None:
        item["currencyCode"] = str(payload.get("currency_code")).upper()
    if "happened_at" in payload:
        item["happenedAt"] = _to_iso8601(payload.get("happened_at"))

    mapping = {
        "note": "note",
        "merchant": "merchant",
        "fee_label": "feeLabel",
        "discount_label": "discountLabel",
        "category_name": "categoryName",
        "category_kind": "categoryKind",
        "category_id": "categoryId",
        "account_name": "accountName",
        "account_id": "accountId",
        "from_account_name": "fromAccountName",
        "from_account_id": "fromAccountId",
        "to_account_name": "toAccountName",
        "to_account_id": "toAccountId",
    }
    for req_key, snapshot_key in mapping.items():
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                item.pop(snapshot_key, None)
            else:
                item[snapshot_key] = str(value)
    if "tags" in payload:
        raw_tags = payload.get("tags")
        normalized = _normalize_tx_tags(raw_tags)
        logger.info(
            "update_transaction.tags tx_id=%s raw=%r normalized=%r",
            tx_id, raw_tags, normalized,
        )
        if normalized is None:
            item.pop("tags", None)
        else:
            item["tags"] = normalized
    else:
        logger.info("update_transaction.tags tx_id=%s 'tags' key NOT in payload", tx_id)
    if "tag_ids" in payload:
        raw = payload.get("tag_ids")
        if isinstance(raw, list):
            tag_ids: list[str] = []
            for value in raw:
                text = str(value).strip()
                if text and text not in tag_ids:
                    tag_ids.append(text)
            if tag_ids:
                item["tagIds"] = tag_ids
            else:
                item.pop("tagIds", None)
        elif raw is None:
            item.pop("tagIds", None)
    if "attachments" in payload:
        attachments = payload.get("attachments")
        if isinstance(attachments, list):
            item["attachments"] = attachments
        elif attachments is None:
            item.pop("attachments", None)
    # 账单标记(.docs/transaction-flags):web update 请求里 None = 不变(由
    # exclude_unset 的 payload 控制:不传该 key 就不进 payload)。带显式布尔
    # 才写。snapshot 用 camelCase。
    for req_key, snapshot_key in (
        ("exclude_from_stats", "excludeFromStats"),
        ("exclude_from_budget", "excludeFromBudget"),
    ):
        if req_key in payload and payload.get(req_key) is not None:
            item[snapshot_key] = bool(payload.get(req_key))
    if "refund_of_id" in payload:
        value = payload.get("refund_of_id")
        if value is None or str(value).strip() == "":
            item.pop("refundOfId", None)
        else:
            item["refundOfId"] = str(value)
    # 週期性收支(§2.12.2):單獨編輯/刪除某一期時,呼叫端強制帶
    # recurring_occurrence_overridden=True,之後 update-from / 視窗續產生都要
    # 跳過這筆,不能被批次覆蓋。
    if "recurring_occurrence_overridden" in payload:
        item["recurringOccurrenceOverridden"] = bool(
            payload.get("recurring_occurrence_overridden")
        )
    if "debt_id" in payload:
        value = payload.get("debt_id")
        if value is None or str(value).strip() == "":
            item.pop("debtId", None)
        else:
            item["debtId"] = str(value)
    if "project_id" in payload:
        value = payload.get("project_id")
        if value is None or str(value).strip() == "":
            item.pop("projectId", None)
        else:
            item["projectId"] = str(value)
    if "deferred_posting_at" in payload:
        value = payload.get("deferred_posting_at")
        if value is None:
            item.pop("deferredPostingAt", None)
        else:
            item["deferredPostingAt"] = _to_iso8601(value)
    if "reconciled_at" in payload:
        value = payload.get("reconciled_at")
        if value is None:
            item.pop("reconciledAt", None)
        else:
            item["reconciledAt"] = _to_iso8601(value)
    if "reward_rule_ids" in payload:
        raw = payload.get("reward_rule_ids")
        if isinstance(raw, list):
            reward_rule_ids: list[str] = []
            for value in raw:
                text = str(value).strip()
                if text and text not in reward_rule_ids:
                    reward_rule_ids.append(text)
            if reward_rule_ids:
                item["rewardRuleIds"] = reward_rule_ids
            else:
                item.pop("rewardRuleIds", None)
        elif raw is None:
            item.pop("rewardRuleIds", None)
    if "splits" in payload:
        splits = _normalize_tx_splits(payload.get("splits"))
        if splits is None:
            item.pop("splits", None)
        else:
            item["splits"] = splits
    _mark_entity_actor(item, payload, create=False)

    # 方案 B 后 snapshot 不写回 DB,items 排序只对 mutator 内部无意义 → 跳过(原 30ms/5k)。
    # projection 读路径走 SQL ORDER BY,顺序由 index 保证。
    target["count"] = len(items)
    return target


def delete_transaction(snapshot: dict, tx_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    items = _ensure_list(target, "items")
    idx, item = _find_by_sync_id(items, tx_id, expected_prefix="tx")
    _assert_actor_can_modify(item, payload or {})
    items.pop(idx)
    # 方案 B 后 snapshot 不写回 DB,items 排序只对 mutator 内部无意义 → 跳过(原 30ms/5k)。
    # projection 读路径走 SQL ORDER BY,顺序由 index 保证。
    target["count"] = len(items)

    # 借還款追蹤:被刪的若是某筆欠款的起點交易(originTxId),且該欠款還沒
    # 有任何還款交易(items 裡沒有任何 debtId 命中它),這筆欠款已經沒有
    # 任何交易佐證,一併從 debts 移除——跟 _shared.py::
    # _cascade_delete_orphaned_origin_debt(单笔 DELETE fast path 用的同款
    # 邏輯)保持一致,這裡是批量刪除(transactions_batch_delete.py)這條路
    # 徑用的 mutator 版本。
    # 股票持股(2026-09-28):綁著這筆交易的股票明細一併刪除,同
    # _shared.py::_cascade_delete_linked_stock_trades(单笔 DELETE fast path)。
    stock_trades = _ensure_list(target, "stockTrades")
    stock_trades[:] = [t for t in stock_trades if t.get("txId") != tx_id]

    debts = _ensure_list(target, "debts")
    for d_idx, debt in enumerate(debts):
        if not isinstance(debt, dict) or debt.get("originTxId") != tx_id:
            continue
        debt_sync_id = debt.get("syncId")
        has_repayment = any(
            isinstance(other, dict) and other.get("debtId") == debt_sync_id
            for other in items
        )
        if not has_repayment:
            debts.pop(d_idx)
        break

    return target


def _normalize_name(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("write validation failed: name is required")
    return value


def create_account(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    name = _normalize_name(payload.get("name"))
    if any(str(row.get("name", "")).strip().lower() == name.lower() for row in accounts):
        raise ValueError("write validation failed: duplicated account name")
    sync_id = _new_sync_id("acc")
    account = {
        "syncId": sync_id,
        "name": name,
        "type": str(payload.get("account_type") or "") or None,
        "currency": str(payload.get("currency") or "") or None,
        "initialBalance": _to_float(payload.get("initial_balance")),
    }
    # 扩展字段:跟 mobile lib/data/db.dart Account 表 schema 对齐(driftCamel:
    # creditLimit / billingDay / paymentDueDay / bankName / cardLastFour /
    # note)。前端 web 字段是 snake_case,这里转 camelCase 写入 snapshot。
    _apply_account_optional_fields(account, payload)
    if "parent_account_id" in payload:
        _assert_valid_account_parent(
            accounts, sync_id, _to_optional_str(account.get("parentAccountId")),
            own_type=account.get("type"),
        )
    if "auto_pay_from_account_id" in payload:
        _assert_valid_auto_pay_source(
            accounts, sync_id, _to_optional_str(account.get("autoPayFromAccountId")),
        )
    _mark_entity_actor(account, payload, create=True)
    accounts.append(account)
    return target, sync_id


def update_account(snapshot: dict, account_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    _, account = _find_by_sync_id(accounts, account_id, expected_prefix="acc")
    _assert_actor_can_modify(account, payload)
    old_name = str(account.get("name") or "").strip()

    if "name" in payload:
        new_name = _normalize_name(payload.get("name"))
        if any(
            str(row.get("syncId")) != account_id
            and str(row.get("name", "")).strip().lower() == new_name.lower()
            for row in accounts
        ):
            raise ValueError("write validation failed: duplicated account name")
        account["name"] = new_name
    if "account_type" in payload:
        value = payload.get("account_type")
        account["type"] = str(value) if value else None
    if "currency" in payload:
        value = payload.get("currency")
        account["currency"] = str(value) if value else None
    if "initial_balance" in payload:
        account["initialBalance"] = _to_float(payload.get("initial_balance"))
    _apply_account_optional_fields(account, payload)
    if "parent_account_id" in payload:
        _assert_valid_account_parent(
            accounts, account_id, _to_optional_str(account.get("parentAccountId")),
            own_type=account.get("type"),
        )
    if "auto_pay_from_account_id" in payload:
        _assert_valid_auto_pay_source(
            accounts, account_id, _to_optional_str(account.get("autoPayFromAccountId")),
        )

    new_name = str(account.get("name") or "").strip()
    if old_name and new_name and old_name != new_name:
        for tx in _ensure_list(target, "items"):
            if tx.get("accountName") == old_name:
                tx["accountName"] = new_name
            if tx.get("fromAccountName") == old_name:
                tx["fromAccountName"] = new_name
            if tx.get("toAccountName") == old_name:
                tx["toAccountName"] = new_name
    _mark_entity_actor(account, payload, create=False)
    return target


def _assert_account_has_no_structural_references(accounts: list[dict], target: dict, account_id: str) -> None:
    """帳戶級聯刪除(2026-08-05):不論交易是否要一併級聯刪除,「結構性設定」
    (週期性收支規則/分期付款/交易範本/信用卡回饋規則/自動扣繳來源帳戶)
    一律照舊擋下,使用者需先到對應功能頁面處理——這些不是單純的一次性
    交易紀錄,自動連動刪除/解除掛勾的影響面太大(例如刪掉一條還在跑的
    週期性收支規則,使用者可能不知情),交給使用者手動決定比較安全。"""
    blockers: list[str] = []

    recurring_hits = sum(
        1
        for rule in _ensure_list(target, "recurringRules")
        if account_id in (rule.get("accountId"), rule.get("fromAccountId"), rule.get("toAccountId"))
    )
    if recurring_hits:
        blockers.append(f"{recurring_hits} recurring rule(s)")

    installment_hits = sum(
        1 for plan in _ensure_list(target, "installmentPlans") if plan.get("accountId") == account_id
    )
    if installment_hits:
        blockers.append(f"{installment_hits} installment plan(s)")

    template_hits = sum(
        1
        for tpl in _ensure_list(target, "txTemplates")
        if account_id in (tpl.get("accountId"), tpl.get("fromAccountId"), tpl.get("toAccountId"))
    )
    if template_hits:
        blockers.append(f"{template_hits} transaction template(s)")

    reward_rule_hits = sum(
        1
        for rule in _ensure_list(target, "cardRewardRules")
        if account_id in (rule.get("accountId"), rule.get("rewardAccountId"))
    )
    if reward_rule_hits:
        blockers.append(f"{reward_rule_hits} card reward rule(s)")

    auto_pay_hits = sum(
        1 for row in accounts if row.get("syncId") != account_id and row.get("autoPayFromAccountId") == account_id
    )
    if auto_pay_hits:
        blockers.append(f"{auto_pay_hits} account(s) using it as auto-pay source")

    if blockers:
        raise ValueError(
            "write validation failed: account is still referenced by " + ", ".join(blockers) +
            "; remove or reassign these first"
        )


def delete_account(
    snapshot: dict, account_id: str, payload: dict | None = None, *, cascade: bool = False,
) -> dict:
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    idx, account = _find_by_sync_id(accounts, account_id, expected_prefix="acc")
    _assert_actor_can_modify(account, payload or {})
    old_name = str(account.get("name") or "").strip()
    # 主帳戶(§2.9 Phase 4):群組帳戶還有子帳戶掛靠時拒絕刪除,避免子帳戶
    # parent_account_id 變成查無此帳戶的孤儿引用(billing-summary 之類的讀
    # 路径会直接 404)。跟下面"有关联交易拒绝删除"同一个"不要 orphan"原则。
    children = sum(1 for row in accounts if str(row.get("parentAccountId") or "") == account_id)
    if children > 0:
        raise ValueError(
            "write validation failed: account_group has linked child accounts; "
            f"unlink or delete the {children} child accounts first"
        )
    _assert_account_has_no_structural_references(accounts, target, account_id)
    # 安全检查:关联交易——cascade=False(现况)拒绝删除,客户端必须先把交易
    # 改/删/迁走;cascade=True(2026-08-05 新增)时改成把这些交易一并删除,
    # 但只要其中有分期付款关联的交易(installmentPlanId 非空,比照
    # transactions_batch_delete.py 的 installment_linked guard),整个级联
    # 删除中止、不留部分删除的中间状态——分期付款是结构性设定,必须让使用者
    # 先去处理分期付款本身。
    # mobile 自己走 sync_applier 路径不经过 snapshot_mutator,这条 guard 只对
    # web write API 生效;mobile 现有行为(orphan)保留不变。
    linked_tx_ids: list[str] = []
    if old_name:
        linked_tx_ids = [
            str(tx.get("syncId"))
            for tx in _ensure_list(target, "items")
            if (
                tx.get("accountName") == old_name
                or tx.get("fromAccountName") == old_name
                or tx.get("toAccountName") == old_name
            )
        ]
    if linked_tx_ids:
        if not cascade:
            raise ValueError(
                "write validation failed: account has linked transactions; "
                f"reassign or delete the {len(linked_tx_ids)} transactions first"
            )
        installment_linked = sum(
            1
            for tx in _ensure_list(target, "items")
            if str(tx.get("syncId")) in linked_tx_ids and tx.get("installmentPlanId")
        )
        if installment_linked:
            raise ValueError(
                "write validation failed: account has "
                f"{installment_linked} installment-linked transactions; resolve the installment plan first"
            )
        linked_id_set = set(linked_tx_ids)
        items = _ensure_list(target, "items")
        target["items"] = [tx for tx in items if str(tx.get("syncId")) not in linked_id_set]
        target["count"] = len(target["items"])
    accounts.pop(idx)
    if old_name:
        for tx in _ensure_list(target, "items"):
            if tx.get("accountName") == old_name:
                tx.pop("accountName", None)
            if tx.get("fromAccountName") == old_name:
                tx.pop("fromAccountName", None)
            if tx.get("toAccountName") == old_name:
                tx.pop("toAccountName", None)
    return target


def create_category(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    name = _normalize_name(payload.get("name"))
    kind = str(payload.get("kind") or "expense").strip()
    if kind not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid category kind")
    if any(
        str(row.get("name", "")).strip().lower() == name.lower()
        and str(row.get("kind", "")).strip() == kind
        for row in categories
    ):
        raise ValueError("write validation failed: duplicated category")
    sync_id = _new_sync_id("cat")
    category = {
        "syncId": sync_id,
        "name": name,
        "kind": kind,
        "level": payload.get("level"),
        "sortOrder": payload.get("sort_order"),
        "icon": payload.get("icon"),
        "iconType": payload.get("icon_type"),
        "customIconPath": payload.get("custom_icon_path"),
        "iconCloudFileId": payload.get("icon_cloud_file_id"),
        "iconCloudSha256": payload.get("icon_cloud_sha256"),
        "parentName": payload.get("parent_name"),
        "color": payload.get("color"),
    }
    _mark_entity_actor(category, payload, create=True)
    categories.append(category)
    return target, sync_id


def update_category(snapshot: dict, category_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    _, category = _find_by_sync_id(categories, category_id, expected_prefix="cat")
    _assert_actor_can_modify(category, payload)
    old_name = str(category.get("name") or "").strip()
    old_kind = str(category.get("kind") or "").strip()

    if "name" in payload:
        category["name"] = _normalize_name(payload.get("name"))
    if "kind" in payload:
        kind = str(payload.get("kind") or "").strip()
        if kind not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid category kind")
        category["kind"] = kind
    for req_key, snapshot_key in [
        ("level", "level"),
        ("sort_order", "sortOrder"),
        ("icon", "icon"),
        ("icon_type", "iconType"),
        ("custom_icon_path", "customIconPath"),
        ("icon_cloud_file_id", "iconCloudFileId"),
        ("icon_cloud_sha256", "iconCloudSha256"),
        ("parent_name", "parentName"),
        ("color", "color"),
    ]:
        if req_key in payload:
            category[snapshot_key] = payload.get(req_key)

    new_name = str(category.get("name") or "").strip()
    new_kind = str(category.get("kind") or "").strip()
    if any(
        str(row.get("syncId")) != category_id
        and str(row.get("name", "")).strip().lower() == new_name.lower()
        and str(row.get("kind", "")).strip() == new_kind
        for row in categories
    ):
        raise ValueError("write validation failed: duplicated category")

    if old_name and old_kind and (old_name != new_name or old_kind != new_kind):
        for tx in _ensure_list(target, "items"):
            if tx.get("categoryName") == old_name and tx.get("categoryKind") == old_kind:
                tx["categoryName"] = new_name
                tx["categoryKind"] = new_kind
    _mark_entity_actor(category, payload, create=False)
    return target


def delete_category(snapshot: dict, category_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    categories = _ensure_list(target, "categories")
    idx, category = _find_by_sync_id(categories, category_id, expected_prefix="cat")
    _assert_actor_can_modify(category, payload or {})
    old_name = str(category.get("name") or "").strip()
    old_kind = str(category.get("kind") or "").strip()
    # 严格策略(跟 AccountsPage / mobile 对齐):有子分类或关联交易时拒绝删除,
    # 要求用户先迁移这些数据。比"允许删除并 orphan"安全 — 避免误删导致一堆
    # 无主交易污染 ledger。前端也有同款拦截,这里是兜底服务端校验防止旧客户
    # 端 / 直接 API 调用绕过。
    if old_name and old_kind:
        child_count = sum(
            1
            for row in categories
            if str(row.get("syncId") or "") != category_id
            and str(row.get("parentName") or "").strip() == old_name
            and str(row.get("kind") or "").strip() == old_kind
        )
        if child_count > 0:
            raise ValueError(
                f"write validation failed: category has {child_count} child categories"
            )
        tx_count = sum(
            1
            for tx in _ensure_list(target, "items")
            if tx.get("categoryName") == old_name
            and tx.get("categoryKind") == old_kind
        )
        if tx_count > 0:
            raise ValueError(
                f"write validation failed: category has {tx_count} transactions"
            )
    categories.pop(idx)
    return target


def _split_tags(raw: object) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _join_tags(tags: list[str]) -> str | None:
    if not tags:
        return None
    return ",".join(dict.fromkeys(tags))


def create_tag(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    name = _normalize_name(payload.get("name"))
    if any(str(row.get("name", "")).strip().lower() == name.lower() for row in tags):
        raise ValueError("write validation failed: duplicated tag")
    sync_id = _new_sync_id("tag")
    item = {"syncId": sync_id, "name": name, "color": payload.get("color")}
    _mark_entity_actor(item, payload, create=True)
    tags.append(item)
    return target, sync_id


def update_tag(snapshot: dict, tag_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    _, tag = _find_by_sync_id(tags, tag_id, expected_prefix="tag")
    _assert_actor_can_modify(tag, payload)
    old_name = str(tag.get("name") or "").strip()
    if "name" in payload:
        new_name = _normalize_name(payload.get("name"))
        if any(
            str(row.get("syncId")) != tag_id
            and str(row.get("name", "")).strip().lower() == new_name.lower()
            for row in tags
        ):
            raise ValueError("write validation failed: duplicated tag")
        tag["name"] = new_name
    if "color" in payload:
        tag["color"] = payload.get("color")

    new_name = str(tag.get("name") or "").strip()
    if old_name and new_name and old_name != new_name:
        for tx in _ensure_list(target, "items"):
            tx_tags = _split_tags(tx.get("tags"))
            if not tx_tags:
                continue
            updated = [new_name if tag_name == old_name else tag_name for tag_name in tx_tags]
            merged = _join_tags(updated)
            if merged is None:
                tx.pop("tags", None)
            else:
                tx["tags"] = merged
    _mark_entity_actor(tag, payload, create=False)
    return target


def delete_tag(snapshot: dict, tag_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    tags = _ensure_list(target, "tags")
    idx, tag = _find_by_sync_id(tags, tag_id, expected_prefix="tag")
    _assert_actor_can_modify(tag, payload or {})
    old_name = str(tag.get("name") or "").strip()

    # 拦截关联交易:有交易引用此 tag 时禁止删除,让用户先把标签从交易里
    # 摘掉(或删交易)再来删标签。之前是"静默把 tag 从所有引用它的 tx
    # 里抽走",数据上可恢复但用户无感知,跟 app 行为(确认对话框 + 阻止)
    # 不一致,容易误删。
    # ValueError 由路由层抓出来翻译成 4xx 响应,error message 走 i18n。
    if old_name:
        in_use = sum(
            1
            for tx in _ensure_list(target, "items")
            if old_name in _split_tags(tx.get("tags"))
        )
        if in_use > 0:
            raise ValueError(
                f"write validation failed: tag has {in_use} linked transactions"
            )

    tags.pop(idx)
    return target


# ============================================================================
# Budgets —— 跟 mobile lib/data/db.dart Budget 表对齐:type / categoryId /
# amount / period / startDay / enabled。snapshot 用 driftCamel(categoryId,
# startDay)。type='total' 在每个账本只允许一条;'category' 同一 categoryId
# 也只允许一条(对应 mobile budget_edit_page._saveBudget 的 unique check)。
# ============================================================================


def _normalize_budget_period(raw: object) -> str:
    """空 / 无效时回退到 'monthly'(跟 mobile budget_repository 的 default 一致)。"""
    s = str(raw or "").strip().lower()
    if s in ("monthly", "weekly", "yearly"):
        return s
    return "monthly"


def _normalize_budget_type(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s == "category":
        return "category"
    return "total"


def create_budget(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    budgets = _ensure_list(target, "budgets")
    btype = _normalize_budget_type(payload.get("type"))
    category_id = _to_optional_str(payload.get("category_id"))
    period = _normalize_budget_period(payload.get("period"))
    # 唯一性:total 只一条;category 按 categoryId 唯一(对齐 mobile)。
    if btype == "total":
        if any(_normalize_budget_type(row.get("type")) == "total" for row in budgets):
            raise ValueError("write validation failed: total budget already exists")
    else:
        if not category_id:
            raise ValueError("write validation failed: category budget requires category_id")
        if any(
            _normalize_budget_type(row.get("type")) == "category"
            and str(row.get("categoryId") or "") == category_id
            for row in budgets
        ):
            raise ValueError("write validation failed: category budget already exists")
    amount = _to_optional_float(payload.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("write validation failed: budget amount must be > 0")
    start_day = _to_optional_int(payload.get("start_day"))
    if start_day is None:
        start_day = 1
    if start_day < 1 or start_day > 28:
        raise ValueError("write validation failed: start_day out of range")
    sync_id = _new_sync_id("bgt")
    enabled_raw = payload.get("enabled")
    enabled = bool(enabled_raw) if enabled_raw is not None else True
    # ledgerSyncId 必须显式带,mobile _applyBudgetChange 用它解析本地 ledger id;
    # 不带则 mobile 永远 skip 这条 change。
    ledger_sync_id = _to_optional_str(target.get("ledgerSyncId")) or _to_optional_str(
        payload.get("ledger_sync_id")
    )
    budget = {
        "syncId": sync_id,
        "type": btype,
        "categoryId": category_id if btype == "category" else None,
        "amount": amount,
        "period": period,
        "startDay": start_day,
        "enabled": enabled,
    }
    if ledger_sync_id:
        budget["ledgerSyncId"] = ledger_sync_id
    _mark_entity_actor(budget, payload, create=True)
    budgets.append(budget)
    return target, sync_id


def update_budget(snapshot: dict, budget_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    budgets = _ensure_list(target, "budgets")
    _, budget = _find_by_sync_id(budgets, budget_id, expected_prefix="bgt")
    _assert_actor_can_modify(budget, payload)
    # 历史 budget(snapshot_builder 从 projection 重建已经带 ledgerSyncId,但
    # 老 SyncChange 里的 payload 可能没带)被 update 时,补齐 ledgerSyncId,
    # 否则 mobile 收到这条 update change 还是因为缺 ledgerSyncId 直接 skip。
    if "ledgerSyncId" not in budget:
        ledger_sync_id = _to_optional_str(target.get("ledgerSyncId"))
        if ledger_sync_id:
            budget["ledgerSyncId"] = ledger_sync_id
    if "amount" in payload:
        amount = _to_optional_float(payload.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("write validation failed: budget amount must be > 0")
        budget["amount"] = amount
    if "period" in payload:
        budget["period"] = _normalize_budget_period(payload.get("period"))
    if "start_day" in payload:
        start_day = _to_optional_int(payload.get("start_day"))
        if start_day is None:
            start_day = 1
        if start_day < 1 or start_day > 28:
            raise ValueError("write validation failed: start_day out of range")
        budget["startDay"] = start_day
    if "enabled" in payload:
        budget["enabled"] = bool(payload.get("enabled"))
    # 不允许改 type 和 categoryId(语义混乱:从 total 改成 category 等于
    # 删一条新建一条,UI 走删除 + 新建路径更直观)。
    _mark_entity_actor(budget, payload, create=False)
    return target


def delete_budget(snapshot: dict, budget_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    budgets = _ensure_list(target, "budgets")
    idx, budget = _find_by_sync_id(budgets, budget_id, expected_prefix="bgt")
    _assert_actor_can_modify(budget, payload or {})
    budgets.pop(idx)
    return target


# ============================================================================
# Recurring rules(§2.2)—— 跟 budget 同款 boilerplate。snapshot 用
# driftCamel:txType/categoryId/accountId/fromAccountId/toAccountId/frequency/
# interval/nextRunAt/endAt/enabled。
# ============================================================================


def next_run_from(now: datetime, frequency: str, interval: int) -> datetime:
    """算下一次 next_run_at:frequency 单位 * interval。月/年用日历推进(处理
    月末溢出:借用 `add_months` 风格,31 号 + 1 月 → 2 月最后一天,不进 3 月)。"""
    interval = max(1, int(interval or 1))
    if frequency == "daily":
        return now + timedelta(days=interval)
    if frequency == "weekly":
        return now + timedelta(weeks=interval)
    if frequency == "yearly":
        return add_months(now, interval * 12)
    return add_months(now, interval)  # monthly(默认兜底)


def add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    import calendar

    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def create_recurring_rule(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "recurringRules")
    tx_type = str(payload.get("tx_type") or "expense")
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid transaction type")
    amount = _to_optional_float(payload.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("write validation failed: amount must be > 0")
    frequency = str(payload.get("frequency") or "monthly")
    if frequency not in {"daily", "weekly", "monthly", "yearly"}:
        raise ValueError("write validation failed: invalid frequency")
    next_run_at = payload.get("next_run_at")
    if next_run_at is None:
        raise ValueError("write validation failed: next_run_at is required")
    sync_id = _new_sync_id("rec")
    rule: dict[str, object] = {
        "syncId": sync_id,
        "txType": tx_type,
        "amount": amount,
        "frequency": frequency,
        "interval": _to_optional_int(payload.get("interval")) or 1,
        "nextRunAt": _to_iso8601(next_run_at),
        "enabled": bool(payload.get("enabled")) if payload.get("enabled") is not None else True,
    }
    if payload.get("note") is not None:
        rule["note"] = str(payload.get("note"))
    if payload.get("category_id") is not None:
        rule["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_id") is not None:
        rule["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_id") is not None:
        rule["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_id") is not None:
        rule["toAccountId"] = str(payload.get("to_account_id"))
    if payload.get("merchant") is not None:
        rule["merchant"] = str(payload.get("merchant"))
    if payload.get("project_id") is not None:
        rule["projectId"] = str(payload.get("project_id"))
    tag_ids_raw = payload.get("tag_ids")
    if isinstance(tag_ids_raw, list):
        tag_ids: list[str] = []
        for raw in tag_ids_raw:
            value = str(raw).strip()
            if value and value not in tag_ids:
                tag_ids.append(value)
        if tag_ids:
            rule["tagIds"] = tag_ids
    if payload.get("end_at") is not None:
        rule["endAt"] = _to_iso8601(payload.get("end_at"))
    # Phase 1.5(§2.12.2):进阶规则(snapshot 层保持嵌套 dict,序列化成 JSON
    # 字串是 projection.py upsert 时才做的事,跟 attachments/tagIds 同一套
    # "snapshot 存原生结构,projection 落库时才 json.dumps"惯例)+ 视窗生成
    # 进度,建规则的 write endpoint 用
    # recurring_schedule.plan_initial_generation 算出这次批次生成到哪里,
    # 一起传进来跟规则本身一次建好,不用建完再补一次 update。
    if payload.get("advanced_rule_json") is not None:
        rule["advancedRuleJson"] = payload.get("advanced_rule_json")
    if payload.get("generated_until_at") is not None:
        rule["generatedUntilAt"] = _to_iso8601(payload.get("generated_until_at"))
    # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):同 create_transaction 同名
    # 欄位寫法,規則固定屬性,每一期自動產生的 occurrence 都要繼承。
    if payload.get("base_amount") is not None:
        rule["baseAmount"] = _to_float(payload.get("base_amount"))
    if payload.get("fee_amount") is not None:
        rule["feeAmount"] = _to_float(payload.get("fee_amount"))
    if payload.get("fee_label") is not None:
        rule["feeLabel"] = str(payload.get("fee_label"))
    if payload.get("discount_amount") is not None:
        rule["discountAmount"] = _to_float(payload.get("discount_amount"))
    if payload.get("discount_label") is not None:
        rule["discountLabel"] = str(payload.get("discount_label"))
    reward_rule_ids_raw = payload.get("reward_rule_ids")
    if isinstance(reward_rule_ids_raw, list):
        reward_rule_ids: list[str] = []
        for raw in reward_rule_ids_raw:
            value = str(raw).strip()
            if value and value not in reward_rule_ids:
                reward_rule_ids.append(value)
        if reward_rule_ids:
            rule["rewardRuleIds"] = reward_rule_ids
    # 股票定期定額(2026-09-28,docs/STOCK_HOLDINGS_SD.md §9):kind='stock_dca'
    # 必須搭配 tx_type='transfer'(router 層已驗證),這裡只写入欄位,不重複
    # 尚驗證。kind='general'(預設值)不寫入欄位,同既有規則一致不帶 kind key。
    kind = str(payload.get("kind") or "general")
    if kind not in {"general", "stock_dca"}:
        raise ValueError("write validation failed: invalid kind")
    if kind != "general":
        rule["kind"] = kind
    if payload.get("market") is not None:
        rule["market"] = str(payload.get("market")).upper()
    if payload.get("symbol") is not None:
        rule["symbol"] = str(payload.get("symbol")).upper()
    if payload.get("security_name") is not None:
        rule["securityName"] = str(payload.get("security_name"))
    if payload.get("stock_fee_rate") is not None:
        rule["stockFeeRate"] = _to_float(payload.get("stock_fee_rate"))
    if payload.get("stock_fee_min") is not None:
        rule["stockFeeMin"] = _to_float(payload.get("stock_fee_min"))
    _mark_entity_actor(rule, payload, create=True)
    rules.append(rule)
    return target, sync_id


def update_recurring_rule(snapshot: dict, rule_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "recurringRules")
    _, rule = _find_by_sync_id(rules, rule_id, expected_prefix="rec")
    _assert_actor_can_modify(rule, payload)

    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid transaction type")
        rule["txType"] = tx_type
    if "amount" in payload:
        amount = _to_optional_float(payload.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("write validation failed: amount must be > 0")
        rule["amount"] = amount
    if "frequency" in payload:
        frequency = str(payload.get("frequency") or "")
        if frequency not in {"daily", "weekly", "monthly", "yearly"}:
            raise ValueError("write validation failed: invalid frequency")
        rule["frequency"] = frequency
    if "interval" in payload:
        rule["interval"] = _to_optional_int(payload.get("interval")) or 1
    if "next_run_at" in payload and payload.get("next_run_at") is not None:
        rule["nextRunAt"] = _to_iso8601(payload.get("next_run_at"))
    # 2026-09-29:只有 routers/write/recurring_rules.py 的「到期才逐筆生成」
    # 規則改下次執行時間時會帶這個 key(= None,從新的 next_run_at 重新起算),
    # 不在 WriteRecurringRuleUpdateRequest 暴露。
    if "__reset_generated_until_at" in payload:
        rule.pop("generatedUntilAt", None)
    if "end_at" in payload:
        value = payload.get("end_at")
        if value is None:
            rule.pop("endAt", None)
        else:
            rule["endAt"] = _to_iso8601(value)
    if "enabled" in payload:
        rule["enabled"] = bool(payload.get("enabled"))
    for req_key, snapshot_key in (
        ("note", "note"),
        ("category_id", "categoryId"),
        ("account_id", "accountId"),
        ("from_account_id", "fromAccountId"),
        ("to_account_id", "toAccountId"),
        ("merchant", "merchant"),
        ("project_id", "projectId"),
        ("fee_label", "feeLabel"),
        ("discount_label", "discountLabel"),
    ):
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                rule.pop(snapshot_key, None)
            else:
                rule[snapshot_key] = str(value)
    # 手續費/折扣/信用卡回饋(2026-08 使用者回饋):同 update_transaction 同名
    # 欄位寫法,"key" in payload 才動作(PATCH 缺鍵保留既有值),顯式傳 None =
    # 清掉該分量。
    if "base_amount" in payload:
        if payload.get("base_amount") is None:
            rule.pop("baseAmount", None)
        else:
            rule["baseAmount"] = _to_float(payload.get("base_amount"))
    if "fee_amount" in payload:
        if payload.get("fee_amount") is None:
            rule.pop("feeAmount", None)
        else:
            rule["feeAmount"] = _to_float(payload.get("fee_amount"))
    if "discount_amount" in payload:
        if payload.get("discount_amount") is None:
            rule.pop("discountAmount", None)
        else:
            rule["discountAmount"] = _to_float(payload.get("discount_amount"))
    if "reward_rule_ids" in payload:
        raw = payload.get("reward_rule_ids")
        if isinstance(raw, list):
            reward_rule_ids: list[str] = []
            for value in raw:
                text = str(value).strip()
                if text and text not in reward_rule_ids:
                    reward_rule_ids.append(text)
            if reward_rule_ids:
                rule["rewardRuleIds"] = reward_rule_ids
            else:
                rule.pop("rewardRuleIds", None)
        elif raw is None:
            rule.pop("rewardRuleIds", None)
    # 股票定期定額:手續費覆寫可改(kind/market/symbol/securityName 建立後
    # 鎖定,不在 WriteRecurringRuleUpdateRequest 暴露,不會出現在這裡的 payload)。
    if "stock_fee_rate" in payload:
        value = payload.get("stock_fee_rate")
        if value is None:
            rule.pop("stockFeeRate", None)
        else:
            rule["stockFeeRate"] = _to_float(value)
    if "stock_fee_min" in payload:
        value = payload.get("stock_fee_min")
        if value is None:
            rule.pop("stockFeeMin", None)
        else:
            rule["stockFeeMin"] = _to_float(value)
    if "tag_ids" in payload:
        raw = payload.get("tag_ids")
        if isinstance(raw, list):
            tag_ids: list[str] = []
            for value in raw:
                text = str(value).strip()
                if text and text not in tag_ids:
                    tag_ids.append(text)
            if tag_ids:
                rule["tagIds"] = tag_ids
            else:
                rule.pop("tagIds", None)
        elif raw is None:
            rule.pop("tagIds", None)
    if "advanced_rule_json" in payload:
        value = payload.get("advanced_rule_json")
        if value is None:
            rule.pop("advancedRuleJson", None)
        else:
            rule["advancedRuleJson"] = value
    if "generated_until_at" in payload:
        value = payload.get("generated_until_at")
        if value is None:
            rule.pop("generatedUntilAt", None)
        else:
            rule["generatedUntilAt"] = _to_iso8601(value)
    _mark_entity_actor(rule, payload, create=False)
    return target


def delete_recurring_rule(snapshot: dict, rule_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "recurringRules")
    idx, rule = _find_by_sync_id(rules, rule_id, expected_prefix="rec")
    _assert_actor_can_modify(rule, payload or {})
    rules.pop(idx)
    return target


# ============================================================================
# Installment plans(§2.3 / Phase 1.5 修正版 §2.12.1)—— 建计画时算好攤還
# 参数(repaymentMethod/interestPeriod/interestRate/roundAmounts/
# remainderPosition/gracePeriodMonths)存进这个实体,但**不**在这里跑攤還
# 演算法、也不生成任何 tx/period ——那需要 services.installment_amortization
# + 同时写 read_tx_projection + read_installment_period_projection,
# snapshot_mutator 只管这一个实体的字段。全部期数的生成放在 write endpoint
# 里跟计画一起提交(同一个 _commit_write 事务),见 routers/write/
# installment_plans.py。periodAmount/nextPeriodAt/paidPeriods 这三个字段
# Phase 1.5 之后只是历史相容占位(不再被任何排程更新),真正的每期明细以
# read_installment_period_projection 为准,读路径(snapshot_builder.build /
# read/ledgers.py)从 period 行即时算出展示用的 paidPeriods/nextPeriodAt,
# 不读这里存的值。
# ============================================================================


def create_installment_plan(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    plans = _ensure_list(target, "installmentPlans")
    total_amount = _to_optional_float(payload.get("total_amount"))
    if total_amount is None or total_amount <= 0:
        raise ValueError("write validation failed: total_amount must be > 0")
    periods = _to_optional_int(payload.get("periods"))
    if periods is None or periods < 1:
        raise ValueError("write validation failed: periods must be >= 1")
    first_period_at = payload.get("first_period_at")
    if first_period_at is None:
        raise ValueError("write validation failed: first_period_at is required")
    first_dt = datetime.fromisoformat(_to_iso8601(first_period_at))

    repayment_method = str(payload.get("repayment_method") or "equal_principal")
    if repayment_method not in {"equal_installment", "equal_principal", "fixed_interest"}:
        raise ValueError("write validation failed: invalid repayment_method")
    interest_period = str(payload.get("interest_period") or "monthly")
    if interest_period not in {"monthly", "daily"}:
        raise ValueError("write validation failed: invalid interest_period")
    interest_rate = _to_optional_float(payload.get("interest_rate"))
    if interest_rate is None:
        interest_rate = 0.0
    if interest_rate < 0:
        raise ValueError("write validation failed: interest_rate must be >= 0")
    round_amounts = (
        bool(payload.get("round_amounts")) if payload.get("round_amounts") is not None else True
    )
    remainder_position = str(payload.get("remainder_position") or "last")
    if remainder_position not in {"first", "last"}:
        raise ValueError("write validation failed: invalid remainder_position")
    grace_period_months = _to_optional_int(payload.get("grace_period_months"))
    if grace_period_months is None:
        grace_period_months = 0
    if not (0 <= grace_period_months < periods):
        raise ValueError(
            "write validation failed: grace_period_months must be >= 0 and < periods"
        )

    period_amount = round(total_amount / periods, 2)
    sync_id = _new_sync_id("ins")
    plan: dict[str, object] = {
        "syncId": sync_id,
        "totalAmount": total_amount,
        "periods": periods,
        "periodAmount": period_amount,
        "firstPeriodAt": _to_iso8601(first_period_at),
        "nextPeriodAt": _to_iso8601(add_months(first_dt, 1)),
        "paidPeriods": 1,
        "status": "active",
        "repaymentMethod": repayment_method,
        "interestPeriod": interest_period,
        "interestRate": interest_rate,
        "roundAmounts": round_amounts,
        "remainderPosition": remainder_position,
        "gracePeriodMonths": grace_period_months,
    }
    if payload.get("account_id") is not None:
        plan["accountId"] = str(payload.get("account_id"))
    if payload.get("category_id") is not None:
        plan["categoryId"] = str(payload.get("category_id"))
    if payload.get("note") is not None:
        plan["note"] = str(payload.get("note"))
    if payload.get("offset_breakdown"):
        plan["offsetBreakdownJson"] = json.dumps(payload.get("offset_breakdown"))
    _mark_entity_actor(plan, payload, create=True)
    plans.append(plan)
    return target, sync_id


def update_installment_plan(snapshot: dict, plan_id: str, payload: dict) -> dict:
    """`note`/`status` 是 plain PATCH 端点(`WriteInstallmentPlanUpdateRequest`)
    唯一暴露的字段 —— 金额/期数/攤還参数不给直接改,语义混乱(等同删了重建)。
    `interest_rate`/`repayment_method` 两个 key 只有 §2.12.1 的
    `rebalance-from` 端点内部会传(它自己的 request schema
    `WriteInstallmentRebalanceRequest` 不长这样,是 endpoint 手工组 payload
    调这里),用来把新利率/攤還方式记录回 plan 实体供之后展示/再次 rebalance
    用,不是让 client 能直接打这个字段。"""
    target = ensure_snapshot_v2(snapshot)
    plans = _ensure_list(target, "installmentPlans")
    _, plan = _find_by_sync_id(plans, plan_id, expected_prefix="ins")
    _assert_actor_can_modify(plan, payload)
    if "note" in payload:
        value = payload.get("note")
        if value is None or str(value).strip() == "":
            plan.pop("note", None)
        else:
            plan["note"] = str(value)
    if "status" in payload and payload.get("status") is not None:
        status = str(payload.get("status"))
        if status not in {"active", "settled", "terminated"}:
            raise ValueError("write validation failed: invalid status")
        plan["status"] = status
    if "interest_rate" in payload and payload.get("interest_rate") is not None:
        plan["interestRate"] = _to_float(payload.get("interest_rate"))
    if "repayment_method" in payload and payload.get("repayment_method") is not None:
        method = str(payload.get("repayment_method"))
        if method not in {"equal_installment", "equal_principal", "fixed_interest"}:
            raise ValueError("write validation failed: invalid repayment_method")
        plan["repaymentMethod"] = method
    _mark_entity_actor(plan, payload, create=False)
    return target


def delete_installment_plan(snapshot: dict, plan_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    plans = _ensure_list(target, "installmentPlans")
    idx, plan = _find_by_sync_id(plans, plan_id, expected_prefix="ins")
    _assert_actor_can_modify(plan, payload or {})
    plans.pop(idx)
    return target


# ============================================================================
# Installment periods(§2.12.1 Phase 1.5 新增)—— 每期本金/利息/合计明细。
# 只由 server 端写入口(建计画 / rebalance-from / early-repay-principal /
# payoff / terminate-future)生成,不接受 client 直接 POST 单笔 period,但
# 仍走完整 sync entity 六步(需要跨装置可见)。跟 budget/recurring_rule 同款
# boilerplate,snapshot 用 driftCamel:planId/periodNo/dueAt/principalAmount/
# interestAmount/totalAmount/status/txId。
# ============================================================================


def create_installment_period(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    periods_list = _ensure_list(target, "installmentPeriods")
    plan_id = payload.get("plan_id")
    if not plan_id:
        raise ValueError("write validation failed: plan_id is required")
    period_no = _to_optional_int(payload.get("period_no"))
    if period_no is None or period_no < 1:
        raise ValueError("write validation failed: period_no must be >= 1")
    due_at = payload.get("due_at")
    if due_at is None:
        raise ValueError("write validation failed: due_at is required")
    sync_id = _new_sync_id("insp")
    period: dict[str, object] = {
        "syncId": sync_id,
        "planId": str(plan_id),
        "periodNo": period_no,
        "dueAt": _to_iso8601(due_at),
        "principalAmount": _to_float(payload.get("principal_amount")),
        "interestAmount": _to_float(payload.get("interest_amount")),
        "totalAmount": _to_float(payload.get("total_amount")),
        "status": str(payload.get("status") or "generated"),
    }
    if payload.get("tx_id") is not None:
        period["txId"] = str(payload.get("tx_id"))
    _mark_entity_actor(period, payload, create=True)
    periods_list.append(period)
    return target, sync_id


def update_installment_period(snapshot: dict, period_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    periods_list = _ensure_list(target, "installmentPeriods")
    _, period = _find_by_sync_id(periods_list, period_id, expected_prefix="insp")
    _assert_actor_can_modify(period, payload)
    if "due_at" in payload and payload.get("due_at") is not None:
        period["dueAt"] = _to_iso8601(payload.get("due_at"))
    if "principal_amount" in payload and payload.get("principal_amount") is not None:
        period["principalAmount"] = _to_float(payload.get("principal_amount"))
    if "interest_amount" in payload and payload.get("interest_amount") is not None:
        period["interestAmount"] = _to_float(payload.get("interest_amount"))
    if "total_amount" in payload and payload.get("total_amount") is not None:
        period["totalAmount"] = _to_float(payload.get("total_amount"))
    if "status" in payload and payload.get("status") is not None:
        status = str(payload.get("status"))
        if status not in {"pending", "generated", "overridden", "refunded"}:
            raise ValueError("write validation failed: invalid status")
        period["status"] = status
    if "tx_id" in payload and payload.get("tx_id") is not None:
        period["txId"] = str(payload.get("tx_id"))
    _mark_entity_actor(period, payload, create=False)
    return target


def delete_installment_period(snapshot: dict, period_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    periods_list = _ensure_list(target, "installmentPeriods")
    idx, period = _find_by_sync_id(periods_list, period_id, expected_prefix="insp")
    _assert_actor_can_modify(period, payload or {})
    periods_list.pop(idx)
    return target


# ============================================================================
# 借還款追蹤(§2.5 MOZE_FEATURE_GAP_SD.md Phase 3)—— 跟 budget 同款
# boilerplate。`principalAmount` 建立后不可改(见 ReadDebtProjection
# docstring),PATCH 只暴露 counterpartyName/dueAt/note。remaining_amount/
# status 不是这个实体的字段,读路径从反查交易 derive。
# ============================================================================


def create_debt(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    direction = str(payload.get("direction") or "")
    if direction not in {"payable", "receivable"}:
        raise ValueError("write validation failed: invalid direction")
    counterparty_name = _normalize_name(payload.get("counterparty_name"))
    principal_amount = _to_optional_float(payload.get("principal_amount"))
    if principal_amount is None or principal_amount <= 0:
        raise ValueError("write validation failed: principal_amount must be > 0")
    sync_id = _new_sync_id("debt")
    debt: dict[str, object] = {
        "syncId": sync_id,
        "direction": direction,
        "counterpartyName": counterparty_name,
        "principalAmount": principal_amount,
        "excludedFromTotal": bool(payload.get("excluded_from_total", False)),
    }
    if payload.get("due_at") is not None:
        debt["dueAt"] = _date_only_iso8601(payload.get("due_at"))
    if payload.get("note") is not None:
        debt["note"] = str(payload.get("note"))
    if payload.get("category_id") is not None:
        debt["categoryId"] = str(payload.get("category_id"))
    # originTxId 只在 mobile 建立欠款連帶起點交易時帶入,web 建立不帶這欄
    # (web 沒有「起點交易」這個概念)——建立後不可改,見 update_debt。
    if payload.get("origin_tx_id") is not None:
        debt["originTxId"] = str(payload.get("origin_tx_id"))
    _mark_entity_actor(debt, payload, create=True)
    debts.append(debt)
    return target, sync_id


def update_debt(snapshot: dict, debt_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    _, debt = _find_by_sync_id(debts, debt_id, expected_prefix="debt")
    _assert_actor_can_modify(debt, payload)
    if "counterparty_name" in payload:
        debt["counterpartyName"] = _normalize_name(payload.get("counterparty_name"))
    if "due_at" in payload:
        value = payload.get("due_at")
        if value is None:
            debt.pop("dueAt", None)
        else:
            debt["dueAt"] = _date_only_iso8601(value)
    if "note" in payload:
        value = payload.get("note")
        if value is None or str(value).strip() == "":
            debt.pop("note", None)
        else:
            debt["note"] = str(value)
    if "closed_at" in payload:
        value = payload.get("closed_at")
        if value is None:
            debt.pop("closedAt", None)
        else:
            debt["closedAt"] = _to_iso8601(value)
    if "category_id" in payload:
        value = payload.get("category_id")
        if value is None:
            debt.pop("categoryId", None)
        else:
            debt["categoryId"] = str(value)
    if "excluded_from_total" in payload:
        debt["excludedFromTotal"] = bool(payload.get("excluded_from_total"))
    _mark_entity_actor(debt, payload, create=False)
    return target


def rename_debt_counterparty(snapshot: dict, payload: dict) -> tuple[dict, list[str]]:
    """對象改名(§5.4 對象管理,對齐 Moze「改名連動該對象所有記錄」)——跟
    [update_debt] 不同,這裡一次改同一帳本下所有 counterpartyName ==
    old_counterparty_name 的欠款,不是只改一筆。回傳(snapshot,受影響的
    debt syncId 列表)——找不到任何符合的記錄時回傳空列表,由呼叫端
    (router)決定要不要當成 400。"""
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    old_name = str(payload.get("old_counterparty_name") or "").strip()
    if not old_name:
        raise ValueError("write validation failed: old_counterparty_name is required")
    new_name = _normalize_name(payload.get("new_counterparty_name"))
    renamed_ids: list[str] = []
    for debt in debts:
        if not isinstance(debt, dict) or debt.get("counterpartyName") != old_name:
            continue
        _assert_actor_can_modify(debt, payload)
        debt["counterpartyName"] = new_name
        _mark_entity_actor(debt, payload, create=False)
        sync_id = debt.get("syncId")
        if isinstance(sync_id, str):
            renamed_ids.append(sync_id)
    return target, renamed_ids


def delete_debt(snapshot: dict, debt_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    debts = _ensure_list(target, "debts")
    idx, debt = _find_by_sync_id(debts, debt_id, expected_prefix="debt")
    _assert_actor_can_modify(debt, payload or {})
    debts.pop(idx)
    return target


# ============================================================================
# 專案(Phase 13,docs/PH13_PROJECT_SD.md)—— 跟 budget/debt 同款 boilerplate。
# 花費彙總不落庫,读路径从 read_tx_projection.project_sync_id 反查交易即时
# 汇总算出(见 ReadProjectProjection docstring)。
# ============================================================================


_PROJECT_PERIOD_TYPES = {"fixed", "monthly", "yearly"}


def create_project(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    projects = _ensure_list(target, "projects")
    name = _normalize_name(payload.get("name"))
    if not name:
        raise ValueError("write validation failed: project name is required")
    period_type = str(payload.get("period_type") or "monthly")
    if period_type not in _PROJECT_PERIOD_TYPES:
        raise ValueError("write validation failed: invalid period_type")
    period_start = payload.get("period_start")
    period_end = payload.get("period_end")
    if period_type == "fixed" and (period_start is None or period_end is None):
        raise ValueError(
            "write validation failed: fixed period_type requires period_start and period_end"
        )
    budget_amount = _to_optional_float(payload.get("budget_amount"))
    if budget_amount is not None and budget_amount <= 0:
        raise ValueError("write validation failed: budget_amount must be > 0")
    sync_id = _new_sync_id("proj")
    project: dict[str, object] = {
        "syncId": sync_id,
        "name": name,
        "periodType": period_type,
        "carryoverEnabled": bool(payload.get("carryover_enabled")),
        "visibleOnHome": bool(payload.get("visible_on_home")) if payload.get("visible_on_home") is not None else True,
        "enabled": bool(payload.get("enabled")) if payload.get("enabled") is not None else True,
        "sortOrder": _to_optional_int(payload.get("sort_order")) or 0,
        "incomeIncludedInBudget": bool(payload.get("income_included_in_budget")),
        "dailyBudgetEnabled": bool(payload.get("daily_budget_enabled")),
    }
    if payload.get("icon") is not None:
        project["icon"] = str(payload.get("icon"))
    if budget_amount is not None:
        project["budgetAmount"] = budget_amount
    if period_start is not None:
        project["periodStart"] = _date_only_iso8601(period_start)
    if period_end is not None:
        project["periodEnd"] = _date_only_iso8601(period_end)
    if payload.get("daily_budget_mode") is not None:
        project["dailyBudgetMode"] = str(payload.get("daily_budget_mode"))
    if payload.get("reminder_threshold_percent") is not None:
        reminder_threshold_percent = _to_optional_int(payload.get("reminder_threshold_percent"))
        if reminder_threshold_percent is None or not (1 <= reminder_threshold_percent <= 200):
            raise ValueError("write validation failed: reminder_threshold_percent must be in [1, 200]")
        project["reminderThresholdPercent"] = reminder_threshold_percent
    _mark_entity_actor(project, payload, create=True)
    projects.append(project)
    return target, sync_id


def update_project(snapshot: dict, project_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    projects = _ensure_list(target, "projects")
    _, project = _find_by_sync_id(projects, project_id, expected_prefix="proj")
    _assert_actor_can_modify(project, payload)

    if "name" in payload:
        name = _normalize_name(payload.get("name"))
        if not name:
            raise ValueError("write validation failed: project name is required")
        project["name"] = name
    if "icon" in payload:
        value = payload.get("icon")
        if value is None or str(value).strip() == "":
            project.pop("icon", None)
        else:
            project["icon"] = str(value)
    if "budget_amount" in payload:
        value = payload.get("budget_amount")
        if value is None:
            project.pop("budgetAmount", None)
        else:
            budget_amount = _to_optional_float(value)
            if budget_amount is None or budget_amount <= 0:
                raise ValueError("write validation failed: budget_amount must be > 0")
            project["budgetAmount"] = budget_amount
    # period_type 变更时不强制立刻带 period_start/period_end(fixed 需要
    # 两者才有意义,交给前端表单一起送;这里只做落库,不重複 create 那层的
    # 强制校验 —— PATCH 通常是逐欄位调整,硬卡在这里反而卡死"先切 fixed
    # 再补日期"这种分两步操作的合理场景)。
    if "period_type" in payload:
        period_type = str(payload.get("period_type") or "")
        if period_type not in _PROJECT_PERIOD_TYPES:
            raise ValueError("write validation failed: invalid period_type")
        project["periodType"] = period_type
    if "period_start" in payload:
        value = payload.get("period_start")
        if value is None:
            project.pop("periodStart", None)
        else:
            project["periodStart"] = _date_only_iso8601(value)
    if "period_end" in payload:
        value = payload.get("period_end")
        if value is None:
            project.pop("periodEnd", None)
        else:
            project["periodEnd"] = _date_only_iso8601(value)
    if "carryover_enabled" in payload:
        project["carryoverEnabled"] = bool(payload.get("carryover_enabled"))
    if "visible_on_home" in payload:
        project["visibleOnHome"] = bool(payload.get("visible_on_home"))
    if "enabled" in payload:
        project["enabled"] = bool(payload.get("enabled"))
    if "sort_order" in payload:
        project["sortOrder"] = _to_optional_int(payload.get("sort_order")) or 0
    if "income_included_in_budget" in payload:
        project["incomeIncludedInBudget"] = bool(payload.get("income_included_in_budget"))
    if "daily_budget_enabled" in payload:
        project["dailyBudgetEnabled"] = bool(payload.get("daily_budget_enabled"))
    if "daily_budget_mode" in payload:
        value = payload.get("daily_budget_mode")
        if value is None:
            project.pop("dailyBudgetMode", None)
        else:
            project["dailyBudgetMode"] = str(value)
    if "reminder_threshold_percent" in payload:
        value = payload.get("reminder_threshold_percent")
        if value is None:
            project.pop("reminderThresholdPercent", None)
        else:
            reminder_threshold_percent = _to_optional_int(value)
            if reminder_threshold_percent is None or not (1 <= reminder_threshold_percent <= 200):
                raise ValueError("write validation failed: reminder_threshold_percent must be in [1, 200]")
            project["reminderThresholdPercent"] = reminder_threshold_percent
    _mark_entity_actor(project, payload, create=False)
    return target


def delete_project(snapshot: dict, project_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    projects = _ensure_list(target, "projects")
    idx, project = _find_by_sync_id(projects, project_id, expected_prefix="proj")
    _assert_actor_can_modify(project, payload or {})
    projects.pop(idx)
    return target


_PROJECT_CATEGORY_BUDGET_MODES = {"fixed", "percentage"}


def create_project_category_budget(snapshot: dict, project_id: str, payload: dict) -> tuple[dict, str]:
    """專案分類子預算(docs/2026-09-06-project-category-budget-period-switch-
    design.md §2.2/§5/§7.2)。`project_id`/`category_id` 的存在性 + 一級分類
    校驗在 write router 層做(`_assert_project_ref_exists`/
    `_assert_top_level_category_exists`),這裡只管資料本身的形狀。"""
    target = ensure_snapshot_v2(snapshot)
    rows = _ensure_list(target, "projectCategoryBudgets")
    category_id = payload.get("category_id")
    if not category_id:
        raise ValueError("write validation failed: category_id is required")
    if any(
        r.get("projectId") == project_id and r.get("categoryId") == category_id
        for r in rows
    ):
        raise ValueError(
            "write validation failed: category already has a budget allocation for this project"
        )
    mode = str(payload.get("mode") or "fixed")
    if mode not in _PROJECT_CATEGORY_BUDGET_MODES:
        raise ValueError("write validation failed: invalid mode")
    fixed_amount = _to_optional_float(payload.get("fixed_amount"))
    percentage = _to_optional_float(payload.get("percentage"))
    if mode == "fixed" and (fixed_amount is None or fixed_amount <= 0):
        raise ValueError("write validation failed: fixed_amount required for fixed mode")
    if mode == "percentage" and (percentage is None or not (0 < percentage <= 100)):
        raise ValueError("write validation failed: percentage required for percentage mode (0,100]")
    sync_id = _new_sync_id("pcb")
    row: dict[str, object] = {
        "syncId": sync_id,
        "projectId": project_id,
        "categoryId": str(category_id),
        "mode": mode,
        "carryoverEnabled": bool(payload.get("carryover_enabled")),
        "sortOrder": _to_optional_int(payload.get("sort_order")) or 0,
    }
    if fixed_amount is not None:
        row["fixedAmount"] = fixed_amount
    if percentage is not None:
        row["percentage"] = percentage
    _mark_entity_actor(row, payload, create=True)
    rows.append(row)
    return target, sync_id


def update_project_category_budget(snapshot: dict, budget_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rows = _ensure_list(target, "projectCategoryBudgets")
    _, row = _find_by_sync_id(rows, budget_id, expected_prefix="pcb")
    _assert_actor_can_modify(row, payload)
    if "mode" in payload:
        mode = str(payload.get("mode") or "")
        if mode not in _PROJECT_CATEGORY_BUDGET_MODES:
            raise ValueError("write validation failed: invalid mode")
        row["mode"] = mode
    if "fixed_amount" in payload:
        value = payload.get("fixed_amount")
        if value is None:
            row.pop("fixedAmount", None)
        else:
            fixed_amount = _to_optional_float(value)
            if fixed_amount is None or fixed_amount <= 0:
                raise ValueError("write validation failed: fixed_amount must be > 0")
            row["fixedAmount"] = fixed_amount
    if "percentage" in payload:
        value = payload.get("percentage")
        if value is None:
            row.pop("percentage", None)
        else:
            percentage = _to_optional_float(value)
            if percentage is None or not (0 < percentage <= 100):
                raise ValueError("write validation failed: percentage must be in (0,100]")
            row["percentage"] = percentage
    mode_now = str(row.get("mode") or "fixed")
    if mode_now == "fixed" and row.get("fixedAmount") is None:
        raise ValueError("write validation failed: fixed mode requires fixed_amount")
    if mode_now == "percentage" and row.get("percentage") is None:
        raise ValueError("write validation failed: percentage mode requires percentage")
    if "carryover_enabled" in payload:
        row["carryoverEnabled"] = bool(payload.get("carryover_enabled"))
    if "sort_order" in payload:
        row["sortOrder"] = _to_optional_int(payload.get("sort_order")) or 0
    _mark_entity_actor(row, payload, create=False)
    return target


def delete_project_category_budget(snapshot: dict, budget_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rows = _ensure_list(target, "projectCategoryBudgets")
    idx, row = _find_by_sync_id(rows, budget_id, expected_prefix="pcb")
    _assert_actor_can_modify(row, payload or {})
    rows.pop(idx)
    return target


# ============================================================================
# 交易範本(§2.7 MOZE_FEATURE_GAP_SD.md Phase 3)—— 跟 budget/tag 同款
# boilerplate,唯一多一步是 `POST .../apply` 端点(routers/write/
# tx_templates.py)另外调 `create_transaction` 把範本内容套成一笔新交易,
# 不在这个模块处理。
# ============================================================================


def create_tx_template(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    templates = _ensure_list(target, "txTemplates")
    name = _normalize_name(payload.get("name"))
    tx_type = str(payload.get("tx_type") or "expense")
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError("write validation failed: invalid transaction type")
    amount = _to_optional_float(payload.get("amount"))
    if amount is None or amount <= 0:
        raise ValueError("write validation failed: amount must be > 0")
    sync_id = _new_sync_id("tpl")
    template: dict[str, object] = {
        "syncId": sync_id,
        "name": name,
        "txType": tx_type,
        "amount": amount,
        "sortOrder": _to_optional_int(payload.get("sort_order")) or 0,
    }
    if payload.get("note") is not None:
        template["note"] = str(payload.get("note"))
    if payload.get("category_id") is not None:
        template["categoryId"] = str(payload.get("category_id"))
    if payload.get("account_id") is not None:
        template["accountId"] = str(payload.get("account_id"))
    if payload.get("from_account_id") is not None:
        template["fromAccountId"] = str(payload.get("from_account_id"))
    if payload.get("to_account_id") is not None:
        template["toAccountId"] = str(payload.get("to_account_id"))
    tag_ids_raw = payload.get("tag_ids")
    if isinstance(tag_ids_raw, list):
        tag_ids = [str(v).strip() for v in tag_ids_raw if str(v).strip()]
        if tag_ids:
            template["tagIds"] = tag_ids
    _mark_entity_actor(template, payload, create=True)
    templates.append(template)
    return target, sync_id


def update_tx_template(snapshot: dict, template_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    templates = _ensure_list(target, "txTemplates")
    _, template = _find_by_sync_id(templates, template_id, expected_prefix="tpl")
    _assert_actor_can_modify(template, payload)
    if "name" in payload:
        template["name"] = _normalize_name(payload.get("name"))
    if "tx_type" in payload:
        tx_type = str(payload.get("tx_type") or "")
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError("write validation failed: invalid transaction type")
        template["txType"] = tx_type
    if "amount" in payload:
        amount = _to_optional_float(payload.get("amount"))
        if amount is None or amount <= 0:
            raise ValueError("write validation failed: amount must be > 0")
        template["amount"] = amount
    if "sort_order" in payload and payload.get("sort_order") is not None:
        template["sortOrder"] = _to_optional_int(payload.get("sort_order")) or 0
    for req_key, snapshot_key in (
        ("note", "note"),
        ("category_id", "categoryId"),
        ("account_id", "accountId"),
        ("from_account_id", "fromAccountId"),
        ("to_account_id", "toAccountId"),
    ):
        if req_key in payload:
            value = payload.get(req_key)
            if value is None or str(value).strip() == "":
                template.pop(snapshot_key, None)
            else:
                template[snapshot_key] = str(value)
    if "tag_ids" in payload:
        raw = payload.get("tag_ids")
        if isinstance(raw, list):
            tag_ids = [str(v).strip() for v in raw if str(v).strip()]
            if tag_ids:
                template["tagIds"] = tag_ids
            else:
                template.pop("tagIds", None)
        elif raw is None:
            template.pop("tagIds", None)
    _mark_entity_actor(template, payload, create=False)
    return target


def delete_tx_template(snapshot: dict, template_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    templates = _ensure_list(target, "txTemplates")
    idx, template = _find_by_sync_id(templates, template_id, expected_prefix="tpl")
    _assert_actor_can_modify(template, payload or {})
    templates.pop(idx)
    return target


# ============================================================================
# 信用卡紅利回饋規則(§2.9.5 Phase 4.5 MOZE_FEATURE_GAP_SD.md)—— user-global,
# 存在 snapshot["cardRewardRules"](跟 accounts/categories/tags 同款,即使
# 挂在某个 ledger 的 snapshot 里,底层数据是 user-global,见
# ReadCardRewardRuleProjection docstring)。回饋金額不落庫,計算邏輯在
# services/card_rewards.py。
# ============================================================================

_CARD_REWARD_RATE_TYPES = {"percentage", "fixed_amount"}
# Phase 8 #4(2026-08 使用者反饋):新增 "keep"(保留小數,不取整),對齊
# Moze「單筆保留小數、總額才取整」的兩段式設計。單筆(rounding)/總額
# (total_rounding)共用同一組合法值。
_CARD_REWARD_ROUNDINGS = {"floor", "round", "ceil", "keep"}
_CARD_REWARD_CALC_BASES = {"transaction_date", "settlement_date"}
_CARD_REWARD_INTERVALS = {"billing_cycle", "calendar_month"}
_CARD_REWARD_SETTLEMENT_TYPES = {
    "immediate_after_tx", "after_posting_date", "period_end", "manual",
}


def _assert_valid_reward_account(accounts: list[dict], reward_account_id: str | None) -> None:
    """信用卡紅利回饋目的帳戶(§2.9.5.4):必須是同一使用者底下真實存在的
    帳戶,可以是這張卡自己(最常見用例——U Bear/Cathay 直接折抵當期帳單,
    跟 `_assert_valid_auto_pay_source` 不同,這裡**不**擋"不能是自己"),
    不能是 account_group(群組沒有自己的資金,不能拿來當回饋目的地)。"""
    if not reward_account_id:
        return
    by_id = {str(row.get("syncId")): row for row in accounts}
    if reward_account_id not in by_id:
        raise ValueError("write validation failed: reward_account_id not found")
    if by_id[reward_account_id].get("type") == "account_group":
        raise ValueError(
            "write validation failed: reward_account_id cannot be an account_group"
        )


def _assert_valid_settlement_day_of_month(day: int | None) -> None:
    """Phase 8 #15(2026-08 使用者反饋):週期結束後一次結算可設定回饋入帳日
    (當月/次月...第 N 天)。限制在 1~28 之內,比照多數帳務系統的保守作法
    避免月底日期溢出(2 月沒有 29~31 號)。"""
    if day is None:
        return
    if not (1 <= day <= 28):
        raise ValueError(
            "write validation failed: settlement_day_of_month must be between 1 and 28"
        )


def create_card_reward_rule(snapshot: dict, payload: dict) -> tuple[dict, str]:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "cardRewardRules")
    accounts = _ensure_list(target, "accounts")
    account_id = _to_optional_str(payload.get("account_id"))
    if not account_id:
        raise ValueError("write validation failed: account_id is required")
    label = _normalize_name(payload.get("label"))
    rate_type = str(payload.get("rate_type") or "percentage")
    if rate_type not in _CARD_REWARD_RATE_TYPES:
        raise ValueError("write validation failed: invalid rate_type")
    rate_value = _to_optional_float(payload.get("rate_value"))
    if rate_value is None or rate_value <= 0:
        raise ValueError("write validation failed: rate_value must be > 0")
    rounding = str(payload.get("rounding") or "round")
    if rounding not in _CARD_REWARD_ROUNDINGS:
        raise ValueError("write validation failed: invalid rounding")
    total_rounding = str(payload.get("total_rounding") or "round")
    if total_rounding not in _CARD_REWARD_ROUNDINGS:
        raise ValueError("write validation failed: invalid total_rounding")
    calc_basis = str(payload.get("calc_basis") or "transaction_date")
    if calc_basis not in _CARD_REWARD_CALC_BASES:
        raise ValueError("write validation failed: invalid calc_basis")
    interval = str(payload.get("interval") or "billing_cycle")
    if interval not in _CARD_REWARD_INTERVALS:
        raise ValueError("write validation failed: invalid interval")
    settlement_type = str(payload.get("settlement_type") or "manual")
    if settlement_type not in _CARD_REWARD_SETTLEMENT_TYPES:
        raise ValueError("write validation failed: invalid settlement_type")
    settlement_days = _to_optional_int(payload.get("settlement_days"))
    if settlement_type in ("immediate_after_tx", "after_posting_date"):
        if settlement_days is None:
            raise ValueError(
                "write validation failed: settlement_days is required for this settlement_type"
            )
    else:
        settlement_days = None
    settlement_month_offset = _to_optional_int(payload.get("settlement_month_offset"))
    settlement_day_of_month = _to_optional_int(payload.get("settlement_day_of_month"))
    if settlement_type != "period_end":
        settlement_month_offset = None
        settlement_day_of_month = None
    else:
        _assert_valid_settlement_day_of_month(settlement_day_of_month)
    reward_account_id = _to_optional_str(payload.get("reward_account_id"))
    if settlement_type != "manual":
        if not reward_account_id:
            raise ValueError(
                "write validation failed: reward_account_id is required for this settlement_type"
            )
        _assert_valid_reward_account(accounts, reward_account_id)
    else:
        reward_account_id = None

    sync_id = _new_sync_id("crr")
    rule: dict[str, object] = {
        "syncId": sync_id,
        "accountId": account_id,
        "label": label,
        "rateType": rate_type,
        "rateValue": rate_value,
        "rounding": rounding,
        "totalRounding": total_rounding,
        "calcBasis": calc_basis,
        "interval": interval,
        "settlementType": settlement_type,
        "enabled": bool(payload.get("enabled", True)),
    }
    if settlement_days is not None:
        rule["settlementDays"] = settlement_days
    if settlement_month_offset is not None:
        rule["settlementMonthOffset"] = settlement_month_offset
    if settlement_day_of_month is not None:
        rule["settlementDayOfMonth"] = settlement_day_of_month
    if reward_account_id:
        rule["rewardAccountId"] = reward_account_id
    category_ids = payload.get("category_ids")
    if isinstance(category_ids, list) and category_ids:
        rule["categoryIds"] = [str(c) for c in category_ids]
    if payload.get("min_spend_threshold") is not None:
        rule["minSpendThreshold"] = _to_optional_float(payload.get("min_spend_threshold"))
    if payload.get("min_tx_amount") is not None:
        rule["minTxAmount"] = _to_optional_float(payload.get("min_tx_amount"))
    if payload.get("cap_amount") is not None:
        rule["capAmount"] = _to_optional_float(payload.get("cap_amount"))
    if payload.get("cap_shared_key"):
        rule["capSharedKey"] = str(payload.get("cap_shared_key"))
    if payload.get("starts_at") is not None:
        rule["startsAt"] = _to_iso8601(payload.get("starts_at"))
    if payload.get("ends_at") is not None:
        rule["endsAt"] = _to_iso8601(payload.get("ends_at"))
    if payload.get("note") is not None:
        rule["note"] = str(payload.get("note"))
    _mark_entity_actor(rule, payload, create=True)
    rules.append(rule)
    return target, sync_id


def update_card_reward_rule(snapshot: dict, rule_id: str, payload: dict) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "cardRewardRules")
    accounts = _ensure_list(target, "accounts")
    _, rule = _find_by_sync_id(rules, rule_id, expected_prefix="crr")
    _assert_actor_can_modify(rule, payload)
    if "label" in payload:
        rule["label"] = _normalize_name(payload.get("label"))
    if "category_ids" in payload:
        value = payload.get("category_ids")
        if isinstance(value, list) and value:
            rule["categoryIds"] = [str(c) for c in value]
        else:
            rule.pop("categoryIds", None)
    if "rate_type" in payload:
        value = str(payload.get("rate_type") or "")
        if value not in _CARD_REWARD_RATE_TYPES:
            raise ValueError("write validation failed: invalid rate_type")
        rule["rateType"] = value
    if "rate_value" in payload:
        value = _to_optional_float(payload.get("rate_value"))
        if value is None or value <= 0:
            raise ValueError("write validation failed: rate_value must be > 0")
        rule["rateValue"] = value
    if "rounding" in payload:
        value = str(payload.get("rounding") or "")
        if value not in _CARD_REWARD_ROUNDINGS:
            raise ValueError("write validation failed: invalid rounding")
        rule["rounding"] = value
    if "total_rounding" in payload:
        value = str(payload.get("total_rounding") or "")
        if value not in _CARD_REWARD_ROUNDINGS:
            raise ValueError("write validation failed: invalid total_rounding")
        rule["totalRounding"] = value
    if "calc_basis" in payload:
        value = str(payload.get("calc_basis") or "")
        if value not in _CARD_REWARD_CALC_BASES:
            raise ValueError("write validation failed: invalid calc_basis")
        rule["calcBasis"] = value
    if "interval" in payload:
        value = str(payload.get("interval") or "")
        if value not in _CARD_REWARD_INTERVALS:
            raise ValueError("write validation failed: invalid interval")
        rule["interval"] = value
    if "min_spend_threshold" in payload:
        value = payload.get("min_spend_threshold")
        if value is None:
            rule.pop("minSpendThreshold", None)
        else:
            rule["minSpendThreshold"] = _to_optional_float(value)
    if "min_tx_amount" in payload:
        value = payload.get("min_tx_amount")
        if value is None:
            rule.pop("minTxAmount", None)
        else:
            rule["minTxAmount"] = _to_optional_float(value)
    if "cap_amount" in payload:
        value = payload.get("cap_amount")
        if value is None:
            rule.pop("capAmount", None)
        else:
            rule["capAmount"] = _to_optional_float(value)
    if "cap_shared_key" in payload:
        value = payload.get("cap_shared_key")
        if value is None or str(value).strip() == "":
            rule.pop("capSharedKey", None)
        else:
            rule["capSharedKey"] = str(value)
    if "starts_at" in payload:
        value = payload.get("starts_at")
        if value is None:
            rule.pop("startsAt", None)
        else:
            rule["startsAt"] = _to_iso8601(value)
    if "ends_at" in payload:
        value = payload.get("ends_at")
        if value is None:
            rule.pop("endsAt", None)
        else:
            rule["endsAt"] = _to_iso8601(value)
    if "settlement_type" in payload:
        value = str(payload.get("settlement_type") or "")
        if value not in _CARD_REWARD_SETTLEMENT_TYPES:
            raise ValueError("write validation failed: invalid settlement_type")
        rule["settlementType"] = value
        if value not in ("immediate_after_tx", "after_posting_date"):
            rule.pop("settlementDays", None)
        if value != "period_end":
            rule.pop("settlementMonthOffset", None)
            rule.pop("settlementDayOfMonth", None)
    if "settlement_days" in payload:
        value = payload.get("settlement_days")
        if value is None:
            rule.pop("settlementDays", None)
        else:
            rule["settlementDays"] = _to_optional_int(value)
    if "settlement_month_offset" in payload:
        value = payload.get("settlement_month_offset")
        if value is None:
            rule.pop("settlementMonthOffset", None)
        else:
            rule["settlementMonthOffset"] = _to_optional_int(value)
    if "settlement_day_of_month" in payload:
        value = payload.get("settlement_day_of_month")
        if value is None:
            rule.pop("settlementDayOfMonth", None)
        else:
            parsed_day = _to_optional_int(value)
            _assert_valid_settlement_day_of_month(parsed_day)
            rule["settlementDayOfMonth"] = parsed_day
    if "reward_account_id" in payload:
        value = payload.get("reward_account_id")
        if value is None:
            rule.pop("rewardAccountId", None)
        else:
            rule["rewardAccountId"] = str(value)
    if "note" in payload:
        value = payload.get("note")
        if value is None or str(value).strip() == "":
            rule.pop("note", None)
        else:
            rule["note"] = str(value)
    if "enabled" in payload:
        rule["enabled"] = bool(payload.get("enabled"))

    # 結算欄位互相依賴,必須在套用完上面所有 partial-update 分支、拿到
    # 合併後的最終狀態才驗證一致性(比照 §2.4 拆帳 _validate_tx_splits 同
    # 一個「改一半欄位也要重新校驗完整狀態」的理由)。
    final_settlement_type = str(rule.get("settlementType") or "manual")
    if final_settlement_type not in _CARD_REWARD_SETTLEMENT_TYPES:
        raise ValueError("write validation failed: invalid settlement_type")
    if final_settlement_type in ("immediate_after_tx", "after_posting_date"):
        if rule.get("settlementDays") is None:
            raise ValueError(
                "write validation failed: settlement_days is required for this settlement_type"
            )
    if final_settlement_type != "manual":
        final_reward_account_id = _to_optional_str(rule.get("rewardAccountId"))
        if not final_reward_account_id:
            raise ValueError(
                "write validation failed: reward_account_id is required for this settlement_type"
            )
        _assert_valid_reward_account(accounts, final_reward_account_id)

    _mark_entity_actor(rule, payload, create=False)
    return target


def delete_card_reward_rule(snapshot: dict, rule_id: str, payload: dict | None = None) -> dict:
    target = ensure_snapshot_v2(snapshot)
    rules = _ensure_list(target, "cardRewardRules")
    idx, rule = _find_by_sync_id(rules, rule_id, expected_prefix="crr")
    _assert_actor_can_modify(rule, payload or {})
    rules.pop(idx)
    return target


# ============================================================================
# 股票交易明細(2026-09-28,docs/STOCK_HOLDINGS_SD.md)。
#
# 「買股票就是一筆轉帳」:buy/sell 同時建立一筆 transfer 交易(交割帳戶 ⇄
# 投資理財帳戶)+ 一筆 stock_trade(txId 指回那筆交易);opening(期初持股)/
# stock_dividend(配股)沒有現金流動,只建 stock_trade。交易欄位的換算規則
# 集中在 `stock_trade_tx_fields`,App 端 lib/services/investment/
# stock_trade_tx_mapper.dart 是同一套規則的 Dart 版本,改一邊要改另一邊。
# ============================================================================

STOCK_TRADE_TYPES = {"buy", "sell", "opening", "stock_dividend", "cash_dividend", "reinvest"}
# 需要交割帳戶 + 產生轉帳交易的類型。
STOCK_TRADE_CASH_TYPES = {"buy", "sell"}
# 產生 income 交易的類型(Phase 2 股利):cash_dividend 入交割帳戶,reinvest
# 入投資理財帳戶本身(股利直接換成股數,帳戶餘額 = 成本跟著增加)。
STOCK_TRADE_INCOME_TYPES = {"cash_dividend", "reinvest"}
# web 可建的類型(待確認股利確認時也走同一套 create_stock_trade)。
STOCK_TRADE_WEB_CREATABLE_TYPES = {"buy", "sell", "opening", "stock_dividend", "cash_dividend", "reinvest"}
# 股利 income 交易的分類(router 用 card_rewards.ensure_dividend_category 建好後
# 透過這兩個 payload key 傳進來)。
DIVIDEND_CATEGORY_ID_KEY = "__dividend_category_id"
DIVIDEND_CATEGORY_NAME_KEY = "__dividend_category_name"
# 入帳帳戶幣別 ≠ 帳本本位幣時,1 單位帳戶幣別折多少本位幣(router 算好傳入),
# 用來補 income 交易的 currencyCode/nativeAmount(App pull 缺鍵會 1:1 退化)。
TX_FX_RATE_KEY = "__tx_fx_rate_to_base"
TX_FX_CURRENCY_KEY = "__tx_fx_currency"

# stock_trade_tx_fields 會寫入/清除的交易欄位,update 時先全部清掉再重寫。
_STOCK_TX_MANAGED_KEYS = ("amount", "toAmount", "feeAmount", "feeLabel", "discountAmount", "discountLabel")


def _round_money(value: float) -> float:
    return round(value + 0.0, 8)


def stock_trade_amount(
    trade_type: str, shares: float, price: float, fee: float, tax: float, currency: str | None = None,
) -> float:
    """以證券幣別計的現金影響(正數),見 ReadStockTradeProjection docstring。

    成交價金依幣別取整(`trade_fees.stock_gross`:台幣無條件捨去),0050 買 50 股
    @97.45、手續費 6 → 4,872 + 6 = 4,878,同券商對帳單。"""
    gross = stock_gross(shares, price, currency)
    if trade_type in ("buy", "opening", "reinvest"):
        return _round_money(gross + fee)
    if trade_type in ("sell", "cash_dividend"):
        return _round_money(gross - fee - tax)
    return 0.0


def stock_trade_tx_fields(
    *,
    trade_type: str,
    shares: float,
    price: float,
    fee: float,
    tax: float,
    security_currency: str | None,
    settlement_currency: str | None,
    settlement_amount: float | None,
) -> dict[str, float]:
    """回傳轉帳交易的金額欄位(snapshot camelCase key)。

    同幣別:
      buy  → amount = 股數×價格,feeAmount = 手續費(轉出端多扣)
      sell → amount = 股數×價格,discountAmount = 手續費+交易稅(轉入端少收)
    跨幣別(例:台幣交割戶買美股):必須給 settlement_amount(交割帳戶實際
    扣款/入帳的金額,已含手續費),手續費/稅併進證券端金額,不另外拆:
      buy  → amount = settlement_amount(交割幣),toAmount = 股數×價格+手續費
      sell → amount = 股數×價格−手續費−稅(證券幣),toAmount = settlement_amount
    """
    gross = stock_gross(shares, price, security_currency)
    same_currency = (
        not security_currency
        or not settlement_currency
        or security_currency.upper() == settlement_currency.upper()
    )
    out: dict[str, float] = {}
    if trade_type == "buy":
        if same_currency:
            out["amount"] = gross
            if fee > 0:
                out["feeAmount"] = _round_money(fee)
        else:
            if settlement_amount is None or settlement_amount <= 0:
                raise ValueError(
                    "write validation failed: settlement_amount is required when "
                    "settlement account currency differs from security currency"
                )
            out["amount"] = _round_money(settlement_amount)
            out["toAmount"] = _round_money(gross + fee)
    elif trade_type == "sell":
        if same_currency:
            out["amount"] = gross
            if fee + tax > 0:
                out["discountAmount"] = _round_money(fee + tax)
        else:
            if settlement_amount is None or settlement_amount <= 0:
                raise ValueError(
                    "write validation failed: settlement_amount is required when "
                    "settlement account currency differs from security currency"
                )
            out["amount"] = _round_money(gross - fee - tax)
            out["toAmount"] = _round_money(settlement_amount)
    return out


def _stock_trade_default_note(trade_type: str, symbol: str, name: str | None, shares: float) -> str:
    label = {"buy": "買進", "sell": "賣出", "cash_dividend": "股利", "reinvest": "股利再投入"}.get(
        trade_type, trade_type
    )
    shares_text = f"{shares:g}"
    parts = [label, symbol]
    if name:
        parts.append(name)
    parts.append(f"{shares_text}股")
    return " ".join(parts)


def _account_by_id(accounts: list[dict], account_id: str | None) -> dict | None:
    if not account_id:
        return None
    for acc in accounts:
        if str(acc.get("syncId")) == account_id:
            return acc
    return None


def _validate_stock_numbers(shares: float | None, price: float | None, fee: float, tax: float, *, trade_type: str) -> None:
    if shares is None or shares <= 0:
        raise ValueError("write validation failed: shares must be > 0")
    if trade_type != "stock_dividend" and (price is None or price < 0):
        raise ValueError("write validation failed: price must be >= 0")
    if fee < 0 or tax < 0:
        raise ValueError("write validation failed: fee/tax must be >= 0")


def _apply_stock_tx(
    target: dict,
    *,
    trade: dict,
    tx: dict | None,
    investment_account: dict,
    settlement_account: dict | None,
    settlement_amount: float | None,
    payload: dict,
) -> tuple[dict, str | None]:
    """依 trade 目前的欄位建立/更新綁定的轉帳交易,回傳 (snapshot, 交易 syncId)。

    注意 `create_transaction` 內部會 deepcopy snapshot,新建交易時必須改用它
    回傳的 snapshot,之後對 stockTrades 的修改也要落在那份上。"""
    trade_type = str(trade.get("tradeType"))
    if trade_type in STOCK_TRADE_INCOME_TYPES:
        return _apply_dividend_tx(
            target, trade=trade, tx=tx, investment_account=investment_account,
            settlement_account=settlement_account, settlement_amount=settlement_amount, payload=payload,
        )
    if trade_type not in STOCK_TRADE_CASH_TYPES:
        return target, None
    if settlement_account is None:
        raise ValueError("write validation failed: settlement_account_id is required for buy/sell")
    fields = stock_trade_tx_fields(
        trade_type=trade_type,
        shares=float(trade.get("shares") or 0),
        price=float(trade.get("price") or 0),
        fee=float(trade.get("fee") or 0),
        tax=float(trade.get("tax") or 0),
        security_currency=trade.get("currency"),
        settlement_currency=settlement_account.get("currency"),
        settlement_amount=settlement_amount,
    )
    if trade_type == "buy":
        from_acc, to_acc = settlement_account, investment_account
    else:
        from_acc, to_acc = investment_account, settlement_account
    note = trade.get("note") or _stock_trade_default_note(
        trade_type, str(trade.get("symbol") or ""), trade.get("securityName"), float(trade.get("shares") or 0),
    )
    if tx is None:
        tx_payload = {
            "tx_type": "transfer",
            "amount": fields["amount"],
            "happened_at": trade.get("tradeDate"),
            "note": note,
            "from_account_id": from_acc.get("syncId"),
            "from_account_name": from_acc.get("name"),
            "to_account_id": to_acc.get("syncId"),
            "to_account_name": to_acc.get("name"),
            "to_amount": fields.get("toAmount"),
            "fee_amount": fields.get("feeAmount"),
            "discount_amount": fields.get("discountAmount"),
        }
        for key in ("__actor_user_id", "__actor_is_admin", "__actor_in_shared_ledger"):
            if key in payload:
                tx_payload[key] = payload[key]
        return create_transaction(target, tx_payload)
    for key in _STOCK_TX_MANAGED_KEYS:
        tx.pop(key, None)
    tx.update(fields)
    tx["happenedAt"] = trade.get("tradeDate")
    tx["note"] = note
    tx["fromAccountId"] = from_acc.get("syncId")
    tx["fromAccountName"] = from_acc.get("name")
    tx["toAccountId"] = to_acc.get("syncId")
    tx["toAccountName"] = to_acc.get("name")
    _mark_entity_actor(tx, payload, create=False)
    return target, str(tx.get("syncId"))


def _apply_dividend_tx(
    target: dict,
    *,
    trade: dict,
    tx: dict | None,
    investment_account: dict,
    settlement_account: dict | None,
    settlement_amount: float | None,
    payload: dict,
) -> tuple[dict, str | None]:
    """cash_dividend / reinvest 綁定的 income 交易。

    - cash_dividend:入 `settlement_account`(股利入帳帳戶),金額 = 實收
      (trade.amount = 股數×每股股利−手續費−稅)。
    - reinvest:入投資理財帳戶本身,金額 = 再投入成本(股數×價格+手續費)。
    入帳帳戶幣別跟證券幣別不同時(例:美股股利換成台幣入帳)必須給
    settlement_amount(入帳帳戶實際收到的金額)。App 端
    `stock_trade_tx_mapper.dart` 同規則。"""
    trade_type = str(trade.get("tradeType"))
    account = investment_account if trade_type == "reinvest" else settlement_account
    if account is None:
        raise ValueError("write validation failed: settlement_account_id is required for cash_dividend")
    if account.get("type") == "account_group":
        raise ValueError("write validation failed: settlement account cannot be an account_group")
    security_currency = _to_optional_str(trade.get("currency"))
    account_currency = _to_optional_str(account.get("currency"))
    same_currency = (
        not security_currency or not account_currency
        or security_currency.upper() == account_currency.upper()
    )
    if same_currency:
        # 交易金額是錢,四捨五入到分(再投入成本 = 股數×價格 可能有很多位小數)。
        amount = round(float(trade.get("amount") or 0) + 0.0, 2)
    else:
        if settlement_amount is None or settlement_amount <= 0:
            raise ValueError(
                "write validation failed: settlement_amount is required when "
                "the receiving account currency differs from security currency"
            )
        amount = _round_money(settlement_amount)
    if amount <= 0:
        raise ValueError("write validation failed: dividend amount must be > 0")
    note = trade.get("note") or _stock_trade_default_note(
        trade_type, str(trade.get("symbol") or ""), trade.get("securityName"), float(trade.get("shares") or 0),
    )
    fx_rate = _to_optional_float(payload.get(TX_FX_RATE_KEY))
    fx_currency = _to_optional_str(payload.get(TX_FX_CURRENCY_KEY))
    if tx is None:
        tx_payload: dict[str, object] = {
            "tx_type": "income",
            "amount": amount,
            "happened_at": trade.get("tradeDate"),
            "note": note,
            "account_id": account.get("syncId"),
            "account_name": account.get("name"),
            "category_kind": "income",
        }
        if payload.get(DIVIDEND_CATEGORY_ID_KEY):
            tx_payload["category_id"] = payload[DIVIDEND_CATEGORY_ID_KEY]
            tx_payload["category_name"] = payload.get(DIVIDEND_CATEGORY_NAME_KEY)
        if fx_rate and fx_currency:
            tx_payload["currency_code"] = fx_currency
            tx_payload["native_amount"] = _round_money(amount * fx_rate)
        for key in ("__actor_user_id", "__actor_is_admin", "__actor_in_shared_ledger"):
            if key in payload:
                tx_payload[key] = payload[key]
        return create_transaction(target, tx_payload)
    old_amount = _to_float(tx.get("amount"))
    if tx.get("nativeAmount") is not None:
        tx["nativeAmount"] = rescale_native_amount(old_amount, _to_float(tx.get("nativeAmount")), amount)
    tx["amount"] = amount
    tx["happenedAt"] = trade.get("tradeDate")
    tx["note"] = note
    tx["accountId"] = account.get("syncId")
    tx["accountName"] = account.get("name")
    _mark_entity_actor(tx, payload, create=False)
    return target, str(tx.get("syncId"))


def create_stock_trade(snapshot: dict, payload: dict) -> tuple[dict, str]:
    """payload(snake_case,web 請求體):account_id / trade_type / market /
    symbol / security_name / shares / price / fee / tax / currency /
    trade_date / settlement_account_id / settlement_amount / note。"""
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")

    trade_type = str(payload.get("trade_type") or "")
    if trade_type not in STOCK_TRADE_WEB_CREATABLE_TYPES:
        raise ValueError("write validation failed: invalid trade_type")
    investment_account = _account_by_id(accounts, _to_optional_str(payload.get("account_id")))
    if investment_account is None:
        raise ValueError("write validation failed: account not found")
    if investment_account.get("type") != "investment":
        raise ValueError("write validation failed: account must be an investment account")
    market = (_to_optional_str(payload.get("market")) or "").upper()
    symbol = (_to_optional_str(payload.get("symbol")) or "").upper()
    if not market or not symbol:
        raise ValueError("write validation failed: market and symbol are required")
    shares = _to_optional_float(payload.get("shares"))
    price = _to_optional_float(payload.get("price"))
    fee = _to_optional_float(payload.get("fee")) or 0.0
    tax = _to_optional_float(payload.get("tax")) or 0.0
    if trade_type in ("opening", "stock_dividend"):
        tax = 0.0
    if trade_type == "stock_dividend":
        price = price or 0.0
        fee = 0.0
    _validate_stock_numbers(shares, price, fee, tax, trade_type=trade_type)
    assert shares is not None and price is not None

    settlement_account = None
    if trade_type == "cash_dividend":
        settlement_account = _account_by_id(accounts, _to_optional_str(payload.get("settlement_account_id")))
        if settlement_account is None:
            raise ValueError("write validation failed: settlement account not found")
    if trade_type in STOCK_TRADE_CASH_TYPES:
        settlement_account = _account_by_id(accounts, _to_optional_str(payload.get("settlement_account_id")))
        if settlement_account is None:
            raise ValueError("write validation failed: settlement account not found")
        if settlement_account.get("type") == "account_group":
            raise ValueError("write validation failed: settlement account cannot be an account_group")
        if settlement_account.get("syncId") == investment_account.get("syncId"):
            raise ValueError("write validation failed: settlement account must differ from the investment account")

    currency = (
        _to_optional_str(payload.get("currency"))
        or _to_optional_str(investment_account.get("currency"))
        or ""
    ).upper() or None
    sync_id = _new_sync_id("stk")
    trade: dict[str, object] = {
        "syncId": sync_id,
        "accountId": investment_account.get("syncId"),
        "market": market,
        "symbol": symbol,
        "tradeType": trade_type,
        "shares": shares,
        "price": price,
        "fee": fee,
        "tax": tax,
        "amount": stock_trade_amount(trade_type, shares, price, fee, tax, currency),
        "tradeDate": _to_iso8601(payload.get("trade_date")),
    }
    if currency:
        trade["currency"] = currency
    name = _to_optional_str(payload.get("security_name"))
    if name:
        trade["securityName"] = name
    note = _to_optional_str(payload.get("note"))
    if note:
        trade["note"] = note
    target, tx_id = _apply_stock_tx(
        target,
        trade=trade,
        tx=None,
        investment_account=investment_account,
        settlement_account=settlement_account,
        settlement_amount=_to_optional_float(payload.get("settlement_amount")),
        payload=payload,
    )
    if tx_id:
        trade["txId"] = tx_id
    ref = _to_optional_str(payload.get("dividend_event_ref"))
    if ref:
        trade["dividendEventRef"] = ref
    _mark_entity_actor(trade, payload, create=True)
    _ensure_list(target, "stockTrades").append(trade)
    return target, sync_id


def update_stock_trade(snapshot: dict, trade_id: str, payload: dict) -> dict:
    """PATCH:可改 shares / price / fee / tax / trade_date / note /
    security_name / settlement_account_id / settlement_amount。trade_type /
    帳戶 / 標的建立後不可改(改了等同刪掉重建)。綁定的交易跟著重算。"""
    target = ensure_snapshot_v2(snapshot)
    accounts = _ensure_list(target, "accounts")
    trades = _ensure_list(target, "stockTrades")
    items = _ensure_list(target, "items")
    _, trade = _find_by_sync_id(trades, trade_id, expected_prefix="stk")
    _assert_actor_can_modify(trade, payload)
    trade_type = str(trade.get("tradeType"))
    if trade_type not in STOCK_TRADE_WEB_CREATABLE_TYPES:
        raise ValueError("write validation failed: invalid trade_type")

    for key, snap_key in (("shares", "shares"), ("price", "price"), ("fee", "fee"), ("tax", "tax")):
        if key in payload:
            trade[snap_key] = _to_optional_float(payload.get(key)) or 0.0
    if "trade_date" in payload:
        trade["tradeDate"] = _to_iso8601(payload.get("trade_date"))
    if "note" in payload:
        note = _to_optional_str(payload.get("note"))
        if note:
            trade["note"] = note
        else:
            trade.pop("note", None)
    if "security_name" in payload:
        name = _to_optional_str(payload.get("security_name"))
        if name:
            trade["securityName"] = name
        else:
            trade.pop("securityName", None)
    shares = float(trade.get("shares") or 0)
    price = float(trade.get("price") or 0)
    fee = float(trade.get("fee") or 0)
    tax = float(trade.get("tax") or 0)
    _validate_stock_numbers(shares, price, fee, tax, trade_type=trade_type)
    trade["amount"] = stock_trade_amount(trade_type, shares, price, fee, tax, _to_optional_str(trade.get("currency")))

    if trade_type in STOCK_TRADE_CASH_TYPES or trade_type in STOCK_TRADE_INCOME_TYPES:
        investment_account = _account_by_id(accounts, _to_optional_str(trade.get("accountId")))
        if investment_account is None:
            raise ValueError("write validation failed: account not found")
        tx = None
        tx_id = _to_optional_str(trade.get("txId"))
        if tx_id:
            for item in items:
                if str(item.get("syncId")) == tx_id:
                    tx = item
                    break
        if "settlement_account_id" in payload:
            settlement_id = _to_optional_str(payload.get("settlement_account_id"))
        elif tx is not None:
            settlement_id = _to_optional_str(
                tx.get("accountId") if trade_type in STOCK_TRADE_INCOME_TYPES
                else tx.get("fromAccountId") if trade_type == "buy" else tx.get("toAccountId")
            )
        else:
            settlement_id = None
        settlement_account = _account_by_id(accounts, settlement_id)
        if trade_type == "reinvest":
            settlement_account = investment_account
        if settlement_account is None:
            raise ValueError("write validation failed: settlement account not found")
        if "settlement_amount" in payload:
            settlement_amount = _to_optional_float(payload.get("settlement_amount"))
        elif tx is not None:
            settlement_amount = _to_optional_float(
                tx.get("amount") if trade_type in ("buy", "cash_dividend", "reinvest") else tx.get("toAmount")
            )
        else:
            settlement_amount = None
        if tx is None:
            # 綁定的交易不見了(例如舊資料),重建一筆並改掛到新 snapshot 上的
            # 同一筆 trade。
            trades.remove(trade)
        target, new_tx_id = _apply_stock_tx(
            target,
            trade=trade,
            tx=tx,
            investment_account=investment_account,
            settlement_account=settlement_account,
            settlement_amount=settlement_amount,
            payload=payload,
        )
        if new_tx_id:
            trade["txId"] = new_tx_id
        if tx is None:
            _ensure_list(target, "stockTrades").append(trade)
    _mark_entity_actor(trade, payload, create=False)
    return target


def delete_stock_trade(snapshot: dict, trade_id: str, payload: dict | None = None) -> dict:
    """刪除明細並連帶刪除綁定的交易(反向的「刪交易連帶刪明細」見
    delete_transaction)。"""
    target = ensure_snapshot_v2(snapshot)
    trades = _ensure_list(target, "stockTrades")
    idx, trade = _find_by_sync_id(trades, trade_id, expected_prefix="stk")
    _assert_actor_can_modify(trade, payload or {})
    trades.pop(idx)
    tx_id = _to_optional_str(trade.get("txId"))
    if tx_id:
        items = _ensure_list(target, "items")
        items[:] = [item for item in items if str(item.get("syncId")) != tx_id]
        target["count"] = len(items)
    return target
