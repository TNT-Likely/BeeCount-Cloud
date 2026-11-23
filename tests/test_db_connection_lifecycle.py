from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src import database, deps
from src.models import Device, User
from src.routers import ws
from src.security import SCOPE_WEB_READ, SCOPE_WEB_WRITE, create_access_token
from src.websocket_manager import WSConnectionManager

USER_ID = "pool-test-user"
DEVICE_ID = "pool-test-device"
INITIAL_LAST_SEEN = datetime(2020, 1, 1)


@pytest.fixture
def one_connection_pool(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pool.db'}",
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        db.add(User(id=USER_ID, email="pool-test@example.test", password_hash="unused"))
        db.flush()
        db.add(Device(id=DEVICE_ID, user_id=USER_ID, last_seen_at=INITIAL_LAST_SEEN))
    try:
        yield engine, sessions
    finally:
        engine.dispose()


def test_websocket_waiting_for_db_keeps_event_loop_available(one_connection_pool, monkeypatch):
    engine, sessions = one_connection_pool
    lookup_started = Event()

    def start_lookup():
        lookup_started.set()
        return sessions()

    monkeypatch.setattr(ws, "SessionLocal", start_lookup)
    app = FastAPI()
    app.state.ws_manager = WSConnectionManager()
    app.include_router(ws.router)
    token, _ = create_access_token(USER_ID, scopes=[SCOPE_WEB_WRITE], client_type="web")

    def handshake(client: TestClient):
        with client.websocket_connect("/ws", params={"token": token}) as socket:
            socket.send_json({"type": "ping"})
            return socket.receive_json()

    with engine.connect() as occupied, TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as threads:
        @app.get("/release-connection")
        async def release_connection():
            occupied.close()

        socket_reply = threads.submit(handshake, client)
        assert lookup_started.wait(5)
        assert client.get("/release-connection").status_code == 200
        assert socket_reply.result(timeout=5) == {"type": "pong"}


def test_authenticated_device_activity_waits_until_request_session_is_closed(one_connection_pool, monkeypatch):
    _, sessions = one_connection_pool
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(deps, "SessionLocal", sessions)
    monkeypatch.setattr(deps, "_BUMP_CACHE", {})
    app = FastAPI()
    token, _ = create_access_token(USER_ID, scopes=[SCOPE_WEB_READ], client_type="web")

    @app.get("/profile")
    def profile(user: User = Depends(deps.get_current_user)):
        return {"id": user.id}

    with TestClient(app) as client:
        reply = client.get("/profile", headers={"Authorization": f"Bearer {token}", "X-Device-ID": DEVICE_ID})
        assert reply.status_code == 200
        assert reply.json() == {"id": USER_ID}

    with sessions() as db:
        device = db.get(Device, DEVICE_ID)
        assert device is not None and device.last_seen_at > INITIAL_LAST_SEEN
