"""共享账本成员收支统计 + 自定义成员维度统计 endpoint。

GET /api/v1/ledgers/{ledger_external_id}/member-stats
  ?scope=month|year|all  &period=&tz_offset_minutes=
  → 按 read_tx_projection.created_by_user_id 维度(共享账本**登录用户**成员)

GET /api/v1/ledgers/{ledger_external_id}/member-share-stats
  ?scope=month|year|all  &period=&tz_offset_minutes=
  → 0024 自定义成员维度(账本内的人名,如 爸爸/妈妈/我)。口径:
      * 支出带 member_shares → 按各成员的分摊金额计入该成员
      * 没有分摊的交易     → 按 created_by 记账人归属(兼容旧数据)

口径说明:**谁记的就算谁花的 / 挣的**,编辑只改字段不改归属。
跳过 transfer:transfer tx 既非 income 也非 expense,不计入。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..ledger_access import (
    list_ledger_members,
    require_accessible_ledger_by_external_id,
)
from ..models import ReadMemberProjection, ReadTxProjection, User
from ..schemas import AnalyticsScope
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from .read._shared import (
    _analytics_range,
    _refund_native,
    _refund_totals_for_txs,
    _refunded_expense_tx_rows,
    _to_utc,
    _user_info_map,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ)


class MemberStatItem(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    avatar_url: str | None
    avatar_version: int = 0
    role: str
    income_total: float = 0.0
    expense_total: float = 0.0
    tx_count: int = 0


class MemberStatsResponse(BaseModel):
    ledger_id: str
    ledger_currency: str
    scope: AnalyticsScope
    period: str | None
    start_at: datetime | None
    end_at: datetime | None
    items: list[MemberStatItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 0024 自定义成员维度统计                                                      #
# --------------------------------------------------------------------------- #

class MemberShareStatItem(BaseModel):
    """成员维度统计的一行。

    kind 区分来源:
      - 'member' = 账本内自定义成员(通过 tx.member_shares 分摊进来)
      - 'user'   = 记账人(没有分摊的交易按 created_by 归属,兼容旧数据)
    两者混排展示,UI 可按需分组。
    """

    kind: str
    id: str
    name: str
    income_total: float = 0.0
    expense_total: float = 0.0
    tx_count: int = 0


class MemberShareStatsResponse(BaseModel):
    ledger_id: str
    ledger_currency: str
    scope: AnalyticsScope
    period: str | None
    start_at: datetime | None
    end_at: datetime | None
    items: list[MemberShareStatItem] = Field(default_factory=list)


@router.get(
    "/ledgers/{ledger_external_id}/member-stats",
    response_model=MemberStatsResponse,
)
def get_member_stats(
    ledger_external_id: str,
    scope: AnalyticsScope = Query(default="month"),
    period: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberStatsResponse:
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )

    start_at, end_at, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,
        month_start_day=ledger.month_start_day or 1,
    )

    # GROUP BY created_by_user_id 一次聚合 income / expense / tx_count。
    # 账本维度(单账本内跨成员/账户求和,响应按 ledger_currency 展示)→ 折
    # 账本本位币 native_amount(?? amount);排除「不计收支」标记笔,与
    # _projection_totals/analytics/标签聚合口径一致(否则多币种共享账本里
    # 成员金额错、排序错、与账本卡片对不上)。
    from sqlalchemy import false as sa_false

    _native = func.coalesce(ReadTxProjection.native_amount, ReadTxProjection.amount)
    q = select(
        ReadTxProjection.created_by_user_id,
        func.sum(
            case(
                (ReadTxProjection.tx_type == "income", _native),
                else_=0.0,
            )
        ).label("income_total"),
        func.sum(
            case(
                (ReadTxProjection.tx_type == "expense", _native),
                else_=0.0,
            )
        ).label("expense_total"),
        func.count().label("tx_count"),
    ).where(
        ReadTxProjection.ledger_id == ledger.id,
        ReadTxProjection.tx_type.in_(("income", "expense")),  # 跳过 transfer
        ReadTxProjection.created_by_user_id.is_not(None),
        ReadTxProjection.exclude_from_stats == sa_false(),
    )
    if start_at is not None:
        q = q.where(ReadTxProjection.happened_at >= start_at)
    if end_at is not None:
        q = q.where(ReadTxProjection.happened_at < end_at)
    q = q.group_by(ReadTxProjection.created_by_user_id)

    rows = db.execute(q).all()
    stats_by_uid: dict[str, tuple[float, float, int]] = {
        row[0]: (float(row[1] or 0.0), float(row[2] or 0.0), int(row[3] or 0))
        for row in rows
    }

    # 「支出统计不含已退款」—— 记账人维度的支出扣有效退款(退款维度,
    # 折本位币):退款挂在原单上,原单记在谁名下就扣谁的。tx_count 不变。
    refund_rows = _refunded_expense_tx_rows(
        db, ledger_ids=[ledger.id], received_only=True
    )
    if refund_rows:
        refund_totals, _by_account = _refund_totals_for_txs(
            db, ledger_ids=[ledger.id], tx_ids=[r.sync_id for r in refund_rows],
            received_only=True,
        )
        for row in refund_rows:
            if row.exclude_from_stats or not row.created_by_user_id:
                continue
            happened_at = _to_utc(row.happened_at) if row.happened_at else None
            if happened_at is None:
                continue
            if start_at is not None and happened_at < start_at:
                continue
            if end_at is not None and happened_at >= end_at:
                continue
            refunded = refund_totals.get(row.sync_id, 0.0)
            if refunded <= 0:
                continue
            refunded_native = _refund_native(
                refunded,
                amount=float(row.amount or 0.0),
                native_amount=row.native_amount,
            )
            uid = row.created_by_user_id
            if uid in stats_by_uid and refunded_native > 0:
                income, expense, count = stats_by_uid[uid]
                stats_by_uid[uid] = (
                    income, max(0.0, expense - refunded_native), count)

    # 拿全成员名单(包括本期无记账的) + 用户信息批量
    members = list_ledger_members(db, ledger_id=ledger.id)
    member_user_ids = {uid for uid, _role in members}
    # rows 里 created_by 可能不在 LedgerMember(被踢的老成员的 tx 仍保留),
    # 也算上 — 显示成员历史贡献。
    all_user_ids = member_user_ids | set(stats_by_uid.keys())
    user_info = _user_info_map(db, all_user_ids)
    role_by_uid = {uid: role for uid, role in members}

    items: list[MemberStatItem] = []
    for uid in all_user_ids:
        info = user_info.get(uid, (None, None, None, 0))
        email, display_name, avatar_file_id, avatar_version = info
        avatar_url = (
            f"/api/v1/profile/avatar/{uid}?v={avatar_version}"
            if avatar_file_id
            else None
        )
        income, expense, tx_count = stats_by_uid.get(uid, (0.0, 0.0, 0))
        items.append(
            MemberStatItem(
                user_id=uid,
                email=email,
                display_name=display_name,
                avatar_url=avatar_url,
                avatar_version=avatar_version if avatar_file_id else 0,
                # 老成员被踢后 LedgerMember 里没了,但 tx 还在,标 "removed"
                role=role_by_uid.get(uid, "removed"),
                income_total=income,
                expense_total=expense,
                tx_count=tx_count,
            )
        )

    # 排序:支出降序、收入降序、笔数降序、user_id 兜底
    items.sort(
        key=lambda x: (-x.expense_total, -x.income_total, -x.tx_count, x.user_id)
    )

    logger.info(
        "member-stats.get ledger=%s scope=%s period=%s rows=%d caller=%s",
        ledger_external_id,
        scope,
        normalized_period,
        len(items),
        current_user.id,
    )

    return MemberStatsResponse(
        ledger_id=ledger_external_id,
        ledger_currency=ledger.currency or "CNY",
        scope=scope,
        period=normalized_period,
        start_at=start_at,
        end_at=end_at,
        items=items,
    )


def _linked_user_member_map(db: Session, *, ledger_id: str) -> dict[str, str]:
    """本账本内 {登录用户 id → 成员 sync_id}(只含关联了记账人的成员)。

    一个用户理论上只被一个成员关联;真出现重复(并发 / 历史数据)时取
    sync_id 最小的一个,保证口径稳定、不因行序抖动。
    """
    rows = db.execute(
        select(ReadMemberProjection.linked_user_id, ReadMemberProjection.sync_id).where(
            ReadMemberProjection.ledger_id == ledger_id,
            ReadMemberProjection.linked_user_id.is_not(None),
        )
    ).all()
    out: dict[str, str] = {}
    for linked_user_id, sync_id in rows:
        if not linked_user_id or not sync_id:
            continue
        prev = out.get(linked_user_id)
        if prev is None or sync_id < prev:
            out[linked_user_id] = sync_id
    return out


@router.get(
    "/ledgers/{ledger_external_id}/member-share-stats",
    response_model=MemberShareStatsResponse,
)
def get_member_share_stats(
    ledger_external_id: str,
    scope: AnalyticsScope = Query(default="month"),
    period: str | None = Query(default=None),
    tz_offset_minutes: int = Query(default=0, ge=-720, le=840),
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberShareStatsResponse:
    """0024 自定义成员维度统计。

    口径(用户确认):
      - **有分摊**的支出:按 member_shares 里各成员的金额计入该成员;
      - **没有分摊**的交易:按 created_by(记账人)归属,兼容历史数据。
    income 同样处理(收入也可以分摊);transfer 跳过。

    「支出统计不含已退款」:支出按 refunded_total/amount 的占比等比例扣
    (退款记录不带成员维度,按各成员份额比例分摊是最自然的分法)。

    多币种:shares 里的金额是**原币**口径,这里按 tx 的 native/amount 比率
    折成账本本位币,跟其它统计端点(折 native_amount)保持一致。
    """
    ledger, _role = require_accessible_ledger_by_external_id(
        db,
        user_id=current_user.id,
        ledger_external_id=ledger_external_id,
    )
    start_at, end_at, normalized_period = _analytics_range(
        scope=scope, period=period, tz_offset_minutes=tz_offset_minutes,
        month_start_day=ledger.month_start_day or 1,
    )

    from sqlalchemy import false as sa_false

    stmt = select(
        ReadTxProjection.sync_id,
        ReadTxProjection.tx_type,
        ReadTxProjection.amount,
        ReadTxProjection.native_amount,
        ReadTxProjection.member_shares_json,
        ReadTxProjection.created_by_user_id,
    ).where(
        ReadTxProjection.ledger_id == ledger.id,
        ReadTxProjection.tx_type.in_(("income", "expense")),
        ReadTxProjection.exclude_from_stats == sa_false(),
    )
    if start_at is not None:
        stmt = stmt.where(ReadTxProjection.happened_at >= start_at)
    if end_at is not None:
        stmt = stmt.where(ReadTxProjection.happened_at < end_at)
    rows = db.execute(stmt).all()

    # 「支出统计不含已退款」取数:整本一次查询拿退款维度合计(只给支出原单
    # 用,收入 / 无退款的原单查不到即为 0)。
    refund_totals, _refund_by_account = _refund_totals_for_txs(
        db, ledger_ids=[ledger.id], received_only=True
    )

    # key = ('member'|'user', id) → [income, expense, count]
    buckets: dict[tuple[str, str], list] = {}

    def bucket(kind: str, ident: str) -> list:
        return buckets.setdefault((kind, ident), [0.0, 0.0, 0])

    user_ids: set[str] = set()
    # 成员名兜底字典:从份额快照里取。成员被删除后投影表查不到名字,但历史
    # 份额里存了当时的 memberName —— 用它避免统计行显示成裸 sync_id。
    fallback_name_by_member: dict[str, str] = {}
    # 记账人 → 成员:成员被关联了登录用户(members.linked_user_id)之后,该
    # 用户记的、没有分摊的交易**并入该成员**,不再单独出一条 kind='user'。
    # 否则同一个人会在「成员」和「记账人」两个维度里各算一遍(重复计数)。
    member_id_by_linked_user = _linked_user_member_map(db, ledger_id=ledger.id)
    for sync_id, tx_type, amount, native_amount, shares_json, created_by in rows:
        raw_amount = float(amount or 0.0)
        # 折本位币:无 native 快照时 native == amount。
        base_amount = float(native_amount) if native_amount is not None else raw_amount
        # 退款与原单同币种,按金额占比从支出里扣;退款记录不带成员维度,
        # 等比例摊到每个成员的份额是最自然的分法(收入永无退款)。
        refund_ratio = 0.0
        if tx_type == "expense" and raw_amount > 0:
            refund_ratio = min(
                1.0, max(0.0, refund_totals.get(sync_id, 0.0) / raw_amount)
            )
        shares = None
        if tx_type == "expense" and shares_json:
            try:
                parsed = json.loads(shares_json)
                if isinstance(parsed, list) and parsed:
                    shares = parsed
            except (json.JSONDecodeError, TypeError):
                shares = None

        if shares:
            # 按人头分摊:每份金额按 native/amount 比率折本位币。
            factor = (base_amount / raw_amount) if raw_amount else 1.0
            for share in shares:
                if not isinstance(share, dict):
                    continue
                member_id = share.get("memberId") or share.get("member_id") or ""
                share_amount = float(share.get("amount") or 0.0)
                if not member_id or share_amount == 0:
                    continue
                snapshot_name = share.get("memberName") or share.get("member_name") or ""
                if snapshot_name and member_id not in fallback_name_by_member:
                    fallback_name_by_member[member_id] = snapshot_name
                b = bucket("member", member_id)
                b[1] += share_amount * factor * (1.0 - refund_ratio)   # 分摊只可能是支出
                b[2] += 1
            continue

        # 没有分摊 → 归记账人;该记账人若已被成员关联,则并入该成员。
        if not created_by:
            continue
        linked_member = member_id_by_linked_user.get(created_by)
        if linked_member:
            b = bucket("member", linked_member)
        else:
            b = bucket("user", created_by)
            user_ids.add(created_by)
        if tx_type == "income":
            b[0] += base_amount
        else:
            b[1] += base_amount * (1.0 - refund_ratio)
        b[2] += 1

    # 名字解析:自定义成员查投影表;记账人查 user profile。
    member_ids = [ident for (kind, ident) in buckets if kind == "member"]
    name_by_member = {}
    if member_ids:
        mrows = db.scalars(
            select(ReadMemberProjection).where(
                ReadMemberProjection.ledger_id == ledger.id,
                ReadMemberProjection.sync_id.in_(member_ids),
            )
        ).all()
        name_by_member = {r.sync_id: (r.name or "") for r in mrows}

    user_info = _user_info_map(db, user_ids) if user_ids else {}

    items: list[MemberShareStatItem] = []
    for (kind, ident), (income, expense, count) in buckets.items():
        if kind == "member":
            # 成员已被删除时回退成份额快照里的人名,再不行才是 id。
            name = (
                name_by_member.get(ident)
                or fallback_name_by_member.get(ident)
                or ident
            )
        else:
            email, display_name, _avatar, _ver = user_info.get(
                ident, (None, None, None, 0)
            )
            name = display_name or (email.split("@")[0] if email else ident)
        items.append(
            MemberShareStatItem(
                kind=kind,
                id=ident,
                name=name,
                income_total=income,
                expense_total=expense,
                tx_count=int(count),
            )
        )

    items.sort(key=lambda x: (-x.expense_total, -x.income_total, -x.tx_count, x.id))

    logger.info(
        "member-share-stats.get ledger=%s scope=%s period=%s rows=%d caller=%s",
        ledger_external_id, scope, normalized_period, len(items), current_user.id,
    )

    return MemberShareStatsResponse(
        ledger_id=ledger_external_id,
        ledger_currency=ledger.currency or "CNY",
        scope=scope,
        period=normalized_period,
        start_at=start_at,
        end_at=end_at,
        items=items,
    )
