"""db_snapshot.vacuum_into 行为单测。

核心契约:exclude_tables 里的表 **schema 保留、数据清空**。之前版本 DROP TABLE
导致 restore 后 server 启动撞 "no such table",已修复改成 DELETE FROM。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models import Ledger, MCPCallLog, ReadTxProjection, User
from src.services.backup.db_snapshot import (
    DEFAULT_EXCLUDED_TABLES,
    vacuum_into,
)


@pytest.fixture
def src_db(tmp_path):
    """造一个完整 schema + 少量数据的源 DB,返回 (path, session_factory)。"""
    db_path = tmp_path / "src.db"
    # SQLite VACUUM INTO 要求源是文件 DB(不是 :memory:)
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with Session() as db:
        # 用户数据(必须保留)
        db.add(User(id="u1", email="u@x.com", password_hash="h"))
        db.add(
            Ledger(
                id="L1", user_id="u1", external_id="ext", name="L", currency="CNY",
            )
        )
        db.add(
            ReadTxProjection(
                ledger_id="L1", sync_id="tx-1", user_id="u1",
                tx_type="expense", amount=1.0,
                happened_at=datetime.now(timezone.utc),
                tx_index=0, source_change_id=1,
            )
        )
        # 运维表数据(默认 exclude,vacuum_into 应该清掉数据但保留 schema)
        db.add(
            MCPCallLog(
                user_id="u1", tool_name="ping", status="ok",
                called_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    yield db_path, Session
    engine.dispose()


def _read_target(target_path: Path):
    """打开 vacuum_into 输出的目标 DB,返回 (engine, session_factory, inspector)。"""
    engine = create_engine(f"sqlite:///{target_path}")
    Session = sessionmaker(bind=engine)
    inspector = inspect(engine)
    return engine, Session, inspector


def test_vacuum_into_preserves_excluded_table_schema(src_db, tmp_path):
    """exclude_tables 里的表 schema 必须保留 — restore 后 server 启动不报
    'no such table'。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        vacuum_into(db, target)

    engine, _, inspector = _read_target(target)
    try:
        existing = set(inspector.get_table_names())
        for tbl in DEFAULT_EXCLUDED_TABLES:
            assert tbl in existing, (
                f"excluded table {tbl!r} schema 不见了 — restore 后会撞 'no such table'"
            )
    finally:
        engine.dispose()


def test_vacuum_into_clears_excluded_table_data(src_db, tmp_path):
    """exclude_tables 里的数据应当被清空(不属于用户数据,留着只让 tar 变大)。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        # 确认 src 里有运维数据
        n = db.scalar(select(text("count(*)")).select_from(text("mcp_call_logs")))
        assert n == 1, "src 里应该有 1 条 mcp_call_log 测试数据"
        vacuum_into(db, target)

    engine, target_session, _ = _read_target(target)
    try:
        with target_session() as tdb:
            n = tdb.scalar(
                select(text("count(*)")).select_from(text("mcp_call_logs"))
            )
            assert n == 0, "excluded 表的数据应该被清空"
    finally:
        engine.dispose()


def test_vacuum_into_preserves_user_data(src_db, tmp_path):
    """非 excluded 表(用户数据)必须完整保留。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        vacuum_into(db, target)

    engine, target_session, _ = _read_target(target)
    try:
        with target_session() as tdb:
            assert tdb.scalar(
                select(text("count(*)")).select_from(text("users"))
            ) == 1
            assert tdb.scalar(
                select(text("count(*)")).select_from(text("ledgers"))
            ) == 1
            assert tdb.scalar(
                select(text("count(*)")).select_from(text("read_tx_projection"))
            ) == 1
    finally:
        engine.dispose()


def test_vacuum_into_handles_missing_excluded_table(src_db, tmp_path, monkeypatch):
    """exclude_tables 列出但源 DB 里没有的表 — 应当 silent skip,不抛错。

    场景:老 DB 还没跑过新 migration,某些表(比如 mcp_call_logs)还没创建。
    """
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        # 故意删一个表,模拟"老 DB"
        db.execute(text("DROP TABLE mcp_call_logs"))
        db.commit()
        # 不应抛错(silent skip 缺失表)
        vacuum_into(db, target)

    assert target.exists()


def test_vacuum_into_with_none_exclude_keeps_everything(src_db, tmp_path):
    """exclude_tables=None → 一字不删,完整 copy。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        vacuum_into(db, target, exclude_tables=None)

    engine, target_session, _ = _read_target(target)
    try:
        with target_session() as tdb:
            n = tdb.scalar(
                select(text("count(*)")).select_from(text("mcp_call_logs"))
            )
            assert n == 1, "exclude_tables=None 时不该动数据"
    finally:
        engine.dispose()
