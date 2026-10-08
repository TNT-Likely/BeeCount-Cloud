"""回填 read_tx_projection.category_sync_id。

背景:2026-10 前的 Web 导入只落 category_name,不解析 categoryId,投影
category_sync_id 全 NULL → 分类页/按分类统计永远 0 笔。本脚本按
(category_name, kind) 匹配 user_category_projection(同名优先二级 leaf)
回填 sync_id。幂等:只处理 category_sync_id IS NULL 的行,可重复执行。

用法:PYTHONPATH=. python scripts/backfill_category_sync_id.py [--dry-run]
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from src.database import SessionLocal
from src.models import ReadTxProjection, UserCategoryProjection


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill read_tx_projection.category_sync_id by (name, kind)."
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计,不写入")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.category_sync_id.is_(None),
                ReadTxProjection.category_name.is_not(None),
                ReadTxProjection.category_kind.is_not(None),
            )
        ).all()

        # (user_id, name_lower, kind) → sync_id。同名同 kind 优先二级(leaf):
        # 导入交易引用的是 leaf 分类,跟 web 导入端点的解析规则一致。
        by_key: dict[tuple[str, str, str], str] = {}
        for cat in db.scalars(select(UserCategoryProjection)).all():
            name = (cat.name or "").strip().lower()
            if not name or not cat.sync_id:
                continue
            key = (cat.user_id, name, cat.kind or "")
            if (cat.level or 1) == 2 or key not in by_key:
                by_key[key] = cat.sync_id

        fixed = 0
        missing: dict[str, int] = {}
        for tx in rows:
            name = (tx.category_name or "").strip().lower()
            sid = by_key.get((tx.user_id, name, tx.category_kind or ""))
            if sid is None:
                missing[tx.category_name] = missing.get(tx.category_name, 0) + 1
                continue
            tx.category_sync_id = sid
            fixed += 1

        if args.dry_run:
            db.rollback()
        else:
            db.commit()

        print(f"scanned: {len(rows)} rows to backfill")
        print(f"fixed: {fixed}")
        if missing:
            print("unmatched (left as-is):")
            for name, count in sorted(missing.items(), key=lambda kv: -kv[1]):
                print(f"  {name}: {count}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
