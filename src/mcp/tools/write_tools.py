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

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ...config import get_settings
from ...database import SessionLocal
from ...models import (
    Ledger,
    ReadBudgetProjection,
    ReadTxProjection,
    UserAccountProjection,
    UserCategoryProjection,
    UserTagProjection,
    User,
)
from ...security import SCOPE_APP_WRITE, _create_token
from .read_tools import _parse_dt, _resolve_ledger

logger = logging.getLogger(__name__)

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
        raise RuntimeError(
            f"self-call {method} {path} -> {resp.status_code} {resp.text[:300]}"
        )
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"_raw": resp.text}


# ---------- tools -----------------------------------------------------------


async def create_transaction(
    user: User,
    *,
    amount: float,
    tx_type: str = "expense",
    category: str | None = None,
    account: str | None = None,
    happened_at: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
    ledger_id: str | None = None,
) -> dict[str, Any]:
    """新建一笔交易。category / account 用名字。happened_at 不传 = 当前时间。"""
    if tx_type not in {"expense", "income", "transfer"}:
        raise ValueError(f"Invalid tx_type: {tx_type}")
    if amount <= 0:
        raise ValueError("amount must be positive")

    with SessionLocal() as db:
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            raise ValueError(f"Ledger not found: {ledger_id}")
        if category:
            _lookup_category_sync_id(db, user.id, category, tx_type)
        if account:
            _lookup_account_sync_id(db, user.id, account)
        ledger_external_id = led.external_id
        ledger_name = led.name
        led_internal_id = led.id  # 出 with 块后 led 会 detach,提前取值
        mcp_tag_missing = _is_tag_missing_in_ledger(
            db, user_id=user.id, ledger_id=led_internal_id, tag_name=_MCP_DEFAULT_TAG,
        )

    # 标签管理页是从 UserTagProjection 读的 —— 只往 tx.tags_csv 写 "MCP" 不够,
    # 必须额外建一个独立 tag 实体行,Tags 页 / mobile / 同步才能识别。
    # 幂等:如果已存在就跳过。
    if mcp_tag_missing:
        await _ensure_mcp_tag(user, ledger_external_id)

    happened = _parse_dt(happened_at) if happened_at else datetime.now(timezone.utc)
    body: dict[str, Any] = {
        "base_change_id": 0,
        "tx_type": tx_type,
        "amount": float(amount),
        "happened_at": happened.isoformat(),
    }
    if note:
        body["note"] = note
    if category:
        body["category_name"] = category
        body["category_kind"] = tx_type
    if account:
        if tx_type == "transfer":
            body["from_account_name"] = account
        else:
            body["account_name"] = account
    # 始终注入 MCP 默认标签;跟 LLM 传的 tags 并集去重,顺序保持 LLM 给的在前
    final_tags = _merge_default_tag(tags)
    body["tags"] = final_tags
    # 同时把对应 sync_id 也喂给 server —— 两个字段一起填,Tags 详情弹窗
    # (走 tag_sync_ids_json 精确过滤)才能找到这笔 tx。
    with SessionLocal() as db:
        tag_ids = _lookup_tag_sync_ids(
            db, user_id=user.id, ledger_id=led_internal_id, names=final_tags,
        )
    if tag_ids:
        body["tag_ids"] = tag_ids

    settings = get_settings()
    path = f"{settings.api_prefix}/write/ledgers/{ledger_external_id}/transactions"
    result = await _self_call("POST", path, user, json=body)
    return {
        "sync_id": result.get("entity_id"),
        "ledger": ledger_name,
        "tx_type": tx_type,
        "amount": amount,
        "happened_at": happened.isoformat(),
        "category": category,
        "account": account,
        "_meta": result,
    }


async def update_transaction(
    user: User,
    *,
    sync_id: str,
    amount: float | None = None,
    tx_type: str | None = None,
    category: str | None = None,
    account: str | None = None,
    happened_at: str | None = None,
    note: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """更新现有交易。只更新传入的字段。"""
    with SessionLocal() as db:
        existing = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.user_id == user.id,
                ReadTxProjection.sync_id == sync_id,
            )
        )
        if existing is None:
            raise ValueError(f"Transaction not found: {sync_id}")
        led = db.scalar(select(Ledger).where(Ledger.id == existing.ledger_id))
        if led is None:
            raise ValueError("Ledger missing for this tx")
        ledger_external_id = led.external_id
        effective_tx_type = tx_type or existing.tx_type
        if category:
            _lookup_category_sync_id(db, user.id, category, effective_tx_type)
        if account:
            _lookup_account_sync_id(db, user.id, account)

    patch: dict[str, Any] = {"base_change_id": 0}
    if amount is not None:
        if amount <= 0:
            raise ValueError("amount must be positive")
        patch["amount"] = float(amount)
    if tx_type is not None:
        if tx_type not in {"expense", "income", "transfer"}:
            raise ValueError(f"Invalid tx_type: {tx_type}")
        patch["tx_type"] = tx_type
    if happened_at is not None:
        patch["happened_at"] = _parse_dt(happened_at).isoformat()
    if note is not None:
        patch["note"] = note
    if category is not None:
        patch["category_name"] = category
        patch["category_kind"] = effective_tx_type
    if account is not None:
        if effective_tx_type == "transfer":
            patch["from_account_name"] = account
        else:
            patch["account_name"] = account
    if tags is not None:
        patch["tags"] = list(tags)

    settings = get_settings()
    path = f"{settings.api_prefix}/write/ledgers/{ledger_external_id}/transactions/{sync_id}"
    result = await _self_call("PATCH", path, user, json=patch)
    return {
        "sync_id": sync_id,
        "updated": [k for k in patch.keys() if k != "base_change_id"],
        "_meta": result,
    }


async def delete_transaction(
    user: User,
    *,
    sync_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """删除一笔交易。**危险操作** — confirm=False 时返"待确认"状态,LLM 必须
    跟用户确认后带 confirm=True 再调一次。
    """
    if not confirm:
        return {
            "status": "confirmation_required",
            "message": (
                "Delete transaction requires explicit confirmation. "
                "Please confirm with the user, then call again with confirm=true."
            ),
            "sync_id": sync_id,
        }

    with SessionLocal() as db:
        existing = db.scalar(
            select(ReadTxProjection).where(
                ReadTxProjection.user_id == user.id,
                ReadTxProjection.sync_id == sync_id,
            )
        )
        if existing is None:
            raise ValueError(f"Transaction not found: {sync_id}")
        led = db.scalar(select(Ledger).where(Ledger.id == existing.ledger_id))
        if led is None:
            raise ValueError("Ledger missing for this tx")
        ledger_external_id = led.external_id

    settings = get_settings()
    path = f"{settings.api_prefix}/write/ledgers/{ledger_external_id}/transactions/{sync_id}"
    await _self_call("DELETE", path, user, json={"base_change_id": 0})
    return {"status": "deleted", "sync_id": sync_id}


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
        led = _resolve_ledger(db, user.id, ledger_id)
        if led is None:
            raise ValueError("No ledger found")
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

    created = await create_transaction(
        user,
        amount=abs(float(amount)),
        tx_type=tx_type,
        category=category,
        account=account,
        happened_at=happened_at,
        note=note,
        ledger_id=ledger_id,
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


def _lookup_category_sync_id(db, user_id: str, name: str | None, tx_type: str | None) -> str | None:
    if not name:
        return None
    query = select(UserCategoryProjection).where(
        UserCategoryProjection.user_id == user_id,
        UserCategoryProjection.name == name,
    )
    if tx_type and tx_type in {"expense", "income", "transfer"}:
        query = query.where(UserCategoryProjection.kind == tx_type)
    row = db.scalar(query.limit(1))
    if row is None:
        raise ValueError(f"Category not found: {name}")
    return row.sync_id


def _lookup_account_sync_id(db, user_id: str, name: str | None) -> str | None:
    if not name:
        return None
    row = db.scalar(
        select(UserAccountProjection)
        .where(
            UserAccountProjection.user_id == user_id,
            UserAccountProjection.name == name,
        )
        .limit(1)
    )
    if row is None:
        raise ValueError(f"Account not found: {name}")
    return row.sync_id
