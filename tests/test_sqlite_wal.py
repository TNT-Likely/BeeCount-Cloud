"""SQLite WAL + busy_timeout 生产配置回归测试。

锁定 src/database.py 里的 PRAGMA listener 不被无意改回。改坏会导致生产
高并发下 "database is locked" 大面积爆。详见 .docs/sqlite-concurrency-fix.md。
"""
from __future__ import annotations

import threading
import time

import pytest

from src.database import SessionLocal, engine


def _is_file_sqlite() -> bool:
    """只在 file-backed sqlite 跑 — in-memory db 的 journal_mode 永远是 'memory',
    不可能切 WAL;CI 用 `sqlite:///:memory:` 跑测试,这套断言跑不通。"""
    url = engine.url
    if url.get_backend_name() != "sqlite":
        return False
    database = url.database
    if database in {None, "", ":memory:"}:
        return False
    # sqlite:// 没 path 也是 in-memory
    if database.startswith("file:") and url.query.get("mode") == "memory":
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
