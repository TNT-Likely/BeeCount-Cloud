import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Iterable

from fastapi import WebSocket
from sqlalchemy.engine import URL

from .metrics import metrics
from .ws_relay import PAGE_SIZE, WebSocketRelay

logger = logging.getLogger(__name__)


class WSConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, dict[WebSocket, int]] = defaultdict(dict)
        self._relay: WebSocketRelay | None = None
        self._listener: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._subscribe = asyncio.Lock()

    def start(self, url: URL) -> None:
        # An in-memory SQLite database belongs to a single process.
        if url.get_backend_name() == "sqlite" and (
            url.database in (None, "", ":memory:") or url.query.get("mode") == "memory"
        ):
            return
        self._relay = WebSocketRelay(url)
        self._wake = asyncio.Event()

    async def stop(self) -> None:
        task, self._listener = self._listener, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._relay is not None:
            await asyncio.to_thread(self._relay.engine.dispose)
            self._relay = None
        self._wake = None

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        # Subscribe before accepting the socket so a write during the handshake
        # will be included. A new socket should never receive older queued frames.
        async with self._subscribe:
            cursor = await asyncio.to_thread(self._relay.latest) if self._relay else 0
            await websocket.accept()
            self._connections[user_id][websocket] = cursor
            if self._relay and self._listener is None:
                self._listener = asyncio.create_task(self._listen(cursor))
        metrics.set_gauge("beecount_online_ws_users", float(len(self._connections)))

    def disconnect(self, user_id: str, websocket: WebSocket) -> None:
        if user_id in self._connections:
            self._connections[user_id].pop(websocket, None)
            if not self._connections[user_id]:
                del self._connections[user_id]
        if not self._connections and self._listener is not None:
            self._listener.cancel()
            self._listener = None
        metrics.set_gauge("beecount_online_ws_users", float(len(self._connections)))

    async def broadcast_to_user(
        self, user_id: str, payload: dict, *, shared_owner_id: str | None = None
    ) -> None:
        message = json.dumps(payload, ensure_ascii=False, default=str)
        if self._relay is not None and self._wake is not None:
            try:
                await asyncio.to_thread(self._relay.publish, user_id, message, shared_owner_id)
                self._wake.set()
                return
            except Exception:
                # The business transaction has already committed. A relay outage
                # should not turn it into an apparent failed write for the caller.
                logger.exception("ws.relay.publish_failed")
        if shared_owner_id is not None:
            # A delayed incremental body can be stale. Existing clients reconcile
            # their shared-resource snapshot when the connection is re-established.
            sockets = list(self._connections.get(user_id, {}))
            await asyncio.gather(*(self._reconnect(user_id, ws) for ws in sockets))
            return
        await self._send_local(user_id, message)

    async def _listen(self, cursor: int) -> None:
        assert self._relay is not None and self._wake is not None
        while True:
            self._wake.clear()
            try:
                rows = await asyncio.to_thread(self._relay.read_after, cursor)
                if rows and rows[0].id > cursor + 1:
                    # A slow worker fell behind retention (or a sequence rolled
                    # back). Reconnect so clients run their normal initial pull.
                    async with self._subscribe:
                        lagging = [(user, ws) for user, group in self._connections.items()
                                   for ws, since in group.items() if since < rows[0].id - 1]
                    await asyncio.gather(*(self._reconnect(user, ws) for user, ws in lagging))
                for frame in rows:
                    await self._send_local(frame.user_id, frame.payload, frame.id)
                    cursor = frame.id
                if len(rows) == PAGE_SIZE:
                    continue
            except Exception:
                logger.exception("ws.relay.read_failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except TimeoutError:
                pass

    async def _send_local(self, user_id: str, message: str, frame_id: int | None = None) -> None:
        async with self._subscribe:
            recipients = [ws for ws, since in self._connections.get(user_id, {}).items()
                          if frame_id is None or frame_id > since]

        async def send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_text(message), timeout=3)
            except Exception:
                await self._reconnect(user_id, ws)

        await asyncio.gather(*(send(ws) for ws in recipients))

    async def _reconnect(self, user_id: str, ws: WebSocket) -> None:
        try:
            await asyncio.wait_for(ws.close(code=1012), timeout=1)
        except Exception:
            pass
        finally:
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
    from sqlalchemy import func, select

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
