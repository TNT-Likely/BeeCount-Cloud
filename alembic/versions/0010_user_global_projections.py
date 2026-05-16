"""user-global resources: split out of ledger-scoped sync protocol

Revision ID: 0010_user_global_projections
Revises: 0009_mcp_call_log_pat_name
Create Date: 2026-05-16

参考 .docs/user-global-refactor/plan.md。把 category/account/tag 从
ledger-scoped projection 拆出来做成真·per-user 表:

- 新建 user_category_projection / user_account_projection / user_tag_projection
  PK=(user_id, sync_id),不挂任何 ledger
- sync_changes:加 scope 列(user|ledger),ledger_id 改 nullable
- 历史 sync_changes:entity_type ∈ (category,account,tag) 回填 scope='user'
- 数据迁移:read_*_projection → user_*_projection,(user_id, sync_id) 去重取
  MAX(source_change_id)
- drop 老 read_category_projection / read_account_projection / read_tag_projection

跨方言(PostgreSQL prod + SQLite test):用 Python 侧循环 INSERT,不依赖
PG `ON CONFLICT` / SQLite `INSERT OR IGNORE`。
"""
import sqlalchemy as sa
from alembic import op


revision = "0010_user_global_projections"
down_revision = "0009_mcp_call_log_pat_name"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # 1. sync_changes 加 scope 列 + ledger_id 改 nullable + 新索引
    _alter_sync_changes_add_scope()

    # 2. 建 3 张 user_*_projection 表 + 索引
    _create_user_projection_tables()

    # 3. 历史 sync_changes 回填 scope='user'(server_default 是 'ledger',
    #    需要把历史 user-global 类型显式改成 'user')
    op.execute(
        "UPDATE sync_changes SET scope = 'user' "
        "WHERE entity_type IN ('category', 'account', 'tag')"
    )

    # 4. 数据迁移 read_*_projection → user_*_projection
    conn = op.get_bind()
    _migrate_categories(conn)
    _migrate_accounts(conn)
    _migrate_tags(conn)

    # 5. drop 老 projection 表
    op.drop_index("ix_read_cat_ledger_kind", table_name="read_category_projection")
    op.drop_index(
        op.f("ix_read_category_projection_user_id"),
        table_name="read_category_projection",
    )
    op.drop_table("read_category_projection")

    op.drop_index(
        op.f("ix_read_account_projection_user_id"),
        table_name="read_account_projection",
    )
    op.drop_table("read_account_projection")

    op.drop_index(
        op.f("ix_read_tag_projection_user_id"),
        table_name="read_tag_projection",
    )
    op.drop_table("read_tag_projection")


def _alter_sync_changes_add_scope() -> None:
    with op.batch_alter_table("sync_changes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "scope",
                sa.String(8),
                nullable=False,
                server_default="ledger",
            )
        )
        batch_op.alter_column(
            "ledger_id",
            existing_type=sa.String(36),
            nullable=True,
        )
    op.create_index(
        "idx_sync_changes_user_scope_cursor",
        "sync_changes",
        ["user_id", "scope", "change_id"],
    )


def _create_user_projection_tables() -> None:
    op.create_table(
        "user_category_projection",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(255), nullable=True),
        sa.Column("icon_type", sa.String(32), nullable=True),
        sa.Column("custom_icon_path", sa.String(1024), nullable=True),
        sa.Column("icon_cloud_file_id", sa.String(36), nullable=True),
        sa.Column("icon_cloud_sha256", sa.String(64), nullable=True),
        sa.Column("parent_name", sa.Text(), nullable=True),
        sa.Column(
            "source_change_id", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "sync_id"),
    )
    op.create_index(
        "ix_user_cat_kind",
        "user_category_projection",
        ["user_id", "kind"],
    )

    op.create_table(
        "user_account_projection",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("account_type", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("credit_limit", sa.Float(), nullable=True),
        sa.Column("billing_day", sa.Integer(), nullable=True),
        sa.Column("payment_due_day", sa.Integer(), nullable=True),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("card_last_four", sa.String(8), nullable=True),
        sa.Column(
            "source_change_id", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "sync_id"),
    )

    op.create_table(
        "user_tag_projection",
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column(
            "source_change_id", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "sync_id"),
    )


def _migrate_categories(conn) -> None:
    """read_category_projection → user_category_projection。
    每 (user_id, sync_id) 取 MAX(source_change_id) 当胜出版本(BigInt 全局
    自增,严格单调,语义=最后一次 push 的版本)。"""
    rows = conn.execute(
        sa.text(
            "SELECT p.user_id, p.sync_id, p.name, p.kind, p.level, p.sort_order, "
            "p.icon, p.icon_type, p.custom_icon_path, p.icon_cloud_file_id, "
            "p.icon_cloud_sha256, p.parent_name, p.source_change_id "
            "FROM read_category_projection p "
            "INNER JOIN ("
            "  SELECT user_id, sync_id, MAX(source_change_id) AS max_src "
            "  FROM read_category_projection "
            "  GROUP BY user_id, sync_id"
            ") latest "
            "ON latest.user_id = p.user_id AND latest.sync_id = p.sync_id "
            "AND latest.max_src = p.source_change_id"
        )
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row[0], row[1])
        if key in seen:
            # 防御性:理论 source_change_id 全局唯一,不会同 max 撞多个 ledger 行
            continue
        seen.add(key)
        conn.execute(
            sa.text(
                "INSERT INTO user_category_projection ("
                "user_id, sync_id, name, kind, level, sort_order, icon, icon_type, "
                "custom_icon_path, icon_cloud_file_id, icon_cloud_sha256, parent_name, "
                "source_change_id) VALUES ("
                ":user_id, :sync_id, :name, :kind, :level, :sort_order, :icon, "
                ":icon_type, :custom_icon_path, :icon_cloud_file_id, "
                ":icon_cloud_sha256, :parent_name, :source_change_id)"
            ),
            {
                "user_id": row[0],
                "sync_id": row[1],
                "name": row[2],
                "kind": row[3],
                "level": row[4],
                "sort_order": row[5],
                "icon": row[6],
                "icon_type": row[7],
                "custom_icon_path": row[8],
                "icon_cloud_file_id": row[9],
                "icon_cloud_sha256": row[10],
                "parent_name": row[11],
                "source_change_id": row[12],
            },
        )


def _migrate_accounts(conn) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT p.user_id, p.sync_id, p.name, p.account_type, p.currency, "
            "p.initial_balance, p.note, p.credit_limit, p.billing_day, "
            "p.payment_due_day, p.bank_name, p.card_last_four, p.source_change_id "
            "FROM read_account_projection p "
            "INNER JOIN ("
            "  SELECT user_id, sync_id, MAX(source_change_id) AS max_src "
            "  FROM read_account_projection "
            "  GROUP BY user_id, sync_id"
            ") latest "
            "ON latest.user_id = p.user_id AND latest.sync_id = p.sync_id "
            "AND latest.max_src = p.source_change_id"
        )
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row[0], row[1])
        if key in seen:
            continue
        seen.add(key)
        conn.execute(
            sa.text(
                "INSERT INTO user_account_projection ("
                "user_id, sync_id, name, account_type, currency, initial_balance, "
                "note, credit_limit, billing_day, payment_due_day, bank_name, "
                "card_last_four, source_change_id) VALUES ("
                ":user_id, :sync_id, :name, :account_type, :currency, "
                ":initial_balance, :note, :credit_limit, :billing_day, "
                ":payment_due_day, :bank_name, :card_last_four, :source_change_id)"
            ),
            {
                "user_id": row[0],
                "sync_id": row[1],
                "name": row[2],
                "account_type": row[3],
                "currency": row[4],
                "initial_balance": row[5],
                "note": row[6],
                "credit_limit": row[7],
                "billing_day": row[8],
                "payment_due_day": row[9],
                "bank_name": row[10],
                "card_last_four": row[11],
                "source_change_id": row[12],
            },
        )


def _migrate_tags(conn) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT p.user_id, p.sync_id, p.name, p.color, p.source_change_id "
            "FROM read_tag_projection p "
            "INNER JOIN ("
            "  SELECT user_id, sync_id, MAX(source_change_id) AS max_src "
            "  FROM read_tag_projection "
            "  GROUP BY user_id, sync_id"
            ") latest "
            "ON latest.user_id = p.user_id AND latest.sync_id = p.sync_id "
            "AND latest.max_src = p.source_change_id"
        )
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row[0], row[1])
        if key in seen:
            continue
        seen.add(key)
        conn.execute(
            sa.text(
                "INSERT INTO user_tag_projection (user_id, sync_id, name, color, "
                "source_change_id) VALUES (:user_id, :sync_id, :name, :color, "
                ":source_change_id)"
            ),
            {
                "user_id": row[0],
                "sync_id": row[1],
                "name": row[2],
                "color": row[3],
                "source_change_id": row[4],
            },
        )


# ---------------------------------------------------------------------------
# Downgrade —— 尽力而为的兜底,不依赖它做生产回滚(出问题用 backup 还原整库)
# ---------------------------------------------------------------------------


def downgrade() -> None:
    # 1. 重建 3 张老 projection 表
    _recreate_legacy_projection_tables()

    # 2. user_*_projection 数据回写老表;选用户最早 ledger 作 ledger_id;
    #    无 ledger 的用户跳过
    conn = op.get_bind()
    _downgrade_migrate_categories(conn)
    _downgrade_migrate_accounts(conn)
    _downgrade_migrate_tags(conn)

    # 3. drop 新表
    op.drop_table("user_tag_projection")
    op.drop_table("user_account_projection")
    op.drop_index("ix_user_cat_kind", table_name="user_category_projection")
    op.drop_table("user_category_projection")

    # 4. sync_changes 还原:删 NULL ledger_id 行 + 还原 NOT NULL + drop scope
    op.drop_index("idx_sync_changes_user_scope_cursor", table_name="sync_changes")
    op.execute("DELETE FROM sync_changes WHERE ledger_id IS NULL")
    with op.batch_alter_table("sync_changes") as batch_op:
        batch_op.alter_column(
            "ledger_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        batch_op.drop_column("scope")


def _recreate_legacy_projection_tables() -> None:
    op.create_table(
        "read_category_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(255), nullable=True),
        sa.Column("icon_type", sa.String(32), nullable=True),
        sa.Column("custom_icon_path", sa.String(1024), nullable=True),
        sa.Column("icon_cloud_file_id", sa.String(36), nullable=True),
        sa.Column("icon_cloud_sha256", sa.String(64), nullable=True),
        sa.Column("parent_name", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_cat_ledger_kind",
        "read_category_projection",
        ["ledger_id", "kind"],
    )
    op.create_index(
        op.f("ix_read_category_projection_user_id"),
        "read_category_projection",
        ["user_id"],
    )

    op.create_table(
        "read_account_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("account_type", sa.String(64), nullable=True),
        sa.Column("currency", sa.String(16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("credit_limit", sa.Float(), nullable=True),
        sa.Column("billing_day", sa.Integer(), nullable=True),
        sa.Column("payment_due_day", sa.Integer(), nullable=True),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("card_last_four", sa.String(8), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        op.f("ix_read_account_projection_user_id"),
        "read_account_projection",
        ["user_id"],
    )

    op.create_table(
        "read_tag_projection",
        sa.Column("ledger_id", sa.String(36), nullable=False),
        sa.Column("sync_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("color", sa.String(32), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        op.f("ix_read_tag_projection_user_id"),
        "read_tag_projection",
        ["user_id"],
    )


def _downgrade_migrate_categories(conn) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT user_id, sync_id, name, kind, level, sort_order, icon, "
            "icon_type, custom_icon_path, icon_cloud_file_id, icon_cloud_sha256, "
            "parent_name, source_change_id FROM user_category_projection"
        )
    ).fetchall()
    for row in rows:
        ledger_id = conn.execute(
            sa.text(
                "SELECT id FROM ledgers WHERE user_id = :uid "
                "ORDER BY created_at ASC LIMIT 1"
            ),
            {"uid": row[0]},
        ).scalar()
        if ledger_id is None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO read_category_projection ("
                "ledger_id, sync_id, user_id, name, kind, level, sort_order, icon, "
                "icon_type, custom_icon_path, icon_cloud_file_id, icon_cloud_sha256, "
                "parent_name, source_change_id) VALUES ("
                ":ledger_id, :sync_id, :user_id, :name, :kind, :level, :sort_order, "
                ":icon, :icon_type, :custom_icon_path, :icon_cloud_file_id, "
                ":icon_cloud_sha256, :parent_name, :source_change_id)"
            ),
            {
                "ledger_id": ledger_id,
                "sync_id": row[1],
                "user_id": row[0],
                "name": row[2],
                "kind": row[3],
                "level": row[4],
                "sort_order": row[5],
                "icon": row[6],
                "icon_type": row[7],
                "custom_icon_path": row[8],
                "icon_cloud_file_id": row[9],
                "icon_cloud_sha256": row[10],
                "parent_name": row[11],
                "source_change_id": row[12],
            },
        )


def _downgrade_migrate_accounts(conn) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT user_id, sync_id, name, account_type, currency, initial_balance, "
            "note, credit_limit, billing_day, payment_due_day, bank_name, "
            "card_last_four, source_change_id FROM user_account_projection"
        )
    ).fetchall()
    for row in rows:
        ledger_id = conn.execute(
            sa.text(
                "SELECT id FROM ledgers WHERE user_id = :uid "
                "ORDER BY created_at ASC LIMIT 1"
            ),
            {"uid": row[0]},
        ).scalar()
        if ledger_id is None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO read_account_projection ("
                "ledger_id, sync_id, user_id, name, account_type, currency, "
                "initial_balance, note, credit_limit, billing_day, payment_due_day, "
                "bank_name, card_last_four, source_change_id) VALUES ("
                ":ledger_id, :sync_id, :user_id, :name, :account_type, :currency, "
                ":initial_balance, :note, :credit_limit, :billing_day, "
                ":payment_due_day, :bank_name, :card_last_four, :source_change_id)"
            ),
            {
                "ledger_id": ledger_id,
                "sync_id": row[1],
                "user_id": row[0],
                "name": row[2],
                "account_type": row[3],
                "currency": row[4],
                "initial_balance": row[5],
                "note": row[6],
                "credit_limit": row[7],
                "billing_day": row[8],
                "payment_due_day": row[9],
                "bank_name": row[10],
                "card_last_four": row[11],
                "source_change_id": row[12],
            },
        )


def _downgrade_migrate_tags(conn) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT user_id, sync_id, name, color, source_change_id "
            "FROM user_tag_projection"
        )
    ).fetchall()
    for row in rows:
        ledger_id = conn.execute(
            sa.text(
                "SELECT id FROM ledgers WHERE user_id = :uid "
                "ORDER BY created_at ASC LIMIT 1"
            ),
            {"uid": row[0]},
        ).scalar()
        if ledger_id is None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO read_tag_projection (ledger_id, sync_id, user_id, name, "
                "color, source_change_id) VALUES (:ledger_id, :sync_id, :user_id, "
                ":name, :color, :source_change_id)"
            ),
            {
                "ledger_id": ledger_id,
                "sync_id": row[1],
                "user_id": row[0],
                "name": row[2],
                "color": row[3],
                "source_change_id": row[4],
            },
        )
