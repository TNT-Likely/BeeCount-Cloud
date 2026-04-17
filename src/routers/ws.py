from fastapi import APIRouter, Query, WebSocket
from sqlalchemy import select

from ..database import SessionLocal
from ..models import User
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE, decode_token

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default="")) -> None:
    if not token:
        await websocket.close(code=1008)
        return

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            await websocket.close(code=1008)
            return
        scopes = payload.get("scopes", [])
        if not isinstance(scopes, list):
            await websocket.close(code=1008)
            return
        normalized = {str(scope) for scope in scopes if isinstance(scope, str)}
        if SCOPE_APP_WRITE not in normalized and SCOPE_WEB_WRITE not in normalized:
            await websocket.close(code=1008)
            return
        user_id = payload.get("sub")
        if not user_id:
            await websocket.close(code=1008)
            return
    except Exception:
        await websocket.close(code=1008)
        return

    db = SessionLocal()
    user = db.scalar(select(User).where(User.id == user_id))
    db.close()
    if user is None:
        await websocket.close(code=1008)
        return

    manager = websocket.app.state.ws_manager
    await manager.connect(user_id, websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            # Support client-initiated heartbeat: the client sends {"type":"ping"}
            # every ~25s and waits for a pong. If the socket is silently broken,
            # the pong won't arrive and the client's no-frames timer forces a
            # reconnect. Tolerate malformed payloads silently.
            if msg and '"ping"' in msg:
                try:
                    await websocket.send_text('{"type":"pong"}')
                except Exception:
                    break
    except Exception:
        pass
    finally:
        manager.disconnect(user_id, websocket)
