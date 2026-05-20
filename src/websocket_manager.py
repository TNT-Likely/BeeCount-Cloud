import json
import logging
from collections import defaultdict
from collections.abc import Iterable

from fastapi import WebSocket

from .metrics import metrics

logger = logging.getLogger(__name__)


class WSConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[user_id].add(websocket)
        metrics.set_gauge("beecount_online_ws_users", float(len(self._connections)))

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        if user_id in self._connections:
            self._connections[user_id].discard(websocket)
            if not self._connections[user_id]:
                del self._connections[user_id]
        metrics.set_gauge("beecount_online_ws_users", float(len(self._connections)))

    async def broadcast_to_user(self, user_id: str, payload: dict) -> None:
        stale: list[WebSocket] = []
        conns = self._connections.get(user_id, set())
        for ws in conns:
            try:
                await ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))
            except Exception:
                stale.append(ws)

        if conns:
            logger.info(
                "ws.broadcast user=%s type=%s sockets=%d stale=%d",
                user_id,
                payload.get("type"),
                len(conns),
                len(stale),
            )

        for ws in stale:
            self.disconnect(user_id, ws)

    def online_user_ids(self) -> Iterable[str]:
        return self._connections.keys()


async def broadcast_to_ledger(
    *,
    db,
    ws_manager: WSConnectionManager,
    ledger_id: int,
    payload: dict,
    exclude_user_id: str | None = None,
    extra_user_ids: list[str] | None = None,
) -> None:
    """共享账本 fan-out:把 payload 广播给该 ledger 所有 LedgerMember。

    ``ledger_id`` 必须是 ``Ledger.id`` (INT 主键),不是 ``external_id`` UUID
    字符串 — LedgerMember.ledger_id 是 INT 外键。传 external_id 会让查询匹
    配不到任何成员,fan-out 静默失败。

    ``exclude_user_id`` 可用于跳过某个特定用户(如 push 调用方,避免回播给
    自己,虽然 mobile 端有 device_id 去重一般不需要)。
    ``extra_user_ids`` 用于"被踢的用户"场景:已经从 LedgerMember 删了,但仍
    需要通知一次让 client 触发本地清理。
    """
    from .ledger_access import list_ledger_members

    targets: set[str] = set()
    for member_user_id, _role in list_ledger_members(db, ledger_id=ledger_id):
        targets.add(member_user_id)
    if extra_user_ids:
        targets.update(extra_user_ids)
    if exclude_user_id:
        targets.discard(exclude_user_id)
    logger.info(
        "ws.fanout.ledger ledger_id=%s targets=%d type=%s payload_keys=%s",
        ledger_id,
        len(targets),
        payload.get("type"),
        list(payload.keys()),
    )
    for uid in targets:
        await ws_manager.broadcast_to_user(uid, payload)


async def broadcast_to_user_ledgers(
    *,
    db,
    ws_manager: WSConnectionManager,
    user_id: str,
    payload: dict,
    exclude_self: bool = True,
) -> None:
    """跨账本 fan-out:user-global change 时,推给该 user 作为 owner 的所有共享
    账本的非 owner member(即 Editor)。

    `exclude_self=True`(默认)排除 actor 自己 — 同设备 push 收到推送会被 device_id
    去重,但跨设备(用户在 mobile 推,自己 web 同步)需要推。所以推所有 member
    包含 actor 自己,由 client 用 device_id 去重。设置 False 关闭去重。
    """
    from sqlalchemy import select, func
    from .models import Ledger, LedgerMember

    # 该用户作为 owner 的 ledger,且 member_count > 1(共享账本)
    rows = db.execute(
        select(Ledger.id, Ledger.external_id, func.count(LedgerMember.user_id).label("cnt"))
        .join(LedgerMember, LedgerMember.ledger_id == Ledger.id)
        .where(Ledger.user_id == user_id)
        .group_by(Ledger.id, Ledger.external_id)
        .having(func.count(LedgerMember.user_id) > 1)
    ).all()
    shared_ledger_ids = [(r.id, r.external_id) for r in rows]

    for lid, _ext in shared_ledger_ids:
        # 推该 ledger 所有非 owner member
        from .ledger_access import list_ledger_members
        for member_user_id, role in list_ledger_members(db, ledger_id=lid):
            if role == "owner":
                continue
            payload_with_ledger = dict(payload)
            payload_with_ledger.setdefault("ledgerId", _ext)
            await ws_manager.broadcast_to_user(member_user_id, payload_with_ledger)
