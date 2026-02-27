import json
from collections import defaultdict
from collections.abc import Iterable

from fastapi import WebSocket

from .metrics import metrics


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
        for ws in self._connections.get(user_id, set()):
            try:
                await ws.send_text(json.dumps(payload, ensure_ascii=False, default=str))
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.disconnect(user_id, ws)

    def online_user_ids(self) -> Iterable[str]:
        return self._connections.keys()
