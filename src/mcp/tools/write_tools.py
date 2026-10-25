"""MCP write tools — 6 个,LLM 用来修改用户数据。

**实现策略**:write tools 通过 **HTTP self-call** 调现有的 `/api/v1/write/*`
router endpoint,而不是直接动 DB。原因:
  1. 复用所有 idempotency / sync_change 登记 / WebSocket 推送等已有逻辑
  2. 跟 web/mobile 走完全相同代码路径,行为一致,bug 修一处全部受益
  3. write router 内部是 snapshot mutator 模式,直接绕过会丢失关键逻辑

为了让 self-call 通过 auth,我们为当前 PAT user 临时签发一个**仅本进程内**
的短期 JWT(60 秒过期),作为 self-call 的 access token。这个 JWT 不出
进程,scope 严格限制为 SCOPE_APP_WRITE,client_type='app'。

危险操作(delete)需要二次确认 — LLM 调用时如果 confirm=False 返回
"待确认"状态,LLM 跟用户确认后带 confirm=True 调一次。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ...config import get_settings
from ...database import SessionLocal
from ...models import (
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    User,
    UserAccountProjection,
    UserCategoryProjection,
    UserExchangeRateProjection,
    UserTagProjection,
)
from ...security import SCOPE_APP_WRITE, _create_token
from ..attachments import resolve_transaction_attachments
from ..datetime_utils import parse_transaction_datetime
from ..schemas import BatchTransactionItem, positive_amount
from .read_tools import _resolve_ledger, live_ledgers

logger = logging.getLogger(__name__)


class MCPRequestError(RuntimeError):
    def __init__(self, status_code, message):
        self.status_code = status_code
        self.error_code = 'REVISION_CONFLICT' if 'REVISION_CONFLICT' in message else f'HTTP_{status_code}'
        super().__init__(message)

# self-call 内部 JWT — 仅当前进程当下用,60 秒有效。比给 PAT 永久 SCOPE_APP_
# WRITE 安全 — PAT 只有 mcp:* scope,不能走 web/app 路径;但 self-call 的
# 短期 JWT 模拟 'app' client,让 write router 收的就是普通 mobile 提交。
_SELF_TOKEN_TTL_SEC = 60

# MCP 记账自动打的标签 —— 跟 mobile AI 记账(zh `AI记账` / en `AI`)区分开,
# 用户事后能在标签筛选里一键看出"哪些是 LLM 客户端帮我记的"。
# 跟 LLM 调用时传的 tags 是**并集**关系 — LLM 传 ["coffee"] 最终落地为
# ["coffee", "MCP"]。LLM 也可以显式不要某个 tag,但 MCP 这个默认永远会带。
_MCP_DEFAULT_TAG = "MCP"
# MCP 标签的默认颜色(cyan)— 避开 AI 记账 #9C27B0(purple),用户标签管理
# 页一眼可分辨"哪些是 LLM 创建的"和"我自己手动建的 AI 记账标签"。
_MCP_DEFAULT_TAG_COLOR = "#00BCD4"


def _internal_token(user: User) -> str:
    return _create_token(
        sub=user.id,
        token_type="access",
        expires_delta=timedelta(seconds=_SELF_TOKEN_TTL_SEC),
        scopes=[SCOPE_APP_WRITE],
        client_type="app",
    )


async def _self_call(method: str, path: str, user: User, **kwargs: Any) -> dict[str, Any]:
    """异步 HTTP self-call 到本进程的 router endpoint。

    用 ASGI in-process transport 避免真起 socket — 仍然走完整 FastAPI
    dep tree + middleware,但不出 TCP。
    """
    from ..._mcp_internal_client import get_internal_client  # late import 防循环

    headers = kwargs.pop("headers", {}) or {}
    headers["Authorization"] = f"Bearer {_internal_token(user)}"
    headers.setdefault("X-Device-ID", "mcp-internal")

    client = get_internal_client()
    resp = await client.request(method, path, headers=headers, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get('detail')
        except (ValueError, AttributeError):
            detail = None
        message = detail if isinstance(detail, str) else (
            detail.get('error_code', 'Request rejected') if isinstance(detail, dict) else 'Request validation failed')
        raise MCPRequestError(resp.status_code, str(message)[:300])
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text}


# ---------- ledger resolution for writes ------------------------------------


def _resolve_write_ledger(
    db, user: User, ledger_id: str | None
) -> tuple[Ledger | None, dict[str, Any] | None]:
    """为**写**操作解析目标账本。返回 ``(ledger, None)`` 表示成功;
    ``(None, status_dict)`` 表示调用方 / LLM 必须先澄清,**不应写入**。

    issue #31 两条规则:
      - 软删账本不可作为写入目标(`_resolve_ledger` 已排除,B1/B2);
      - 不指定 ``ledger_id`` 且有 **>1 个 live 账本**时**拒绝瞎猜**(B5):返回
        候选列表,逼 LLM 显式带 ``ledger_id`` —— 避免静默落到"最早创建"的幽灵
        默认账本(报告里"写进了不存在的默认账本"的根因)。

    返回的 status_dict 跟 `delete_transaction` 的 ``confirmation_required`` 一样,
    是给 LLM 看的结构化信号,不是错误。
    """
    if ledger_id:
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            return None, {
                "status": "ledger_not_found",
                "message": f"Ledger not found or has been deleted: {ledger_id}",
                "ledger_id": ledger_id,
            }
        return led, None

    live = live_ledgers(db, user.id)
    if not live:
        return None, {
            "status": "no_ledger",
            "message": "You have no ledger yet. Create one in BeeCount first.",
        }
    if len(live) == 1:
        return live[0], None
    return None, {
        "status": "ledger_required",
        "message": (
            "You have multiple ledgers — refusing to guess which one to write to. "
            "Re-call this tool with an explicit `ledger_id` (the `id` field of one "
            "of the candidates below)."
        ),
        "candidates": [{"id": led.external_id, "name": led.name} for led in live],
    }


# ---------- tools -----------------------------------------------------------


async def upload_attachment(
    user: User, *, file_name: str, content_base64: str,
    ledger_id: str | None = None, mime_type: str | None = None,
) -> dict[str, Any]:
    """通过现有 multipart 上传路径保存客户端传来的文件，不读取服务端路径。"""
    if not file_name.strip():
        raise ValueError("file_name must not be empty")
    settings = get_settings()
    max_bytes = settings.attachment_max_upload_bytes
    if len(content_base64) > 4 * ((max_bytes + 2) // 3):
        raise ValueError("Attachment upload too large")
    try:
        data = await asyncio.to_thread(base64.b64decode, content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("content_base64 must be valid standard Base64, without a data URL prefix") from exc
    if not data:
        raise ValueError("Attachment file is empty")
    if len(data) > max_bytes:
        raise ValueError("Attachment upload too large")
    with SessionLocal() as db:
        led, ledger_status = _resolve_write_ledger(db, user, ledger_id)
        if ledger_status is not None:
            return ledger_status
        assert led is not None
        ledger_external_id = led.external_id
    content_type = mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    return await _self_call(
        "POST", f"{settings.api_prefix}/attachments/upload", user,
        data={"ledger_id": ledger_external_id},
        files={"file": (file_name, data, content_type)},
    )


async def create_transaction(
    user: User, *, amount: float, tx_type: str = "expense", category: str | None = None,
    account: str | None = None, happened_at: str | None = None, note: str | None = None,
    tags: list[str] | None = None, ledger_id: str | None = None, currency: str | None = None,
    time_zone: str | None = None, attachments: list[str] | None = None,
    category_id: str | None = None, account_id: str | None = None,
    from_account_id: str | None = None, to_account_id: str | None = None,
    from_account: str | None = None, to_account: str | None = None,
    native_amount: float | None = None, exclude_from_stats: bool = False,
    exclude_from_budget: bool = False, idempotency_key: str | None = None,
) -> dict[str, Any]:
    from ..transaction_service import create_many
    raw = {key: value for key, value in locals().copy().items()
           if key not in {"user", "ledger_id", "time_zone", "idempotency_key", "create_many"}}
    return await create_many(user, transactions=[raw], ledger_id=ledger_id,
                             time_zone=time_zone, idempotency_key=idempotency_key, single=True)


async def update_transaction(
    user: User, *, sync_id: str, amount: float | None = None, tx_type: str | None = None,
    category: str | None = None, account: str | None = None, happened_at: str | None = None,
    note: str | None = None, tags: list[str] | None = None, time_zone: str | None = None,
    attachments: list[str] | None = None, ledger_id: str | None = None,
    category_id: str | None = None, account_id: str | None = None,
    from_account_id: str | None = None, to_account_id: str | None = None,
    from_account: str | None = None, to_account: str | None = None,
    currency: str | None = None, native_amount: float | None = None,
    exclude_from_stats: bool | None = None, exclude_from_budget: bool | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    from ..transaction_service import update_one
    raw = {key: value for key, value in locals().copy().items()
           if key not in {"user", "sync_id", "ledger_id", "update_one"}}
    return await update_one(user, sync_id=sync_id, ledger_id=ledger_id, **raw)


async def delete_transaction(
    user: User, *, sync_id: str, confirm: bool = False,
    ledger_id: str | None = None, expected_revision: int | None = None,
) -> dict[str, Any]:
    if not confirm:
        return {"status": "confirmation_required", "sync_id": sync_id,
                "message": "Confirm deletion with the user, then call with confirm=true"}
    from ..transaction_service import transaction_row
    with SessionLocal() as db:
        _, ledger = transaction_row(db, user, sync_id, ledger_id)
        external_id = ledger.external_id
    body = {"base_change_id": 0}
    if expected_revision is not None:
        body["expected_revision"] = expected_revision
    await _self_call("DELETE", f"{get_settings().api_prefix}/write/ledgers/{external_id}/transactions/{sync_id}",
                     user, json=body)
    return {"status": "deleted", "sync_id": sync_id, "ledger_id": external_id}


async def create_category(
    user: User,
    *,
    name: str,
    kind: str = "expense",
    parent_name: str | None = None,
    icon: str | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """新建一个分类(罕见 — LLM 一般用现有分类)。"""
    if kind not in {"expense", "income", "transfer"}:
        raise ValueError(f"Invalid kind: {kind}")

    with SessionLocal() as db:
        led, ledger_status = _resolve_write_ledger(db, user, ledger_id)
        if ledger_status is not None:
            return ledger_status
        ledger_external_id = led.external_id

    body: dict[str, Any] = {
        "base_change_id": 0,
        "name": name,
        "kind": kind,
        "level": 2 if parent_name else 1,
    }
    if parent_name:
        body["parent_name"] = parent_name
    if icon:
        body["icon"] = icon

    settings = get_settings()
    path = f"{settings.api_prefix}/write/ledgers/{ledger_external_id}/categories"
    result = await _self_call("POST", path, user, json=body)
    return {
        "sync_id": result.get("entity_id"),
        "name": name,
        "kind": kind,
        "_meta": result,
    }


async def update_budget(
    user: User,
    *,
    budget_id: str,
    amount: float,
) -> dict[str, Any]:
    """更新预算金额。"""
    if amount <= 0:
        raise ValueError("amount must be positive")

    with SessionLocal() as db:
        existing = db.scalar(
            select(ReadBudgetProjection).where(
                ReadBudgetProjection.user_id == user.id,
                ReadBudgetProjection.sync_id == budget_id,
            )
        )
        if existing is None:
            raise ValueError(f"Budget not found: {budget_id}")
        led = db.scalar(select(Ledger).where(Ledger.id == existing.ledger_id))
        if led is None:
            raise ValueError("Ledger missing for this budget")
        ledger_external_id = led.external_id

    settings = get_settings()
    path = f"{settings.api_prefix}/write/ledgers/{ledger_external_id}/budgets/{budget_id}"
    result = await _self_call(
        "PATCH",
        path,
        user,
        json={"base_change_id": 0, "amount": float(amount)},
    )
    return {"sync_id": budget_id, "amount": amount, "_meta": result}


# 一次 self-call /transactions/batch 最多塞多少笔(端点自身上限 50)。
_BATCH_CHUNK = 50
# 单次 create_transactions 调用的总上限 —— 防 LLM 一次塞几千笔把单个 tool call
# 拖死;超过让它分多次调。
_BULK_MAX_TOTAL = 200


async def create_transactions(
    user: User, *, transactions: list[dict[str, Any] | BatchTransactionItem],
    ledger_id: str | None = None, time_zone: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    from ..transaction_service import create_many
    items = [item.model_dump() if isinstance(item, BatchTransactionItem) else item for item in transactions]
    return await create_many(user, transactions=items, ledger_id=ledger_id,
                             time_zone=time_zone, idempotency_key=idempotency_key)


async def parse_and_create_from_text(
    user: User,
    *,
    text: str,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """让 BeeCount AI 自己解析自然语言并创建交易。

    LLM 偷懒选项 — 直接转发用户原话 → BeeCount AI parse → 自动 create。
    要求用户已配 AI chat provider(profile.ai_config_json),否则报错。
    """
    # B5(issue #31):先把目标账本定死,多账本不指定则不猜(返回候选),也避免
    # 拿一个软删 / 幽灵账本去跑 AI 解析。pin 到 external_id 后贯穿 parse + create。
    with SessionLocal() as db:
        led, ledger_status = _resolve_write_ledger(db, user, ledger_id)
        if ledger_status is not None:
            return ledger_status
        assert led is not None  # 契约:_resolve_write_ledger 的 status 为 None ⟺ led 命中
        ledger_id = led.external_id

    settings = get_settings()
    path = f"{settings.api_prefix}/ai/parse-tx-text"
    parsed = await _self_call(
        "POST",
        path,
        user,
        json={"text": text, "ledger_id": ledger_id, "locale": "zh"},
    )

    drafts = parsed.get("tx_drafts") or []
    if not drafts:
        return {
            "status": "parse_failed",
            "message": "AI did not extract any draft",
            "parsed": parsed,
        }
    draft = drafts[0]

    amount = draft.get("amount")
    if not isinstance(amount, (int, float)) or amount == 0:
        return {
            "status": "parse_failed",
            "message": "No valid amount in draft",
            "parsed": parsed,
        }
    tx_type = draft.get("tx_type") or "expense"
    category = draft.get("category_name")
    account = draft.get("account_name")
    happened_at = draft.get("happened_at")
    note = draft.get("note") or text
    # 多币种(.docs/multi-currency-ai A9):draft 的 currency 已被 server 端
    # _norm_currency 校验成 ISO 码或 "";空串要传 None 才会走「随账户/本位币」。
    currency = (draft.get("currency") or "").strip().upper() or None

    created = await create_transaction(
        user,
        amount=abs(float(amount)),
        tx_type=tx_type,
        category=category,
        account=account,
        happened_at=happened_at,
        note=note,
        ledger_id=ledger_id,
        currency=currency,
    )
    return {"status": "created", "parsed": draft, "transaction": created}


# ---------- internal helpers ------------------------------------------------


def _is_tag_missing_in_ledger(
    db, *, user_id: str, ledger_id: str, tag_name: str
) -> bool:
    """检查 UserTagProjection 里这个 user 是否已有同名 tag(tag 是 user-global)。"""
    del ledger_id  # tag 是 user-global,不再按 ledger 过滤
    existing = db.scalar(
        select(UserTagProjection).where(
            UserTagProjection.user_id == user_id,
            UserTagProjection.name == tag_name,
        )
    )
    return existing is None


def _lookup_tag_sync_ids(
    db, *, user_id: str, ledger_id: str, names: list[str]
) -> list[str]:
    """把 tag 名字解析成 sync_id(同 ledger)。没找到的名字直接丢弃。

    write router 接收 `tags` (CSV name) 时只填 tx.tags_csv,**不会**自动反查
    sync_id 填 tx.tag_sync_ids_json。导致 Tags 详情弹窗(用 tag_sync_ids_json
    精确过滤)看不到通过 name 创建的 tx。MCP 这里显式查一次 sync_id 一起传,
    两边索引都喂饱。
    """
    if not names:
        return []
    del ledger_id  # tag 是 user-global,跨账本统一
    rows = db.execute(
        select(UserTagProjection.name, UserTagProjection.sync_id).where(
            UserTagProjection.user_id == user_id,
            UserTagProjection.name.in_(names),
        )
    ).all()
    # 同一 name 同 ledger 应当唯一,但稳妥起见去重
    by_name: dict[str, str] = {}
    for n, sid in rows:
        by_name.setdefault(n, sid)
    # 保持 names 的输入顺序
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        sid = by_name.get(n)
        if sid and sid not in seen:
            out.append(sid)
            seen.add(sid)
    return out


async def _ensure_mcp_tag(user: User, ledger_external_id: str) -> None:
    """通过 write router self-call 建一个 MCP tag 实体。失败不阻塞主流程
    (例如 race condition 两个 tool call 同时建,第二个会拿 conflict,忽略即可
    —— tag 反正存在了)。
    """
    body = {
        "base_change_id": 0,
        "name": _MCP_DEFAULT_TAG,
        "color": _MCP_DEFAULT_TAG_COLOR,
    }
    settings = get_settings()
    path = f"{settings.api_prefix}/write/ledgers/{ledger_external_id}/tags"
    try:
        await _self_call("POST", path, user, json=body)
    except RuntimeError as exc:
        # 重复名 / race 会返 409 / 4xx;tag 既然存在或被并发创建了就 OK
        logger.info("mcp: ensure tag fallthrough — %s", exc)


def _merge_default_tag(tags: list[str] | None) -> list[str]:
    """把 `_MCP_DEFAULT_TAG` 并入用户给的 tags,去重保序(LLM 给的在前)。"""
    seen: dict[str, None] = {}
    if tags:
        for t in tags:
            v = (t or "").strip()
            if v:
                seen.setdefault(v, None)
    seen.setdefault(_MCP_DEFAULT_TAG, None)
    return list(seen.keys())


# ---------- internal lookups ------------------------------------------------


def _validate_names_exist(
    db, user_id: str, *, categories: set[str], accounts: set[str]
) -> None:
    """批量校验 category / account 名都存在(各一条 IN 查询),不存在的一次性报全。

    给 create_transactions 用 —— 单笔 create 走 _lookup_* 逐个校验,批量则一次
    查清,避免 N 次查询,且能在一条错误里列出所有未知名字。
    """
    if categories:
        found = {
            n
            for (n,) in db.execute(
                select(UserCategoryProjection.name).where(
                    UserCategoryProjection.user_id == user_id,
                    UserCategoryProjection.name.in_(categories),
                )
            ).all()
        }
        missing = sorted(categories - found)
        if missing:
            raise ValueError(
                f"Unknown categories: {missing}. Use existing category names "
                "(call list_categories) or create them first."
            )
    if accounts:
        found = {
            n
            for (n,) in db.execute(
                select(UserAccountProjection.name).where(
                    UserAccountProjection.user_id == user_id,
                    UserAccountProjection.name.in_(accounts),
                )
            ).all()
        }
        missing = sorted(accounts - found)
        if missing:
            raise ValueError(
                f"Unknown accounts: {missing}. Use existing account names "
                "(call list_accounts)."
            )


def _lookup_category_sync_id(db, user_id: str, name: str | None, tx_type: str | None) -> str | None:
    if not name:
        return None
    query = select(UserCategoryProjection).where(
        UserCategoryProjection.user_id == user_id,
        UserCategoryProjection.name == name,
    )
    if tx_type and tx_type in {"expense", "income", "transfer"}:
        query = query.where(UserCategoryProjection.kind == tx_type)
    rows = db.scalars(query.limit(2)).all()
    if not rows:
        raise ValueError(f"Category not found: {name}")
    if len(rows) > 1:
        raise ValueError(f"Ambiguous category: {name!r} ({tx_type}); rename or select a unique category")
    return rows[0].sync_id


def _account_currency(db, user_id: str, name: str | None) -> str | None:
    """账户名 → 币种(大写)。查不到返回 None。"""
    if not name:
        return None
    row = db.scalar(
        select(UserAccountProjection.currency)
        .where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.name == name,
        )
        .limit(1)
    )
    return (row or "").strip().upper() or None


async def _build_currency_fields(
    user: User,
    *,
    ledger_base: str,
    account_currency: str | None,
    currency_arg: str | None,
    amount: float,
) -> dict[str, Any]:
    """v30 交易级多币种:MCP 记账时定交易币种 + 折账本本位币快照。

    币种优先级:显式 currency 参数 > 账户币种 > 账本本位币(与 App 一致)。
    折算方向:手动 override(1 quote = rate base,乘) > 自动源 fetcher
    (1 base = x quote,除),缺汇率退化 =amount(1:1,currency_code 仍落,
    Web 改主币种重算 / App L11 横幅可捞回)。返回要并进 body 的字段 dict。
    """
    base = ledger_base.strip().upper()
    cc = (currency_arg or account_currency or base).strip().upper()
    if cc == base:
        # 本位币:body 不带两字段(server 落 NULL,统计 COALESCE 回退 amount)
        return {}
    # override 优先(user-global,同步表)
    with SessionLocal() as db:
        ov = db.scalar(
            select(UserExchangeRateProjection.rate).where(
                UserExchangeRateProjection.user_id == user.id,
                UserExchangeRateProjection.base_currency == base,
                UserExchangeRateProjection.quote_currency == cc,
            ).limit(1)
        )
    native: float | None = None
    if ov is not None:
        try:
            r = float(ov)
            if r > 0:
                native = amount * r  # 1 cc = r base
        except (TypeError, ValueError):
            native = None
    if native is None:
        # 自动源(server 汇率代理);拉不到就退化 1:1
        try:
            from ...services.exchange_rate import fetcher as _rf
            with SessionLocal() as db:
                row, _stale = await _rf.get_rates(db, base)
            raw = dict(row.payload_json).get(cc) or dict(row.payload_json).get(cc.lower())
            x = float(raw) if raw is not None else 0.0
            native = amount / x if x > 0 else amount  # 1 base = x cc → cc 折 base 要除
        except Exception:
            native = amount
    return {"currency_code": cc, "native_amount": native}


def _lookup_account_sync_id(db, user_id: str, name: str | None) -> str | None:
    if not name:
        return None
    rows = db.scalars(
        select(UserAccountProjection)
        .where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.name == name,
        )
        .limit(2)
    ).all()
    if not rows:
        raise ValueError(f"Account not found: {name}")
    if len(rows) > 1:
        raise ValueError(f"Ambiguous account: {name!r}; rename or select a unique account")
    return rows[0].sync_id
