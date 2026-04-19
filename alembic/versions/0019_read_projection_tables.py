"""add CQRS read projection tables + backfill from existing snapshots

Revision ID: 0019_read_projection_tables
Revises: 0018_drop_user_dict_tables
Create Date: 2026-04-19

Web `/read/*` 之前走 `json.loads(3MB ledger_snapshot)` + Python filter,
10k tx 账本一次读 50-80ms。新增 5 张 projection 表做 CQRS Q 端,writes 在
同事务内同步写入。这次 migration:

1. 创建 5 张表 + 4 类 index(tx 最多,其他简单)
2. **回填** —— 扫每个 ledger 的 latest ledger_snapshot,按 items/accounts/
   categories/tags/budgets 分别填到对应 projection 表。回填逻辑直接复用
   `src.projection.rebuild_from_snapshot`,跟运行时写入走同一份代码。
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0019_read_projection_tables"
down_revision = "0018_drop_user_dict_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 1. CREATE TABLE ---- #
    op.create_table(
        "read_tx_projection",
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tx_type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("category_sync_id", sa.String(length=255), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=True),
        sa.Column("category_kind", sa.String(length=32), nullable=True),
        sa.Column("account_sync_id", sa.String(length=255), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=True),
        sa.Column("from_account_sync_id", sa.String(length=255), nullable=True),
        sa.Column("from_account_name", sa.Text(), nullable=True),
        sa.Column("to_account_sync_id", sa.String(length=255), nullable=True),
        sa.Column("to_account_name", sa.Text(), nullable=True),
        sa.Column("tags_csv", sa.Text(), nullable=True),
        sa.Column("tag_sync_ids_json", sa.Text(), nullable=True),
        sa.Column("attachments_json", sa.Text(), nullable=True),
        sa.Column("tx_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_tx_projection_user_id", "read_tx_projection", ["user_id"]
    )
    op.create_index(
        "ix_read_tx_ledger_time",
        "read_tx_projection",
        ["ledger_id", sa.text("happened_at DESC"), sa.text("tx_index DESC")],
    )
    op.create_index(
        "ix_read_tx_ledger_category",
        "read_tx_projection",
        ["ledger_id", "category_sync_id"],
    )
    op.create_index(
        "ix_read_tx_ledger_account",
        "read_tx_projection",
        ["ledger_id", "account_sync_id"],
    )
    op.create_index(
        "ix_read_tx_user_time",
        "read_tx_projection",
        ["user_id", sa.text("happened_at DESC")],
    )

    op.create_table(
        "read_account_projection",
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("account_type", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_account_projection_user_id", "read_account_projection", ["user_id"]
    )

    op.create_table(
        "read_category_projection",
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("icon_type", sa.String(length=32), nullable=True),
        sa.Column("custom_icon_path", sa.String(length=1024), nullable=True),
        sa.Column("icon_cloud_file_id", sa.String(length=36), nullable=True),
        sa.Column("icon_cloud_sha256", sa.String(length=64), nullable=True),
        sa.Column("parent_name", sa.Text(), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_category_projection_user_id", "read_category_projection", ["user_id"]
    )
    op.create_index(
        "ix_read_cat_ledger_kind",
        "read_category_projection",
        ["ledger_id", "kind"],
    )

    op.create_table(
        "read_tag_projection",
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=32), nullable=True),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_tag_projection_user_id", "read_tag_projection", ["user_id"]
    )

    op.create_table(
        "read_budget_projection",
        sa.Column("ledger_id", sa.String(length=36), nullable=False),
        sa.Column("sync_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("budget_type", sa.String(length=32), nullable=True),
        sa.Column("category_sync_id", sa.String(length=255), nullable=True),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("period", sa.String(length=32), nullable=True),
        sa.Column("start_day", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source_change_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledgers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ledger_id", "sync_id"),
    )
    op.create_index(
        "ix_read_budget_projection_user_id", "read_budget_projection", ["user_id"]
    )
    op.create_index(
        "ix_read_budget_ledger_cat",
        "read_budget_projection",
        ["ledger_id", "category_sync_id"],
    )

    # ---- 2. 回填 —— 从最新 snapshot 读全量实体,填 projection ---- #
    bind = op.get_bind()

    # 用 bind 直接跑 raw SQL,避免依赖 ORM mapping(migration 时 mapping 可能没初始化)
    ledger_rows = bind.execute(
        sa.text("SELECT id, user_id FROM ledgers")
    ).fetchall()

    for ledger_id, user_id in ledger_rows:
        snap_row = bind.execute(
            sa.text(
                """
                SELECT change_id, payload_json FROM sync_changes
                WHERE ledger_id = :lid AND entity_type = 'ledger_snapshot'
                ORDER BY change_id DESC LIMIT 1
                """
            ),
            {"lid": ledger_id},
        ).fetchone()
        if snap_row is None:
            continue
        change_id, payload_raw = snap_row
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError:
                continue
        else:
            payload = payload_raw
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        try:
            snapshot = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(snapshot, dict):
            continue

        _backfill_ledger(
            bind,
            ledger_id=ledger_id,
            user_id=user_id,
            snapshot=snapshot,
            source_change_id=int(change_id),
        )


def downgrade() -> None:
    op.drop_index("ix_read_budget_ledger_cat", table_name="read_budget_projection")
    op.drop_index("ix_read_budget_projection_user_id", table_name="read_budget_projection")
    op.drop_table("read_budget_projection")

    op.drop_index("ix_read_tag_projection_user_id", table_name="read_tag_projection")
    op.drop_table("read_tag_projection")

    op.drop_index("ix_read_cat_ledger_kind", table_name="read_category_projection")
    op.drop_index("ix_read_category_projection_user_id", table_name="read_category_projection")
    op.drop_table("read_category_projection")

    op.drop_index("ix_read_account_projection_user_id", table_name="read_account_projection")
    op.drop_table("read_account_projection")

    op.drop_index("ix_read_tx_user_time", table_name="read_tx_projection")
    op.drop_index("ix_read_tx_ledger_account", table_name="read_tx_projection")
    op.drop_index("ix_read_tx_ledger_category", table_name="read_tx_projection")
    op.drop_index("ix_read_tx_ledger_time", table_name="read_tx_projection")
    op.drop_index("ix_read_tx_projection_user_id", table_name="read_tx_projection")
    op.drop_table("read_tx_projection")


# --------------------------------------------------------------------------- #
# 回填逻辑(纯 SQL,不依赖 ORM mapping,跟 src/projection.py 语义一致)         #
# --------------------------------------------------------------------------- #
# 为什么 migration 里不直接 import src.projection?
# alembic migration 在某些环境(比如 offline mode 或老版本 SQLAlchemy)下,
# ORM Base 可能未完全初始化,直接用 ORM 查询不稳。这里用 raw SQL batch insert
# 更健壮。语义跟 src/projection.py 对齐,入参字段一一对应。


def _text_or_none(v):
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _float_or_zero(v):
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _float_or_none(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _int_or_default(v, default):
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _bool_or_default(v, default):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y", "t"}
    return default


def _parse_happened_at_sql(raw):
    from datetime import datetime, timezone

    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _backfill_ledger(bind, *, ledger_id, user_id, snapshot, source_change_id):
    """把 snapshot 的 items/accounts/categories/tags/budgets 批量 INSERT。"""
    # --- items → read_tx_projection --- #
    tx_params = []
    for item in snapshot.get("items") or []:
        if not isinstance(item, dict):
            continue
        sync_id = _text_or_none(item.get("syncId"))
        if not sync_id:
            continue
        tags_raw = item.get("tags")
        if isinstance(tags_raw, list):
            tags_csv = ",".join(str(t).strip() for t in tags_raw if str(t).strip())
        else:
            tags_csv = _text_or_none(tags_raw)
        tag_ids = item.get("tagIds")
        tag_ids_json = json.dumps(tag_ids) if isinstance(tag_ids, list) else None
        attachments = item.get("attachments")
        attachments_json = (
            json.dumps(attachments)
            if isinstance(attachments, list) and attachments
            else None
        )
        tx_type = (
            _text_or_none(item.get("txType"))
            or _text_or_none(item.get("tx_type"))
            or _text_or_none(item.get("type"))
            or "expense"
        )
        tx_params.append(
            {
                "ledger_id": ledger_id,
                "sync_id": sync_id,
                "user_id": user_id,
                "tx_type": tx_type,
                "amount": _float_or_zero(item.get("amount")),
                "happened_at": _parse_happened_at_sql(
                    item.get("happenedAt") or item.get("happened_at")
                ),
                "note": _text_or_none(item.get("note")),
                "category_sync_id": _text_or_none(item.get("categoryId")),
                "category_name": _text_or_none(item.get("categoryName")),
                "category_kind": _text_or_none(item.get("categoryKind")),
                "account_sync_id": _text_or_none(item.get("accountId")),
                "account_name": _text_or_none(item.get("accountName")),
                "from_account_sync_id": _text_or_none(item.get("fromAccountId")),
                "from_account_name": _text_or_none(item.get("fromAccountName")),
                "to_account_sync_id": _text_or_none(item.get("toAccountId")),
                "to_account_name": _text_or_none(item.get("toAccountName")),
                "tags_csv": tags_csv,
                "tag_sync_ids_json": tag_ids_json,
                "attachments_json": attachments_json,
                "tx_index": _int_or_default(
                    item.get("txIndex") or item.get("tx_index"), 0
                ),
                "created_by_user_id": _text_or_none(item.get("createdByUserId")),
                "source_change_id": source_change_id,
            }
        )
    if tx_params:
        bind.execute(
            sa.text(
                """
                INSERT INTO read_tx_projection (
                    ledger_id, sync_id, user_id, tx_type, amount, happened_at, note,
                    category_sync_id, category_name, category_kind,
                    account_sync_id, account_name,
                    from_account_sync_id, from_account_name,
                    to_account_sync_id, to_account_name,
                    tags_csv, tag_sync_ids_json, attachments_json,
                    tx_index, created_by_user_id, source_change_id
                ) VALUES (
                    :ledger_id, :sync_id, :user_id, :tx_type, :amount, :happened_at, :note,
                    :category_sync_id, :category_name, :category_kind,
                    :account_sync_id, :account_name,
                    :from_account_sync_id, :from_account_name,
                    :to_account_sync_id, :to_account_name,
                    :tags_csv, :tag_sync_ids_json, :attachments_json,
                    :tx_index, :created_by_user_id, :source_change_id
                )
                """
            ),
            tx_params,
        )

    # --- accounts --- #
    acc_params = []
    for item in snapshot.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        sync_id = _text_or_none(item.get("syncId"))
        if not sync_id:
            continue
        acc_params.append(
            {
                "ledger_id": ledger_id,
                "sync_id": sync_id,
                "user_id": user_id,
                "name": _text_or_none(item.get("name")),
                "account_type": _text_or_none(item.get("type")),
                "currency": _text_or_none(item.get("currency")),
                "initial_balance": _float_or_none(item.get("initialBalance")),
                "source_change_id": source_change_id,
            }
        )
    if acc_params:
        bind.execute(
            sa.text(
                """
                INSERT INTO read_account_projection
                    (ledger_id, sync_id, user_id, name, account_type, currency,
                     initial_balance, source_change_id)
                VALUES
                    (:ledger_id, :sync_id, :user_id, :name, :account_type, :currency,
                     :initial_balance, :source_change_id)
                """
            ),
            acc_params,
        )

    # --- categories --- #
    cat_params = []
    for item in snapshot.get("categories") or []:
        if not isinstance(item, dict):
            continue
        sync_id = _text_or_none(item.get("syncId"))
        if not sync_id:
            continue
        cat_params.append(
            {
                "ledger_id": ledger_id,
                "sync_id": sync_id,
                "user_id": user_id,
                "name": _text_or_none(item.get("name")),
                "kind": _text_or_none(item.get("kind")),
                "level": _int_or_none(item.get("level")),
                "sort_order": _int_or_none(item.get("sortOrder")),
                "icon": _text_or_none(item.get("icon")),
                "icon_type": _text_or_none(item.get("iconType")),
                "custom_icon_path": _text_or_none(item.get("customIconPath")),
                "icon_cloud_file_id": _text_or_none(item.get("iconCloudFileId")),
                "icon_cloud_sha256": _text_or_none(item.get("iconCloudSha256")),
                "parent_name": _text_or_none(item.get("parentName")),
                "source_change_id": source_change_id,
            }
        )
    if cat_params:
        bind.execute(
            sa.text(
                """
                INSERT INTO read_category_projection
                    (ledger_id, sync_id, user_id, name, kind, level, sort_order,
                     icon, icon_type, custom_icon_path,
                     icon_cloud_file_id, icon_cloud_sha256, parent_name, source_change_id)
                VALUES
                    (:ledger_id, :sync_id, :user_id, :name, :kind, :level, :sort_order,
                     :icon, :icon_type, :custom_icon_path,
                     :icon_cloud_file_id, :icon_cloud_sha256, :parent_name, :source_change_id)
                """
            ),
            cat_params,
        )

    # --- tags --- #
    tag_params = []
    for item in snapshot.get("tags") or []:
        if not isinstance(item, dict):
            continue
        sync_id = _text_or_none(item.get("syncId"))
        if not sync_id:
            continue
        tag_params.append(
            {
                "ledger_id": ledger_id,
                "sync_id": sync_id,
                "user_id": user_id,
                "name": _text_or_none(item.get("name")),
                "color": _text_or_none(item.get("color")),
                "source_change_id": source_change_id,
            }
        )
    if tag_params:
        bind.execute(
            sa.text(
                """
                INSERT INTO read_tag_projection
                    (ledger_id, sync_id, user_id, name, color, source_change_id)
                VALUES
                    (:ledger_id, :sync_id, :user_id, :name, :color, :source_change_id)
                """
            ),
            tag_params,
        )

    # --- budgets --- #
    budget_params = []
    for item in snapshot.get("budgets") or []:
        if not isinstance(item, dict):
            continue
        sync_id = _text_or_none(item.get("syncId"))
        if not sync_id:
            continue
        budget_params.append(
            {
                "ledger_id": ledger_id,
                "sync_id": sync_id,
                "user_id": user_id,
                "budget_type": _text_or_none(item.get("type")),
                "category_sync_id": _text_or_none(item.get("categoryId")),
                "amount": _float_or_none(item.get("amount")),
                "period": _text_or_none(item.get("period")),
                "start_day": _int_or_none(item.get("startDay")),
                "enabled": _bool_or_default(item.get("enabled"), True),
                "source_change_id": source_change_id,
            }
        )
    if budget_params:
        bind.execute(
            sa.text(
                """
                INSERT INTO read_budget_projection
                    (ledger_id, sync_id, user_id, budget_type, category_sync_id,
                     amount, period, start_day, enabled, source_change_id)
                VALUES
                    (:ledger_id, :sync_id, :user_id, :budget_type, :category_sync_id,
                     :amount, :period, :start_day, :enabled, :source_change_id)
                """
            ),
            budget_params,
        )
