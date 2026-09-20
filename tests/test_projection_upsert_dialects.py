"""projection._upsert 的跨方言回归测试。

生产事故:PG 绑定下 _upsert 曾用 sqlite insert 构造 ON CONFLICT 语句,新版
SQLAlchemy 的 PG 编译路径引用了 sqlite OnConflictDoUpdate 没有的
constraint_target 属性,执行期 AttributeError,导致 /sync/push 500、App 同步失败。

这里不连真库:用假 Session 把 _upsert 生成的语句捕获下来,再分别按
PG / SQLite 方言编译一遍(纯编译即可复现当时的崩溃)。
"""
from __future__ import annotations

from sqlalchemy.dialects import postgresql, sqlite

from src.models import UserAccountProjection
from src.projection import _upsert


class _FakeBind:
    def __init__(self, dialect):
        self.dialect = dialect


class _CapturingSession:
    """只实现 _upsert 用到的 get_bind/execute,把语句抓下来不真执行。"""

    def __init__(self, dialect):
        self._dialect = dialect
        self.captured = None

    def get_bind(self):
        return _FakeBind(self._dialect)

    def execute(self, stmt):
        self.captured = stmt


def _capture(dialect) -> str:
    db = _CapturingSession(dialect)
    _upsert(
        db,
        UserAccountProjection,
        ("user_id", "sync_id"),
        {"user_id": "u1", "sync_id": "s1", "name": "cash"},
    )
    assert db.captured is not None
    return str(db.captured.compile(dialect=dialect))


def test_upsert_compiles_under_postgresql():
    sql = _capture(postgresql.dialect())
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql


def test_upsert_compiles_under_sqlite():
    sql = _capture(sqlite.dialect())
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
