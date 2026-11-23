from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(settings.database_url, **engine_kwargs)


# 生产 SQLite 必配:WAL + busy_timeout。详见 .docs/sqlite-concurrency-fix.md
#
# 默认 journal_mode=DELETE + busy_timeout=0 在多 worker FastAPI 下会随机
# 报 "database is locked" — 任何 writer 进入 PENDING 状态(等其它 reader
# 退出),新 reader 都拿不到 SHARED 锁,立即报错。WAL 模式下 reader/writer
# 走 MVCC 不互相阻塞。
#
# 这是生产部署基础设施配置,不是业务 bug。
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        # WAL: reader 不阻塞 writer,writer 不阻塞 reader(MVCC)。
        # 一次设置后持久化到 db 文件,后续 connection 自动是 WAL。
        cur.execute("PRAGMA journal_mode=WAL")
        # 写写互斥时等 5 秒再报错(默认 0 立即报)。
        cur.execute("PRAGMA busy_timeout=5000")
        # WAL 下推荐 NORMAL,数据不丢且写性能更好(DELETE 默认 FULL,fsync 太频繁)。
        cur.execute("PRAGMA synchronous=NORMAL")
        # SQLite 默认 FK 检查 OFF,显式开启让 ondelete=CASCADE 真生效。
        cur.execute("PRAGMA foreign_keys=ON")
        # 64MB page cache,大库读取性能大幅提升。
        cur.execute("PRAGMA cache_size=-64000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
DEVICE_ACTIVITY_AFTER_CLOSE = "device_activity_after_close"


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        # 刷新设备活跃时间使用独立事务，先归还业务请求占用的连接。
        update_device_activity = db.info.pop(DEVICE_ACTIVITY_AFTER_CLOSE, None)
        if update_device_activity is not None:
            update_device_activity()
