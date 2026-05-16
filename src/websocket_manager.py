import json
import logging
from collections import defaultdict
from collections.abc import Iterable

from fastapi import WebSocket
from sqlalchemy.orm import Session

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
    db: Session,
    ws_manager: "WSConnectionManager",
    ledger_id: str,
    payload: dict,
    extra_user_ids: Iterable[str] | None = None,
) -> None:
    """共享账本 fan-out:把 payload 推给该账本所有 member。

    替代历史上的 `broadcast_to_user(ledger.user_id, ...)` 单 owner 推送 — 现在
    一个 ledger 可能有多个 member,WS 事件必须 fan-out 给所有人。
    成员数 ≤ 5(Phase 1 上限)时 N×单播性能毫无问题。

    extra_user_ids:在常规 member 列表外强制再推几个用户。给"踢人"场景用 ——
    被踢用户已经从 ledger_members 删除,但 client 端还在监听,需要收到
    `member_change.removed` 才能本地清理(否则只能等下一次 reconnect)。

    放在模块底层,避免 WSConnectionManager 强耦合 ORM。
    """
    # 局部 import 避免 circular(ledger_access 不导回 WS)
    from .ledger_access import list_ledger_member_user_ids
    user_ids = set(list_ledger_member_user_ids(db, ledger_id=ledger_id))
    if extra_user_ids:
        user_ids.update(extra_user_ids)
    for uid in user_ids:
        await ws_manager.broadcast_to_user(uid, payload)
