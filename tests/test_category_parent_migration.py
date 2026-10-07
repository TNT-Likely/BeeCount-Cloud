"""Conservative, scoped and repeatable recovery of historical #101 rows."""
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, text


def test_parent_backfill_repairs_only_unambiguous_owned_links():
    path = Path(__file__).parents[1] / 'alembic/versions/0020_repair_category_parent_links.py'
    spec = importlib.util.spec_from_file_location('parent_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE user_category_projection (user_id TEXT, sync_id TEXT, name TEXT, kind TEXT, '
                        'level INTEGER, parent_name TEXT, parent_sync_id TEXT)'))
        rows = [
            ('u1', 'p', '伙食', 'expense', 1, None, None),
            ('u1', 'stable', '早餐', 'expense', 2, '餐饮', 'p'),
            ('u1', 'legacy', '午餐', 'expense', 2, '伙食', None),
            ('u1', 'deleted', '晚餐', 'expense', 2, '伙食', 'missing'),
            ('u1', 'orphan', '夜宵', 'expense', 2, '餐饮', None),
            ('u2', 'other', '其他', 'expense', 2, '餐饮', 'p'),
            ('u1', 'income', '收入', 'income', 2, '伙食', None),
            ('u1', 'a', '同名', 'expense', 1, None, None),
            ('u1', 'b', '同名', 'expense', 1, None, None),
            ('u1', 'ambiguous', '子类', 'expense', 2, '同名', None),
        ]
        for row in rows:
            db.execute(text('INSERT INTO user_category_projection VALUES (:u,:id,:name,:kind,:level,:pn,:pid)'),
                       dict(zip(('u','id','name','kind','level','pn','pid'), row)))
        def read():
            return {r.sync_id: (r.parent_name, r.parent_sync_id) for r in db.execute(text('SELECT * FROM user_category_projection'))}
        before = read()
        for statement in module.BACKFILL_STATEMENTS:
            db.execute(text(statement))
        after = read()
        assert after['stable'] == ('伙食', 'p')
        assert after['legacy'] == ('伙食', 'p')
        for sid in ['deleted', 'orphan', 'other', 'income', 'ambiguous']:
            assert after[sid] == before[sid]
        for statement in module.BACKFILL_STATEMENTS:
            db.execute(text(statement))
        assert read() == after
