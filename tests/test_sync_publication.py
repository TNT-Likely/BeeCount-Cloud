import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql, sqlite

from src import projection, ws_relay
from src.models import UserTagProjection, WebSocketFrame
from src.websocket_manager import WSConnectionManager


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_projection_upsert_compiles_with_the_bound_database(dialect):
    db = Mock()
    db.get_bind.return_value.dialect = dialect
    projection._upsert(
        db,
        UserTagProjection,
        ("user_id", "sync_id"),
        {
            "user_id": "owner",
            "sync_id": "tag",
            "name": "new",
        },
    )
    statement = db.execute.call_args.args[0]
    assert "ON CONFLICT (user_id, sync_id) DO UPDATE" in str(statement.compile(dialect=dialect))


def test_relay_prunes_expired_frames_and_limits_backlog(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'frames.db'}")
    table = WebSocketFrame.__table__
    table.create(engine)
    relay = ws_relay.WebSocketRelay(engine.url)
    monkeypatch.setattr(ws_relay, "MAX_FRAMES", 3)
    try:
        with engine.begin() as connection:
            connection.execute(
                table.insert(),
                {
                    "created_at": datetime.now(timezone.utc) - timedelta(days=1),
                    "user_id": "owner",
                    "payload": "expired",
                },
            )
        relay.publish("owner", "one")
        with engine.connect() as connection:
            assert list(connection.scalars(select(table.c.payload))) == ["one"]
        for value in ("two", "three", "four"):
            relay.publish("owner", value)
        assert [row.payload for row in relay.read_after(0)] == ["two", "three", "four"]
        assert relay.read_after(relay.latest()) == []
    finally:
        relay.engine.dispose()
        engine.dispose()


def test_cross_worker_frames_are_ordered_scoped_and_subscribe_from_now(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'frames.db'}")
    WebSocketFrame.__table__.create(engine)

    class Socket:
        def __init__(self):
            self.messages = asyncio.Queue()

        async def accept(self):
            pass

        async def send_text(self, text):
            await self.messages.put(json.loads(text))

    async def run():
        first, second = WSConnectionManager(), WSConnectionManager()
        first.start(engine.url)
        second.start(engine.url)
        reader, other = Socket(), Socket()
        try:
            await first.broadcast_to_user("reader", {"v": "before subscribe"})
            await second.connect("reader", reader)
            await second.connect("other", other)
            await first.broadcast_to_user("reader", {"v": 1})
            await second.broadcast_to_user("reader", {"v": 2})
            await first.broadcast_to_user("other", {"v": 3})

            async def receive(socket):
                return await asyncio.wait_for(socket.messages.get(), timeout=3)

            assert [await receive(reader), await receive(reader)] == [{"v": 1}, {"v": 2}]
            assert await receive(other) == {"v": 3}
            assert reader.messages.empty() and other.messages.empty()
        finally:
            await first.stop()
            await second.stop()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_relay_retention_gap_reconnects_a_slow_subscriber(tmp_path, monkeypatch):
    from threading import Event

    engine = create_engine(f"sqlite:///{tmp_path / 'gap.db'}")
    WebSocketFrame.__table__.create(engine)
    monkeypatch.setattr(ws_relay, "MAX_FRAMES", 2)

    class Socket:
        def __init__(self):
            self.closed = asyncio.Queue()
            self.delivered = asyncio.Queue()

        async def accept(self):
            pass

        async def send_text(self, content):
            await self.delivered.put(json.loads(content))

        async def close(self, code):
            await self.closed.put(code)

    async def run():
        manager = WSConnectionManager()
        manager.start(engine.url)
        initial_read, resume = Event(), Event()
        read = manager._relay.read_after

        def paused_read(cursor):
            initial_read.set()
            if not resume.wait(5):
                raise TimeoutError("test did not release the relay reader")
            return read(cursor)

        monkeypatch.setattr(manager._relay, "read_after", paused_read)
        lagging = Socket()
        try:
            await manager.connect("reader", lagging)
            assert await asyncio.to_thread(initial_read.wait, 3)
            for value in range(3):
                await manager.broadcast_to_user("reader", {"v": value})
            resume.set()
            assert await asyncio.wait_for(lagging.closed.get(), 3) == 1012
            manager.disconnect("reader", lagging)
            assert not manager.online_user_ids()
            new = Socket()
            await manager.connect("reader", new)
            await manager.broadcast_to_user("reader", {"v": 3})
            assert await asyncio.wait_for(new.delivered.get(), 3) == {"v": 3}
        finally:
            resume.set()
            await manager.stop()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()


def test_mobile_push_and_web_delete_do_not_block_one_worker_event_loop(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker

    from src.database import Base, get_db
    from src.routers import auth, sync, write

    engine = create_engine(f"sqlite:///{tmp_path / 'writer.db'}", connect_args={"timeout": 1})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    app = FastAPI()
    app.state.ws_manager = WSConnectionManager()
    app.include_router(auth.router, prefix="/auth")
    app.include_router(sync.router, prefix="/sync")
    app.include_router(write.router, prefix="/write")

    def request_session():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = request_session
    armed, first_acquired, second_waiting, release = Event(), Event(), Event(), Event()
    waited_in_event_loop = []

    @event.listens_for(engine, "before_cursor_execute")
    def observe_second(conn, cursor, statement, params, context, many):
        if statement == "BEGIN IMMEDIATE" and first_acquired.is_set() and not release.is_set():
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                waited_in_event_loop.append(True)
            second_waiting.set()

    @event.listens_for(engine, "after_cursor_execute")
    def pause_first(conn, cursor, statement, params, context, many):
        if statement == "BEGIN IMMEDIATE" and armed.is_set() and not first_acquired.is_set():
            first_acquired.set()
            assert release.wait(5)

    user = {"email": "one-worker@example.com", "password": str(uuid4())}
    try:
        with (
            TestClient(app, raise_server_exceptions=False) as client,
            ThreadPoolExecutor(2) as peers,
        ):

            def login(path, kind):
                response = client.post(
                    path, json={**user, "device_name": kind, "client_type": kind, "platform": kind}
                )
                response.raise_for_status()
                return response.json()

            phone, browser = login("/auth/register", "app"), login("/auth/login", "web")
            phone_headers = {"Authorization": "Bearer " + phone["access_token"]}
            web_headers = {"Authorization": "Bearer " + browser["access_token"]}

            def rename(ledger, title):
                now = datetime.now(timezone.utc).isoformat()
                return client.post(
                    "/sync/push",
                    headers=phone_headers,
                    json={
                        "device_id": phone["device_id"],
                        "changes": [
                            {
                                "entity_type": "ledger",
                                "action": "upsert",
                                "entity_sync_id": ledger,
                                "ledger_id": ledger,
                                "updated_at": now,
                                "payload": {"syncId": ledger, "ledgerName": title},
                            }
                        ],
                    },
                )

            assert (
                rename("phone", "first").status_code
                == rename("temporary", "second").status_code
                == 200
            )
            armed.set()
            added = peers.submit(rename, "phone", "changed")
            try:
                assert first_acquired.wait(5)
                removed = peers.submit(
                    client.delete, "/write/ledgers/temporary", headers=web_headers
                )
                assert second_waiting.wait(5)
            finally:
                release.set()
            assert [added.result().status_code, removed.result().status_code] == [200, 200]
            assert not waited_in_event_loop
    finally:
        engine.dispose()


def test_delayed_shared_resource_frames_use_current_projection_and_membership(tmp_path):
    from sqlalchemy.orm import Session

    from src.database import Base
    from src.models import Ledger, LedgerMember, User

    engine = create_engine(f"sqlite:///{tmp_path / 'shared.db'}")
    Base.metadata.create_all(engine)
    relay = ws_relay.WebSocketRelay(engine.url)
    with Session(engine) as db:
        db.add_all(
            [
                User(id=name, email=name + "@example.com", password_hash="unused")
                for name in ("owner", "editor")
            ]
        )
        db.flush()
        ledger = Ledger(user_id="owner", external_id="shared")
        db.add(ledger)
        db.flush()
        ledger_id = ledger.id
        db.add(LedgerMember(ledger_id=ledger_id, user_id="editor", role="editor"))
        db.add(UserTagProjection(user_id="owner", sync_id="tag", name="latest", source_change_id=4))
        db.commit()
    delayed = json.dumps(
        {
            "type": "shared_resource_change",
            "resourceType": "tag",
            "ledgerId": "shared",
            "action": "upsert",
            "payload": {"syncId": "tag", "name": "earlier"},
        }
    )
    try:
        relay.publish("editor", delayed, "owner")
        frame = json.loads(relay.read_after(0)[0].payload)
        assert (frame["action"], frame["payload"]["name"]) == ("upsert", "latest")
        with Session(engine) as db:
            db.delete(db.get(UserTagProjection, ("owner", "tag")))
            db.commit()
        cursor = relay.latest()
        relay.publish("editor", delayed, "owner")
        frame = json.loads(relay.read_after(cursor)[0].payload)
        assert (frame["action"], frame["payload"]) == ("delete", {"syncId": "tag"})
        with Session(engine) as db:
            member = db.scalar(select(LedgerMember).where(LedgerMember.ledger_id == ledger_id))
            db.delete(member)
            db.commit()
        cursor = relay.latest()
        relay.publish("editor", delayed, "owner")
        assert relay.read_after(cursor) == []
    finally:
        relay.engine.dispose()
        engine.dispose()
