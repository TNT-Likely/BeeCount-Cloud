from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings

settings = get_settings()


def _is_file_sqlite(database_url: str) -> bool:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return False
    database = url.database or ""
    # 除了常见的 :memory:,SQLite 还支持 URI named-memory database:
    # sqlite:///file:name?mode=memory&cache=shared&uri=true。
    return bool(database and database != ":memory:" and url.query.get("mode") != "memory")


def _engine_kwargs(database_url: str) -> dict:
    kwargs: dict = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_file_sqlite(database_url):
            # SQLAlchemy 2.x 对文件型 SQLite 默认使用 QueuePool(5 + 10,
            # pool_timeout=30s)。Web 首屏会同时拉 profile / ledgers / analytics /
            # budgets 等,单个 tab 就可能超过 15 个请求,导致后续请求精确等待
            # 30s 后报 QueuePool TimeoutError。
            #
            # SQLite 连接创建成本很低,真正的并发控制由 WAL + busy_timeout
            # 负责。NullPool 让每个 request session 在结束时直接关闭连接,不会
            # 再人为套一层固定 15 个连接的进程内瓶颈。内存 SQLite 不能使用
            # NullPool(每条连接会得到不同数据库),所以只应用于文件型 SQLite。
            kwargs["poolclass"] = NullPool
    return kwargs


engine_kwargs = _engine_kwargs(settings.database_url)
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


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
