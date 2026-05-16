"""drop legacy ledger_snapshot SyncChange rows

Revision ID: 0011_drop_legacy_ledger_snapshot
Revises: 0010_user_global_projections
Create Date: 2026-05-16

方案 B(`sync_applier.apply_change_to_projection`)之后 sync push 不再写
`entity_type='ledger_snapshot'` 的 SyncChange 行 —— projection 是权威源,
不需要再嵌一份完整 snapshot JSON。但 1.x 之前积累的 ledger_snapshot 行
还在 sync_changes 表里,每行 1-4 MB(完整 snapshot 序列化),线上备份
26k 行 sync_changes 里这 6 行就占 10.6 MB / 23%。

这些行**死数据**:
- sync_applier 收到 ledger_snapshot 类型时不再 dispatch(`sync_applier.py
  apply_change_to_projection` 没有 ledger_snapshot 分支)
- mobile 端 `/sync/pull` 拉到也会被前端 ignore(只处理 INDIVIDUAL_ENTITY_TYPES
  + ledger metadata)
- `projection.rebuild_from_snapshot` 当前已经从 user_*_projection /
  read_*_projection 直接重建,不读 ledger_snapshot

唯一 ledger_snapshot:delete 行例外 —— `LocalRepository.deleteLedger` 仍
登记 ledger_snapshot:delete 一条让 server 知道账本被删。这里只清 upsert,
保留 delete 行。

空间释放:DELETE 后行没了但 SQLite 页空间不会自动收回。下次 backup runner
跑 VACUUM INTO(`vacuum_into` in db_snapshot.py)会把空页 squeeze 掉,备份
文件立即变小。alembic transaction 不能跑 VACUUM,这里只 DELETE。
"""
import sqlalchemy as sa
from alembic import op


revision = "0011_drop_legacy_ledger_snapshot"
down_revision = "0010_user_global_projections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 只清 upsert 类型 —— delete 类型保留(deleteLedger 路径仍在用,清了
    # server 端可能把已删账本认回来)。
    result = op.get_bind().execute(
        sa.text(
            "DELETE FROM sync_changes "
            "WHERE entity_type = 'ledger_snapshot' AND action = 'upsert'"
        )
    )
    # alembic logger 在 upgrade 命令 stdout 显示
    # rowcount 对 SQLite / Postgres 都返回 int
    print(f"[0011] deleted {result.rowcount} legacy ledger_snapshot upsert rows")


def downgrade() -> None:
    # 删了就回不去了 —— sync_changes 是 append-only log,无 backup 路径回填
    # 完整 snapshot payload。downgrade 是 no-op,仅为满足 alembic 规范。
    pass
