"""救急脚本:从每个 ledger 的最新 snapshot 全量重建 read_*_projection。

什么时候跑这个脚本:
- projection 数据肉眼不对 / 跟 snapshot 不一致(writes 路径某处掉了 hook)
- 大规模数据修复后(admin 直接改了 sync_changes payload,绕过了 projection writers)
- migration 0019 的回填脚本漏掉某些字段,想补一次

语义:对每个 ledger 调 `projection.rebuild_from_snapshot`,先 TRUNCATE 该
ledger 的 5 张 projection,再按 snapshot 权威源批量 upsert 回去。幂等,重复跑
结果一致。

用法:
    cd /path/to/BeeCount-Platform
    python -m scripts.rebuild_all_projections
    # 或限定单个用户/账本:
    python -m scripts.rebuild_all_projections --user-email a@example.com
    python -m scripts.rebuild_all_projections --ledger-external-id my_ledger
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from src import projection
from src.database import SessionLocal
from src.models import Ledger, SyncChange, User


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rebuild all projection tables from latest snapshots")
    p.add_argument("--user-email", help="Limit to a single user's ledgers")
    p.add_argument("--ledger-external-id", help="Limit to a single ledger (external id)")
    p.add_argument("--dry-run", action="store_true", help="Print what would be rebuilt, do not write")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        query = select(Ledger.id, Ledger.user_id, Ledger.external_id)
        if args.user_email:
            user = db.scalar(select(User).where(User.email == args.user_email.strip().lower()))
            if user is None:
                print(f"user not found: {args.user_email}", file=sys.stderr)
                return 1
            query = query.where(Ledger.user_id == user.id)
        if args.ledger_external_id:
            query = query.where(Ledger.external_id == args.ledger_external_id)

        ledgers = db.execute(query).all()
        if not ledgers:
            print("no ledgers matched")
            return 0

        rebuilt = 0
        skipped = 0
        for ledger_id, user_id, external_id in ledgers:
            latest = db.scalar(
                select(SyncChange)
                .where(
                    SyncChange.ledger_id == ledger_id,
                    SyncChange.entity_type == "ledger_snapshot",
                )
                .order_by(SyncChange.change_id.desc())
                .limit(1)
            )
            if latest is None:
                print(f"skip {external_id}: no snapshot")
                skipped += 1
                continue
            payload = latest.payload_json
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    print(f"skip {external_id}: payload not JSON")
                    skipped += 1
                    continue
            if not isinstance(payload, dict):
                print(f"skip {external_id}: payload not dict")
                skipped += 1
                continue
            content = payload.get("content")
            if not isinstance(content, str) or not content.strip():
                print(f"skip {external_id}: empty content")
                skipped += 1
                continue
            try:
                snapshot = json.loads(content)
            except json.JSONDecodeError:
                print(f"skip {external_id}: snapshot not JSON")
                skipped += 1
                continue
            if not isinstance(snapshot, dict):
                print(f"skip {external_id}: snapshot not dict")
                skipped += 1
                continue

            tx_cnt = len(snapshot.get("items") or [])
            acc_cnt = len(snapshot.get("accounts") or [])
            cat_cnt = len(snapshot.get("categories") or [])
            tag_cnt = len(snapshot.get("tags") or [])
            bud_cnt = len(snapshot.get("budgets") or [])
            print(
                f"{'[DRY]' if args.dry_run else '[OK ]'} {external_id}: "
                f"tx={tx_cnt} acc={acc_cnt} cat={cat_cnt} tag={tag_cnt} bud={bud_cnt} "
                f"source_change_id={latest.change_id}"
            )
            if args.dry_run:
                continue
            projection.rebuild_from_snapshot(
                db,
                ledger_id=ledger_id,
                user_id=user_id,
                snapshot=snapshot,
                source_change_id=int(latest.change_id),
            )
            rebuilt += 1

        if not args.dry_run:
            db.commit()
        print(f"\nDone. rebuilt={rebuilt} skipped={skipped} total={len(ledgers)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
