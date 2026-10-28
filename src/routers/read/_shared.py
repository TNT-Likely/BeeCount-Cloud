"""read.py 的共享层。

原 src/routers/read.py 1705 行,按路由组拆成 3 个子模块后,各 endpoint 都
依赖的 imports + helper(_resolve_ledger_name / _owner_map_for_ledgers /
_get_latest_change_id / snapshot_cache 相关 / Flutter ↔ server 字段转换 等)
集中在这里。

子模块各自关注一类资源:
  - ledgers.py    /ledgers / /ledgers/{id}/*      账本维度读
  - workspace.py  /workspace/*                    跨账本聚合读
  - summary.py    /summary                        单独小端点

修改某条 read 端点的具体查询,进对应子模块;修改共享字段映射 / 账本可见性
/ projection 读辅助,改 _shared.py 一处。
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session

from ...config import get_settings
from ...database import get_db
from ...deps import get_current_user, require_any_scopes, require_scopes
from ...ledger_access import (
    get_accessible_ledger_by_external_id,
)
from ...models import (
    AttachmentFile,
    Ledger,
    LedgerMember,
    ReadBudgetProjection,
    ReadItemProjection,
    ReadRefundProjection,
    ReadTimeDepositProjection,
    ReadTxProjection,
    SyncChange,
    User,
    UserAccountProjection,
    UserCategoryProjection,
    UserProfile,
    UserTagProjection,
)
from ...services.linked_transactions import refund_entity_id_from_tx_sync_id
from ...schemas import (
    AnalyticsMetric,
    AnalyticsScope,
    ReadAccountOut,
    ReadBudgetOut,
    ReadBudgetUsageItemOut,
    ReadBudgetUsageOut,
    ReadCategoryOut,
    ReadLedgerDetailOut,
    ReadLedgerOut,
    ReadSummaryOut,
    ReadTagOut,
    ReadTransactionOut,
    WorkspaceAccountOut,
    WorkspaceAnalyticsAnomalyAttributionOut,
    WorkspaceAnalyticsAnomalyMonthOut,
    WorkspaceAnalyticsCategoryRankOut,
    WorkspaceAnalyticsOut,
    WorkspaceAnalyticsRangeOut,
    WorkspaceAnalyticsSeriesItemOut,
    WorkspaceAnalyticsSummaryOut,
    WorkspaceCategoryOut,
    WorkspaceLedgerCountsOut,
    WorkspaceTagOut,
    WorkspaceTransactionOut,
    WorkspaceTransactionPageOut,
)
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from ... import snapshot_cache

router = APIRouter()
settings = get_settings()
_READ_SCOPE_DEP = (
    require_any_scopes(SCOPE_WEB_READ, SCOPE_APP_WRITE)
    if settings.allow_app_rw_scopes
    else require_scopes(SCOPE_WEB_READ)
)


def _is_admin(current_user: User) -> bool:
    """单用户隔离模型下,read 路由永远按 current_user 过滤 —— admin 角色只
    作用于 /admin/* 管理面板(用户列表、备份、日志等),不给读账本/交易/分类/
    标签/账户开"看所有用户数据"的后门。之前 admin 用户注册成第一个账号会
    自动被提升为 admin(见 alembic 0007_admin_bootstrap),结果 User B 登录
    就看到 User A 所有账本 —— 单用户自部署场景下这是 bug,不是 feature。
    """
    _ = current_user
    return False


def _require_ledger(
    db: Session,
    *,
    user_id: str,
    ledger_external_id: str,
    is_admin: bool,
) -> tuple[Ledger, None]:
    """Resolve a ledger for the caller.

    Returns ``(ledger, None)`` — the second slot used to hold a LedgerMember
    row and is retained for back-compat with callers that destructure.

    `is_admin` 参数保留兼容上游签名,但**不再触发跨用户旁路** —— 历史上 admin
    分支只按 external_id 查会随机命中第一行(Ledger.external_id 是
    (user_id, external_id) 复合唯一,不是全局唯一),导致 admin 通过 mobile
    访问时把自己的数据错挂到其他用户的同 external_id 账本。admin 跨用户访问
    需求应该通过专门的管理后台 endpoint(带显式 user_id 参数)实现。
    """
    del is_admin  # 历史参数,不再用作旁路
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user_id,
        ledger_external_id=ledger_external_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Ledger not found")
    ledger_row, _ = row
    if _is_ledger_deleted(db, ledger_id=ledger_row.id):
        raise HTTPException(status_code=404, detail="Ledger not found")
    return row


# ---------------------------------------------------------------------------
# Snapshot helpers — replaces all Web*Projection queries
# ---------------------------------------------------------------------------

def _get_latest_snapshot(db: Session, *, ledger_id: str) -> dict[str, Any] | None:
    """Return the parsed snapshot from the most recent ledger_snapshot SyncChange.

    payload_json 形状是 `{"content": "<json-string>", "metadata": {...}}`,
    content 是真正的 snapshot(ledgerName / items / accounts / categories / tags /
    budgets)。

    热路径:
    - 先只查该 ledger 的 `ledger_snapshot` 最大 change_id(很轻,命中索引,不读 blob)
    - 拿进程内 `snapshot_cache` 按 (ledger_id, change_id) 对账,命中直接返回 —
      跳过 3MB 行读 + 几十毫秒的 json.loads
    - 未命中才读 payload + parse + 回灌缓存

    命中率:单用户日常 dashboard 连环打 5-6 次 `/read/*`,首次 miss、其余全 hit,
    累计耗时从 ~250ms 降到 ~50ms(一次 parse 摊薄)。
    """
    latest_change_id_for_snapshot = db.scalar(
        select(func.max(SyncChange.change_id)).where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
        )
    )
    if latest_change_id_for_snapshot is None:
        return None

    cached = snapshot_cache.get(ledger_id, int(latest_change_id_for_snapshot))
    if cached is not None:
        return cached

    row = db.scalar(
        select(SyncChange.payload_json)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
            SyncChange.change_id == latest_change_id_for_snapshot,
        )
        .limit(1)
    )
    if row is None:
        return None
    if isinstance(row, str):
        row = json.loads(row)
    parsed: dict[str, Any] | None = None
    if isinstance(row, dict):
        content = row.get("content")
        if isinstance(content, str) and content.strip():
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = row  # fallback:把原 payload_json 返回,维持老行为
        else:
            parsed = row
    if parsed is not None:
        snapshot_cache.put(ledger_id, int(latest_change_id_for_snapshot), parsed)
    return parsed


def _get_latest_change_id(db: Session, *, ledger_id: str) -> int:
    val = db.scalar(
        select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id == ledger_id)
    )
    return int(val or 0)


def _user_info_map(
    db: Session, user_ids: set[str]
) -> dict[str, tuple[str | None, str | None, str | None, int]]:
    """Return {user_id: (email, display_name, avatar_file_id, avatar_version)}
    给共享账本 tx 列表展示 "X 创建 · Y 编辑" 时回填创建者/编辑者头像 + name 用。

    avatar_file_id 是 server attachment id;web 拼成 `/api/v1/profile/avatar/...`
    访问。display_name 缺失时 caller fallback 到 email split('@')[0]。
    """
    if not user_ids:
        return {}
    users = db.execute(
        select(User.id, User.email).where(User.id.in_(user_ids))
    ).all()
    profiles = db.execute(
        select(
            UserProfile.user_id,
            UserProfile.display_name,
            UserProfile.avatar_file_id,
            UserProfile.avatar_version,
        ).where(UserProfile.user_id.in_(user_ids))
    ).all()
    email_by_uid = {row[0]: row[1] for row in users}
    profile_by_uid: dict[str, tuple[str | None, str | None, int]] = {
        row[0]: (row[1], row[2], int(row[3] or 0)) for row in profiles
    }
    out: dict[str, tuple[str | None, str | None, str | None, int]] = {}
    for uid in user_ids:
        email = email_by_uid.get(uid)
        prof = profile_by_uid.get(uid, (None, None, 0))
        out[uid] = (email, prof[0], prof[1], prof[2])
    return out


def _owner_map_for_ledgers(
    db: Session, ledgers: list[Ledger]
) -> dict[str, tuple[str, str | None]]:
    """Return {ledger_external_id: (user_id, email)} for the given ledgers.
    Single-user-per-ledger: every entity in a ledger was created by its owner,
    so this is the right attribution for the web tables.
    """
    user_ids = {lg.user_id for lg in ledgers}
    if not user_ids:
        return {}
    rows = db.execute(
        select(User.id, User.email).where(User.id.in_(user_ids))
    ).all()
    email_by_uid = {row[0]: row[1] for row in rows}
    return {
        lg.external_id: (lg.user_id, email_by_uid.get(lg.user_id))
        for lg in ledgers
    }


def _is_ledger_deleted(db: Session, *, ledger_id: str) -> bool:
    """True iff the latest ledger_snapshot sync change for this ledger is a
    tombstone (``action='delete'``). Used to filter / 404 deleted ledgers in
    read endpoints without dropping the underlying rows (we keep the audit
    trail under the soft-delete model)."""
    latest_action = db.scalar(
        select(SyncChange.action)
        .where(
            SyncChange.ledger_id == ledger_id,
            SyncChange.entity_type == "ledger_snapshot",
        )
        .order_by(SyncChange.change_id.desc())
        .limit(1)
    )
    return latest_action == "delete"


def _visible_workspace_ledgers(
    db: Session,
    *,
    current_user: User,
    is_admin: bool,
    ledger_id: str | None = None,
    user_id: str | None = None,
) -> list[Ledger]:
    """workspace.py 跨账本聚合端点共用的账本可见性解析。

    统一三件事(此前 7 个端点各自 copy-paste 一份 ledger_conditions):
      - ``ledger_id``(external_id)限定单账本;
      - admin + ``user_id`` 限定某用户;非 admin 走 ``LedgerMember``;
      - **排除软删账本**(issue #31):软删账本被写入"复活"出来的交易不应再出现
        在跨账本 / tag / 统计视图里 —— 跟 ``/read/ledgers`` 的 `_is_ledger_deleted`
        过滤口径保持一致(此前 workspace 不过滤,导致"tag 里看得到、账本列表里
        却没有"的幽灵交易)。
    """
    conditions: list[Any] = []
    if ledger_id:
        conditions.append(Ledger.external_id == ledger_id)
    if is_admin:
        if user_id:
            conditions.append(Ledger.user_id == user_id)
    else:
        # 共享账本:走 LedgerMember 维度,Editor 也能看到 Owner 的账本数据。
        conditions.append(
            Ledger.id.in_(
                select(LedgerMember.ledger_id).where(
                    LedgerMember.user_id == current_user.id
                )
            )
        )
    ledgers = list(
        db.execute(
            select(Ledger).where(and_(*conditions) if conditions else true())
        ).scalars().all()
    )
    return [lg for lg in ledgers if not _is_ledger_deleted(db, ledger_id=lg.id)]


def _snapshot_ledger_info(
    snapshot: dict[str, Any] | None,
    *,
    ledger: Ledger,
) -> tuple[str, str]:
    """Return (ledger_name, currency) from a snapshot, with fallbacks."""
    if snapshot:
        name = (snapshot.get("ledgerName") or "").strip()
        currency = (snapshot.get("currency") or "").strip()
    else:
        name = ""
        currency = ""
    if not name:
        name = (ledger.name or ledger.external_id).strip() or ledger.external_id
    if not currency:
        currency = "CNY"
    return name, currency


def _resolve_ledger_name(db: Session, *, ledger: Ledger) -> str:
    """Ledger.name 是权威源。边缘 case(老数据 name 为 NULL,0020 迁移错过,
    或 mobile 没 push ledger entity 过):回退到 sync_changes,并自愈写回 Ledger.name。
    自愈写入用独立 commit —— 读 endpoint 默认不提交,我们显式提交以持久化。
    """
    if ledger.name and ledger.name.strip():
        return ledger.name.strip()

    resolved: str | None = None

    # Fallback 1:最近一条 ledger entity SyncChange 的 ledgerName
    recent_ledger = db.scalar(
        select(SyncChange.payload_json).where(
            SyncChange.ledger_id == ledger.id,
            SyncChange.entity_type == "ledger",
            SyncChange.action == "upsert",
        ).order_by(SyncChange.change_id.desc()).limit(1)
    )
    if recent_ledger is not None:
        payload = recent_ledger
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
        if isinstance(payload, dict):
            name = (payload.get("ledgerName") or "").strip()
            if name:
                resolved = name

    # Fallback 2:历史 ledger_snapshot 的 content.ledgerName(Plan B 前的数据)
    if resolved is None:
        recent_snap = db.scalar(
            select(SyncChange.payload_json).where(
                SyncChange.ledger_id == ledger.id,
                SyncChange.entity_type == "ledger_snapshot",
                SyncChange.action == "upsert",
            ).order_by(SyncChange.change_id.desc()).limit(1)
        )
        if recent_snap is not None:
            payload = recent_snap
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if isinstance(payload, dict):
                content = payload.get("content")
                if isinstance(content, str) and content.strip():
                    try:
                        snap = json.loads(content)
                        if isinstance(snap, dict):
                            name = (snap.get("ledgerName") or "").strip()
                            if name:
                                resolved = name
                    except json.JSONDecodeError:
                        pass

    if resolved is None:
        return ledger.external_id

    # 自愈持久化:显式 commit 让下次 read 直接命中 Ledger.name 快路径
    try:
        ledger.name = resolved[:255]
        db.commit()
    except Exception:  # noqa: BLE001 — 自愈失败不影响读本次返回
        db.rollback()
    return resolved


def _load_owner_identity(db: Session, *, ledger: Ledger) -> tuple[str, str | None, str | None, str | None, int]:
    """Return (user_id, email, display_name, avatar_url, avatar_version) for
    the ledger owner. Single-user-per-ledger model: every row in the ledger
    was created by the owner."""
    row = db.execute(
        select(
            User.id,
            User.email,
            UserProfile.display_name,
            UserProfile.avatar_file_id,
            UserProfile.avatar_version,
        )
        .join(UserProfile, UserProfile.user_id == User.id, isouter=True)
        .where(User.id == ledger.user_id)
    ).first()
    if row is None:
        return ledger.user_id, None, None, None, 0
    return row[0], row[1], row[2], (
        f"/api/v1/attachments/{row[3]}" if row[3] else None
    ), int(row[4] or 0)


def _tags_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _tx_extension_fields(
    row: Any,
    item_name: str | None = None,
    time_deposit_name: str | None = None,
    refund_source_label: str | None = None,
) -> dict[str, Any]:
    """0021 交易扩展字段:projection JSON 文本列 → 原生对象。

    discount(优惠)/ transfer_fee(转账手续费)/ payment_legs(合并付款)/
    member_shares(成员分摊,0024)。空值 / 坏 JSON → None,跟
    ReadTransactionOut 的默认值语义一致。

    归物(0029):item_id 直接从 row 取;item_name 由调用方按本页 sync_id
    批量查好传进来(避免逐行查物品表的 N+1)。

    定期存款(0031):time_deposit_id 直接从 row 取;time_deposit_name 同理
    由调用方批量查好传进来(定存没有名称列,由账户名 + 本金合成)。

    退款到账(0032):refund_source_label 由调用方按 tx sync_id 批量反查
    (退款记录 → 原账单)传进来;非退款关联交易恒为 None。
    """
    out: dict[str, Any] = {}
    for attr, key in (
        ("discount_json", "discount"),
        ("transfer_fee_json", "transfer_fee"),
        ("payment_legs_json", "payment_legs"),
        ("member_shares_json", "member_shares"),
    ):
        raw = getattr(row, attr, None)
        value: Any = None
        if raw:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, (dict, list)) and parsed:
                value = parsed
        out[key] = value
    out["item_id"] = getattr(row, "item_sync_id", None)
    out["item_name"] = item_name
    # 定期存款利息(0030):关联交易 sync_id 直接取(弱引用,存款被删不影响交易)。
    out["time_deposit_id"] = getattr(row, "time_deposit_sync_id", None)
    out["time_deposit_name"] = time_deposit_name
    # 退款到账(0032):原账单摘要(备注 > 分类名 > 金额·日期);非退款关联为 None。
    out["refund_source_label"] = refund_source_label
    return out


def item_names_by_sync_id(
    db: Any, rows: list[Any], *, ledger_ids: set[str] | None = None
) -> dict[str, str]:
    """归物(0029):批量把 tx 行的 item_sync_id 换成物品名。

    一次查询搞定本页所有关联物品(避免 N+1);物品被删 → 映射里没有该
    sync_id,item_name 自然为 None(交易不阻塞,只是不显示名字)。
    """
    ids = {
        str(getattr(row, "item_sync_id"))
        for row in rows
        if getattr(row, "item_sync_id", None)
    }
    if not ids:
        return {}
    stmt = select(ReadItemProjection.sync_id, ReadItemProjection.name).where(
        ReadItemProjection.sync_id.in_(ids)
    )
    if ledger_ids:
        stmt = stmt.where(ReadItemProjection.ledger_id.in_(ledger_ids))
    return {sid: name for sid, name in db.execute(stmt).all()}


def time_deposit_names_by_sync_id(
    db: Any, rows: list[Any], *, ledger_ids: set[str] | None = None
) -> dict[str, str]:
    """定期存款(0031):批量把 tx 行的 time_deposit_sync_id 换成存款展示名。

    定存没有「名称」列,展示名由「资金来源账户名 + 币种本金」合成,便于在流水里
    一眼认出这笔利息来自哪笔定存。一次查询搞定本页(避免 N+1);存款被删 →
    映射里没有该 sync_id,name 为 None(交易不阻塞,只是不显示名字)。
    """
    ids = {
        str(getattr(row, "time_deposit_sync_id"))
        for row in rows
        if getattr(row, "time_deposit_sync_id", None)
    }
    if not ids:
        return {}
    stmt = select(
        ReadTimeDepositProjection.sync_id,
        ReadTimeDepositProjection.account_name,
        ReadTimeDepositProjection.principal,
        ReadTimeDepositProjection.currency,
    ).where(ReadTimeDepositProjection.sync_id.in_(ids))
    if ledger_ids:
        stmt = stmt.where(ReadTimeDepositProjection.ledger_id.in_(ledger_ids))
    out: dict[str, str] = {}
    for sid, account_name, principal, currency in db.execute(stmt).all():
        amount = f"{principal or 0:,.2f}".rstrip("0").rstrip(".")
        account = (account_name or "").strip()
        out[sid] = f"{account} · {currency} {amount}" if account else f"{currency} {amount}"
    return out


def refund_source_labels_by_tx_sync_id(
    db: Any, rows: list[Any], *, ledger_ids: set[str] | None = None
) -> dict[str, str]:
    """退款到账(0032):批量把「退款关联交易」的 sync_id 换成原账单展示名。

    退款 status=received 会生成 income 关联交易,tx sync_id = ``<refund_id>::rf-tx``
    / ``<refund_id>::rf-leg-N``。流水里这笔只看得到金额和「退款到账」备注,认不出
    退的是哪笔消费 —— 这里反查 退款记录 → 原交易,回填一句原账单摘要。

    展示名优先级:原单备注 > 分类名 > 「金额 · 日期」(总有值,老数据也认得出)。
    三次查询搞定本页(避免 N+1);原单被删 → 该 tx 不进映射,label 为 None。
    """
    entity_by_tx: dict[str, str] = {}
    for row in rows:
        tx_sid = str(getattr(row, "sync_id", "") or "")
        entity_id = refund_entity_id_from_tx_sync_id(tx_sid)
        if entity_id:
            entity_by_tx[tx_sid] = entity_id
    if not entity_by_tx:
        return {}

    # 退款记录 → 原单 id(弱引用,原交易被删则 original_tx_id 仍存但查不到行)。
    stmt = select(
        ReadRefundProjection.sync_id, ReadRefundProjection.original_tx_id
    ).where(ReadRefundProjection.sync_id.in_(set(entity_by_tx.values())))
    if ledger_ids:
        stmt = stmt.where(ReadRefundProjection.ledger_id.in_(ledger_ids))
    orig_by_refund = {
        rid: oid for rid, oid in db.execute(stmt).all() if oid
    }
    if not orig_by_refund:
        return {}

    # 原单 → 展示名。
    tx_stmt = select(
        ReadTxProjection.sync_id,
        ReadTxProjection.note,
        ReadTxProjection.category_name,
        ReadTxProjection.amount,
        ReadTxProjection.happened_at,
    ).where(ReadTxProjection.sync_id.in_(set(orig_by_refund.values())))
    if ledger_ids:
        tx_stmt = tx_stmt.where(ReadTxProjection.ledger_id.in_(ledger_ids))
    label_by_orig: dict[str, str] = {}
    for oid, note, category_name, amount, happened_at in db.execute(tx_stmt).all():
        label = (note or "").strip() or (category_name or "").strip()
        if not label:
            amt = f"{float(amount or 0):,.2f}".rstrip("0").rstrip(".")
            day = happened_at.strftime("%Y-%m-%d") if hasattr(happened_at, "strftime") else ""
            label = f"{amt} · {day}" if day else amt
        label_by_orig[str(oid)] = label

    out: dict[str, str] = {}
    for tx_sid, entity_id in entity_by_tx.items():
        oid = orig_by_refund.get(entity_id)
        if not oid:
            continue
        label = label_by_orig.get(str(oid))
        if label:
            out[tx_sid] = label
    return out


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _projection_totals(
    db: Session, ledger_internal_id: str
) -> tuple[int, float, float, float, datetime | None]:
    """从 read_tx_projection 聚合出 (count, income_total, expense_total, latest)。
    SQLite / PostgreSQL 通用:用 SQLAlchemy 的 `case` 做条件 sum。

    账本维度口径(交易级多币种,0018):折账本本位币,读 native_amount,
    NULL(旧 App 推的 / 存量)回退 amount。单币种账本 native==amount,结果不变。
    账户维度(workspace/accounts 按 account_sync_id 聚合)仍读 amount 原币,不要仿此改。

    收支 SUM 排除 exclude_from_stats=True 的标记笔(#340 D1,补 0017 的遗漏:
    此前只有 analytics 过滤了,账本卡片没过滤 → 两处统计对不上)。tx_count /
    latest 不过滤 —— D1 语义:标记只排收支金额,仍计入账单列表与笔数。

    「支出统计不含已退款」:expense_total 再扣掉这些账本下支出原单的有效
    退款(退款维度 refunded_total,折本位币)。账本/全局维度不看退款去向
    哪张卡 —— 钱已退回用户,支出就没真实发生;「退回本卡才算」的卡维度口径
    只用于账户页/流水删除线(_refunded_to_account)。balance_all 不动:
    「余额=钱的位置」是交易现金流口径,退款不是交易(D5)。

    退款只认**已到账**(received)—— pending 的钱还没回到用户手里,此时支出
    仍真实发生、回款 income 也未生成(见 _refund_totals_for_txs 的
    received_only),统计若提前冲减就会与余额给出相反结论。

    返回 (count, income_ex, expense_ex, balance_all, latest):balance_all 是
    **不排除**标记笔的收支差 —— 「余额=钱的位置」必须含标记笔(D5,与 App
    getLedgerStats 口径一致),否则跨端余额对不上。"""
    from sqlalchemy import case as sa_case
    from sqlalchemy import false as sa_false

    _native = func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
    _counted = ReadTxProjection.exclude_from_stats == sa_false()
    row = db.execute(
        select(
            func.count(ReadTxProjection.sync_id),
            func.coalesce(func.sum(
                sa_case(((ReadTxProjection.tx_type == "income") & _counted, _native),
                        else_=0.0)
            ), 0.0),
            func.coalesce(func.sum(
                sa_case(((ReadTxProjection.tx_type == "expense") & _counted, _native),
                        else_=0.0)
            ), 0.0),
            # balance_all:不排除标记笔(D5「余额=钱的位置」)
            func.coalesce(func.sum(
                sa_case(
                    (ReadTxProjection.tx_type == "income", _native),
                    (ReadTxProjection.tx_type == "expense", -_native),
                    else_=0.0,
                )
            ), 0.0),
            func.max(ReadTxProjection.happened_at),
        ).where(ReadTxProjection.ledger_id == ledger_internal_id)
    ).one()
    tx_count, income_total, expense_total, balance_all, latest_raw = row
    # 支出扣已退款(全局口径):只有存在退款时才多两次轻查询,无退款的账本
    # 零额外开销。标记笔与上面的 _counted 一致 —— 不计收支的原单,其退款
    # 也不从统计里扣。
    refund_rows = _refunded_expense_tx_rows(
        db, ledger_ids=[ledger_internal_id], received_only=True
    )
    if refund_rows:
        totals, _by_account = _refund_totals_for_txs(
            db,
            ledger_ids=[ledger_internal_id],
            tx_ids=[r.sync_id for r in refund_rows],
            received_only=True,
        )
        refunded_expense = 0.0
        for refund_row in refund_rows:
            if refund_row.exclude_from_stats:
                continue
            refunded = totals.get(refund_row.sync_id, 0.0)
            if refunded <= 0:
                continue
            refunded_expense += _refund_native(
                refunded,
                amount=float(refund_row.amount or 0.0),
                native_amount=refund_row.native_amount,
            )
        expense_total = max(0.0, expense_total - refunded_expense)
    return (
        int(tx_count or 0),
        float(income_total or 0),
        float(expense_total or 0),
        float(balance_all or 0),
        _to_utc(latest_raw) if latest_raw else None,
    )


def _refund_totals_for_txs(
    db: Session,
    *,
    ledger_ids: str | list[str] | tuple[str, ...],
    tx_ids: list[str] | None = None,
    received_only: bool = False,
) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    """0022 退款聚合(默认不含 cancelled;received_only 时只认已到账)。

    返回 ``(totals, by_account)``:

    - ``totals[original_tx_id]``:该原单有效退款合计 —— 退款管理页进度 /
      剩余可退金额用这个(退款维度:钱退没退回来);
    - ``by_account[(original_tx_id, account_sync_id)]``:按退款去向卡拆分
      (unified 退到顶层 accountId;split 按 refund_legs 各腿 accountId)。

    卡维度消费方(流水列表删除线 / 账户页支出):只认「退回本卡」的部分。跨卡
    退款 —— 统一退到别的卡 / 分别退到其他卡 —— 钱到了别的卡,原支出卡的
    支出仍然成立(余额也没回来),不能算已退款;这与卡的角度(账户余额)口径
    一致。

    ``received_only=True`` 是「钱真的回到用户手里」的口径:只统计 status=
    received 的退款。删除线 / 支出冲减 / 账户页支出一律用它 —— 回款 income
    只在 received 时生成(services/linked_transactions.py),pending 的钱还没
    到账,不该同时被算成「已退款」,否则统计说钱回来了、余额却仍扣着。
    默认 False = 含 pending 的「已申请」口径(额度占用),退款管理页进度由
    list_refunds 自己 GROUP BY,不走本函数。

    ``tx_ids=None`` 时不按原单过滤,返回这些账本下**全部**退款 —— 统计类
    端点(analytics / summary / 标签 / 预算 / 成员)事先不知道哪些原单有退款,
    传 None 一次拿全,再由调用方只对自己聚合到的行扣减。
    """
    totals: dict[str, float] = {}
    by_account: dict[tuple[str, str], float] = {}
    if isinstance(ledger_ids, str):
        ledger_ids = [ledger_ids]
    conditions = [
        ReadRefundProjection.ledger_id.in_(ledger_ids),
        ReadRefundProjection.status == "received"
        if received_only
        else ReadRefundProjection.status != "cancelled",
    ]
    if tx_ids is not None:
        if not tx_ids:
            return totals, by_account
        conditions.append(ReadRefundProjection.original_tx_id.in_(tx_ids))
    rows = db.execute(
        select(
            ReadRefundProjection.original_tx_id,
            ReadRefundProjection.amount,
            ReadRefundProjection.account_sync_id,
            ReadRefundProjection.refund_mode,
            ReadRefundProjection.refund_legs_json,
        ).where(and_(*conditions))
    ).all()
    for original_tx_id, amount, account_sync_id, refund_mode, legs_json in rows:
        amt = float(amount or 0.0)
        if amt <= 0:
            continue
        orig = str(original_tx_id)
        totals[orig] = totals.get(orig, 0.0) + amt
        if refund_mode == "split" and legs_json:
            try:
                legs = json.loads(legs_json)
            except (json.JSONDecodeError, TypeError):
                legs = None
            if isinstance(legs, list):
                for leg in legs:
                    if not isinstance(leg, dict):
                        continue
                    leg_account = str(leg.get("accountId") or "").strip()
                    leg_amount = float(leg.get("amount") or 0.0)
                    if not leg_account or leg_amount <= 0:
                        continue
                    key = (orig, leg_account)
                    by_account[key] = by_account.get(key, 0.0) + leg_amount
                continue
        # unified / 0025 之前的老数据 / split 腿解析失败:整笔记到顶层去向
        # 账户(可为空串 —— pending 未选账户 / 脏数据,匹配不上任何真实卡)。
        key = (orig, str(account_sync_id or ""))
        by_account[key] = by_account.get(key, 0.0) + amt
    return totals, by_account


def _refunded_to_account(
    row: Any,
    *,
    totals: dict[str, float],
    by_account: dict[tuple[str, str], float],
) -> float:
    """单行交易的「退回本卡」退款额(卡维度,配合 _refund_totals_for_txs)。

    原交易有账户:取 ``by_account[(sync_id, account_sync_id)]``,没有去向
    本卡的退款就是 0 —— 跨卡退款不计入,本卡支出仍成立。
    原交易无账户(脏数据 / 极老数据):无从判断卡片,退化为总口径 totals,
    保持与旧版删除线行为一致,不凭空改判。
    """
    account_sync_id = getattr(row, "account_sync_id", None)
    if account_sync_id:
        return by_account.get((row.sync_id, str(account_sync_id)), 0.0)
    return totals.get(row.sync_id, 0.0)


def _refunded_expense_tx_rows(
    db: Session,
    *,
    ledger_ids: list[str] | tuple[str, ...],
    received_only: bool = False,
) -> list[Any]:
    """有退款的**支出原单**投影行(整行,调用方按需取列)。

    「支出统计不含已退款」的公共取数层:统计端点只需要对这些行扣退款,
    无退款的行一分钱不差,不用全表扫。两次轻查询:
      1. 这些账本下有效退款的 original_tx_id 去重;
      2. 按 id 捞原单行(tx_type='expense' —— 退款只挂在支出上)。

    ``received_only=True`` 时第 1 步只认已到账(received)的退款,与
    _refund_totals_for_txs(received_only=True) 配套 —— 只有 pending 退款的
    原单直接不进结果,统计端少捞一行。

    返回的行走原单币种(amount)与账本本位币(native_amount)双轨,调用方
    按自己口径折算(见 _refund_native)。
    """
    if not ledger_ids:
        return []
    refunded_ids = set(
        db.scalars(
            select(ReadRefundProjection.original_tx_id)
            .where(
                ReadRefundProjection.ledger_id.in_(ledger_ids),
                ReadRefundProjection.status == "received"
                if received_only
                else ReadRefundProjection.status != "cancelled",
            )
            .distinct()
        ).all()
    )
    if not refunded_ids:
        return []
    return list(
        db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id.in_(ledger_ids),
                ReadTxProjection.sync_id.in_(refunded_ids),
                ReadTxProjection.tx_type == "expense",
            )
        ).all()
    )


def _refund_native(
    refund_amount: float,
    *,
    amount: float,
    native_amount: float | None,
) -> float:
    """把退款额折成账本本位币。

    退款与原单同币种(退款页按 original_amount 校验可退余额),故按原单的
    native/amount 比率折算 —— 跟 member-share-stats 折分摊额同款算法。
    native 缺失(0018 之前的老数据)/ amount=0 时比率按 1,即原额。
    """
    if native_amount is None or not amount:
        return refund_amount
    return refund_amount * (float(native_amount) / float(amount))


def _clamp_month_start_day(value: int | None) -> int:
    """月度起始日统一钳到 [1, 28](2 月安全上限);None/0 → 1(自然月)。"""
    return max(1, min(28, value or 1))


def _bucket_key(
    scope: AnalyticsScope,
    happened_at: datetime,
    tz_offset_minutes: int = 0,
    month_start_day: int = 1,
) -> str:
    """按用户本地时区把 happened_at 折成 month-bucket(YYYY-MM-DD)或 year-bucket(YYYY-MM)。

    跟 `_analytics_range` 同一原因 —— UTC 会把 CST `2026-04-16 00:00` 当成 UTC
    `2026-04-15 16:00`,落到 4/15 桶里;日历 / analytics 跟用户感知错位一天。
    `tz_offset_minutes` 跟 `_analytics_range` 同符号(JavaScript
    `-new Date().getTimezoneOffset()`),CST 传 +480。默认 0 走 UTC,跟老客户端
    行为保持一致。

    `month_start_day`(1-28):year scope 按「周期标签月」分桶 —— 本地日期
    day >= start_day 归当月,否则归上月(周期按起始月命名)。默认 1 = 自然月,
    老调用行为不变。
    """
    from datetime import timedelta

    normalized = _to_utc(happened_at)
    # 偏移到用户本地时区(naive 化),strftime 就是本地日期
    local = normalized + timedelta(minutes=tz_offset_minutes)
    if scope == "month":
        return local.strftime("%Y-%m-%d")
    day = _clamp_month_start_day(month_start_day)
    if local.day >= day:
        return local.strftime("%Y-%m")
    prev = local.replace(day=1) - timedelta(days=1)
    return prev.strftime("%Y-%m")


def _analytics_range(
    *,
    scope: AnalyticsScope,
    period: str | None,
    tz_offset_minutes: int = 0,
    month_start_day: int = 1,
) -> tuple[datetime | None, datetime | None, str | None]:
    """计算 analytics 的 [start, end) UTC 范围。

    边界对应用户**本地**的月份/年份,不是 UTC —— UTC 计算会把 CST "2026-04-01 00:00"
    当成 UTC "2026-03-31 16:00",落到 3 月,导致月初那笔支出没算进 4 月。

    `tz_offset_minutes`:客户端传的本地时区偏移(正数 = 东半球),与 JavaScript
    `-new Date().getTimezoneOffset()` 同符号。中国是 +480。默认 0 = UTC(老客户端不传
    这个参数时的行为保持 UTC 切月)。

    `month_start_day`(1-28):周期按起始月命名 —— "2026-06"(msd=10) 表示本地
    2026-06-10 ~ 2026-07-10。year scope 同理:[当年1月周期起点, 次年1月周期起点)。
    默认 1 = 自然月/年,所有行为与改造前逐位一致。
    """
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    tz = timezone(timedelta(minutes=tz_offset_minutes))

    if scope == "all":
        return None, None, None

    if scope == "month":
        day = _clamp_month_start_day(month_start_day)
        if isinstance(period, str) and period.strip():
            target = period.strip()
        else:
            # 默认 = 「当前周期」标签:今天还没到起始日时属上个标签月
            local_now = now.astimezone(tz)
            if local_now.day < day:
                local_now = local_now.replace(day=1) - timedelta(days=1)
            target = local_now.strftime("%Y-%m")
        try:
            year_part, month_part = target.split("-", 1)
            year = int(year_part)
            month = int(month_part)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="Invalid analytics period") from exc
        if month < 1 or month > 12:
            raise HTTPException(status_code=400, detail="Invalid analytics period")
        # 本地时区周期起点/终点 → 转 UTC 给 SQL 用;周期按起始月命名
        start_local = datetime(year, month, day, tzinfo=tz)
        end_local = (
            datetime(year + 1, 1, day, tzinfo=tz)
            if month == 12
            else datetime(year, month + 1, day, tzinfo=tz)
        )
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), f"{year:04d}-{month:02d}"

    day = _clamp_month_start_day(month_start_day)
    if isinstance(period, str) and period.strip():
        target = period.strip()
    else:
        local_now = now.astimezone(tz)
        target_year = local_now.year
        # 只有 1 月里还没到起始日时才归上一年度周期;2-12 月无论 day 与 msd
        # 关系如何,年标签都是当前日历年(勿仿 month scope 把借位推广到全月)。
        if local_now.month == 1 and local_now.day < day:
            target_year -= 1
        target = str(target_year)
    try:
        year = int(target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid analytics period") from exc
    start_local = datetime(year, 1, day, tzinfo=tz)
    end_local = datetime(year + 1, 1, day, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), f"{year:04d}"


# ---------------------------------------------------------------------------
# CSV 导出辅助
# ---------------------------------------------------------------------------

import re as _re

_CSV_NEEDS_QUOTE = (",", '"', "\n", "\r")
_FILENAME_BAD = _re.compile(r'[\\/:*?"<>|\r\n]')


def _csv_field(value: Any) -> str:
    """RFC 4180 字段转义。None / "" → 空;含 , " \\n \\r → 双引号包裹 + 转义。"""
    if value is None:
        return ""
    s = str(value)
    if s == "":
        return ""
    if any(c in s for c in _CSV_NEEDS_QUOTE):
        return '"' + s.replace('"', '""') + '"'
    return s


def _sanitize_filename(name: str | None, max_len: int = 64) -> str:
    """文件系统安全的文件名片段。Windows 上 / \\ : * ? \" < > | 都禁;两端空格点也清。"""
    safe = _FILENAME_BAD.sub("_", (name or "").strip()) or "ledger"
    safe = safe.strip(" .") or "ledger"
    return safe[:max_len]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


__all__ = [
    'json',
    'datetime',
    'timedelta',
    'timezone',
    'Any',
    'cast',
    'APIRouter',
    'Depends',
    'HTTPException',
    'Query',
    'and_',
    'func',
    'or_',
    'select',
    'true',
    'Session',
    'get_settings',
    'get_db',
    'get_current_user',
    'require_any_scopes',
    'require_scopes',
    'get_accessible_ledger_by_external_id',
    'AttachmentFile',
    'Ledger',
    'LedgerMember',
    'ReadBudgetProjection',
    'ReadItemProjection',
    'ReadTxProjection',
    'UserAccountProjection',
    'UserCategoryProjection',
    'UserTagProjection',
    'SyncChange',
    'User',
    'UserProfile',
    'AnalyticsMetric',
    'AnalyticsScope',
    'ReadAccountOut',
    'ReadBudgetOut',
    'ReadBudgetUsageItemOut',
    'ReadBudgetUsageOut',
    'ReadCategoryOut',
    'ReadLedgerDetailOut',
    'ReadLedgerOut',
    'ReadSummaryOut',
    'ReadTagOut',
    'ReadTransactionOut',
    'WorkspaceAccountOut',
    'WorkspaceAnalyticsAnomalyAttributionOut',
    'WorkspaceAnalyticsAnomalyMonthOut',
    'WorkspaceAnalyticsCategoryRankOut',
    'WorkspaceAnalyticsOut',
    'WorkspaceAnalyticsRangeOut',
    'WorkspaceAnalyticsSeriesItemOut',
    'WorkspaceAnalyticsSummaryOut',
    'WorkspaceCategoryOut',
    'WorkspaceLedgerCountsOut',
    'WorkspaceTagOut',
    'WorkspaceTransactionOut',
    'WorkspaceTransactionPageOut',
    'SCOPE_APP_WRITE',
    'SCOPE_WEB_READ',
    'snapshot_cache',
    'router',
    'settings',
    '_READ_SCOPE_DEP',
    '_is_admin',
    '_require_ledger',
    '_get_latest_snapshot',
    '_get_latest_change_id',
    '_owner_map_for_ledgers',
    '_user_info_map',
    '_is_ledger_deleted',
    '_visible_workspace_ledgers',
    '_snapshot_ledger_info',
    '_resolve_ledger_name',
    '_load_owner_identity',
    '_tags_list',
    '_tx_extension_fields',
    'item_names_by_sync_id',
    'time_deposit_names_by_sync_id',
    'refund_source_labels_by_tx_sync_id',
    '_to_utc',
    '_projection_totals',
    '_refund_totals_for_txs',
    '_refunded_to_account',
    '_refunded_expense_tx_rows',
    '_refund_native',
    '_bucket_key',
    '_analytics_range',
    '_csv_field',
    '_sanitize_filename',
]
