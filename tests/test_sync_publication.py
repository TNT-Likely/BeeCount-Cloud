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
