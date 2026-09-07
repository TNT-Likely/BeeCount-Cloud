"""SQLite WAL + busy_timeout 生产配置回归测试。

锁定 src/database.py 里的 PRAGMA listener 不被无意改回。改坏会导致生产
高并发下 "database is locked" 大面积爆。详见 .docs/sqlite-concurrency-fix.md。
"""
from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from src.database import SessionLocal, _engine_kwargs, engine


def _is_file_sqlite() -> bool:
    """只在 file-backed sqlite 跑 — in-memory db 的 journal_mode 永远是 'memory',
    不可能切 WAL;CI 用 `sqlite:///:memory:` 跑测试,这套断言跑不通。"""
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return False
    if ":memory:" in url:
        return False
    # sqlite:// 没 path 也是 in-memory
    if url.rstrip("/") in {"sqlite:", "sqlite://"}:
        return False
    return True


def test_engine_uses_wal_mode():
    """每个新 file-backed connection 必须自动配 WAL + busy_timeout=5000。"""
    if not _is_file_sqlite():
        pytest.skip("only file-backed sqlite needs WAL")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        # synchronous=NORMAL = 1 (FULL=2, OFF=0)
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1
        # foreign_keys=ON 让 ondelete=CASCADE 真生效
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_file_sqlite_disables_queue_pool():
    """文件型 SQLite 不应受 QueuePool 默认 15 连接 / 30 秒超时限制。"""
    kwargs = _engine_kwargs("sqlite:////tmp/beecount-pool-regression.db")
    assert kwargs["poolclass"] is NullPool
    assert kwargs["connect_args"] == {"check_same_thread": False}


def test_memory_sqlite_keeps_default_pool():
    """内存 SQLite 的数据库跟连接绑定,不能使用每次断开的 NullPool。"""
    kwargs = _engine_kwargs("sqlite:///:memory:")
    assert "poolclass" not in kwargs


def test_named_memory_sqlite_keeps_default_pool():
    kwargs = _engine_kwargs(
        "sqlite:///file:beecount_test?mode=memory&cache=shared&uri=true"
    )
    assert "poolclass" not in kwargs


def test_postgres_pooling_is_unchanged():
    kwargs = _engine_kwargs("postgresql+psycopg://user:pass@db/beecount")
    assert kwargs == {"pool_pre_ping": True}


def test_initial_read_burst_can_open_more_than_15_connections(tmp_path):
    """回归 #73:首屏 16 个并发读不能卡在 QueuePool 默认容量上。"""
    database_url = f"sqlite:///{tmp_path / 'initial-read-burst.db'}"
    test_engine = create_engine(database_url, **_engine_kwargs(database_url))
    assert isinstance(test_engine.pool, NullPool)

    concurrency = 16
    start = threading.Barrier(concurrency + 1)
    all_connected = threading.Barrier(concurrency)
    errors: list[Exception] = []

    def reader():
        try:
            start.wait(timeout=10)
            with test_engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1").scalar()
                # 所有连接同时保持 checkout；QueuePool 默认最多只能让 15
                # 个线程到这里，第 16 个会等待，因此 barrier 会超时。
                all_connected.wait(timeout=10)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(concurrency)]
    for thread in threads:
        thread.start()
    start.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=12)

    test_engine.dispose()
    assert not [thread for thread in threads if thread.is_alive()]
    assert not errors, f"16 个并发 SQLite 读连接都应立即可用: {errors}"


def test_concurrent_reader_writer_no_lock():
    """WAL 模式下,长 reader 不应阻塞另一线程的 writer。

    模拟数据清理 scan(长 SELECT)期间,普通 sync/pull(commit)能正常完成。
    DELETE 模式下这种场景必然会有一方报 locked,WAL 模式下双方都成功。
    """
    if not _is_file_sqlite():
        pytest.skip("only file-backed sqlite has this lock semantic")

    errors: list[Exception] = []
    writer_ok = threading.Event()

    def long_reader():
        try:
            with SessionLocal() as s:
                # 模拟 scanner 的长 SELECT —— 拿 sqlite_master 一定有结果,不依赖业务表
                s.execute(_text("SELECT name FROM sqlite_master"))
                time.sleep(1.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def writer():
        try:
            # 等 reader 先起来
            time.sleep(0.2)
            with SessionLocal() as s:
                s.execute(_text("CREATE TABLE IF NOT EXISTS _wal_test (id INTEGER PRIMARY KEY)"))
                s.execute(_text("INSERT INTO _wal_test DEFAULT VALUES"))
                s.commit()
                writer_ok.set()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=long_reader)
    t2 = threading.Thread(target=writer)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors, f"WAL 模式下读写不应互相阻塞: {errors}"
    assert writer_ok.is_set(), "writer 应在 reader 还在跑时就完成"

    # 清理
    with SessionLocal() as s:
        s.execute(_text("DROP TABLE IF EXISTS _wal_test"))
        s.commit()


def _text(sql: str):
    """局部 helper,避免 import sqlalchemy 顶层。"""
    from sqlalchemy import text as _t

    return _t(sql)
