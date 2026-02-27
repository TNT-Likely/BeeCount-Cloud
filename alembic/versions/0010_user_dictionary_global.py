"""user scoped dictionaries and tx dictionary references

Revision ID: 0010_user_dictionary_global
Revises: 0009_category_icon_cloud_fields
Create Date: 2026-02-27 00:00:00.000000

"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "0010_user_dictionary_global"
down_revision = "0009_category_icon_cloud_fields"
branch_labels = None
depends_on = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def upgrade() -> None:
    op.create_table(
        "user_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("initial_balance", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_accounts_user_id", "user_accounts", ["user_id"], unique=False)
    op.create_index("ix_user_accounts_name", "user_accounts", ["name"], unique=False)
    op.create_index("ix_user_accounts_created_at", "user_accounts", ["created_at"], unique=False)
    op.create_index("ix_user_accounts_updated_at", "user_accounts", ["updated_at"], unique=False)
    op.create_index("ix_user_accounts_deleted_at", "user_accounts", ["deleted_at"], unique=False)
    op.create_index("idx_user_accounts_user_name", "user_accounts", ["user_id", "name"], unique=False)

    op.create_table(
        "user_categories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("icon", sa.String(length=255), nullable=True),
        sa.Column("icon_type", sa.String(length=32), nullable=True),
        sa.Column("custom_icon_path", sa.String(length=1024), nullable=True),
        sa.Column("icon_cloud_file_id", sa.String(length=36), nullable=True),
        sa.Column("icon_cloud_sha256", sa.String(length=64), nullable=True),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["icon_cloud_file_id"], ["attachment_files.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_id"], ["user_categories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_categories_user_id", "user_categories", ["user_id"], unique=False)
    op.create_index("ix_user_categories_name", "user_categories", ["name"], unique=False)
    op.create_index("ix_user_categories_kind", "user_categories", ["kind"], unique=False)
    op.create_index("ix_user_categories_icon_cloud_file_id", "user_categories", ["icon_cloud_file_id"], unique=False)
    op.create_index("ix_user_categories_parent_id", "user_categories", ["parent_id"], unique=False)
    op.create_index("ix_user_categories_created_at", "user_categories", ["created_at"], unique=False)
    op.create_index("ix_user_categories_updated_at", "user_categories", ["updated_at"], unique=False)
    op.create_index("ix_user_categories_deleted_at", "user_categories", ["deleted_at"], unique=False)
    op.create_index(
        "idx_user_categories_user_kind_name",
        "user_categories",
        ["user_id", "kind", "name"],
        unique=False,
    )

    op.create_table(
        "user_tags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("color", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_tags_user_id", "user_tags", ["user_id"], unique=False)
    op.create_index("ix_user_tags_name", "user_tags", ["name"], unique=False)
    op.create_index("ix_user_tags_created_at", "user_tags", ["created_at"], unique=False)
    op.create_index("ix_user_tags_updated_at", "user_tags", ["updated_at"], unique=False)
    op.create_index("ix_user_tags_deleted_at", "user_tags", ["deleted_at"], unique=False)
    op.create_index("idx_user_tags_user_name", "user_tags", ["user_id", "name"], unique=False)

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_user_accounts_active_name
        ON user_accounts(user_id, lower(name))
        WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_user_categories_active_kind_name
        ON user_categories(user_id, kind, lower(name))
        WHERE deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tags_active_name
        ON user_tags(user_id, lower(name))
        WHERE deleted_at IS NULL
        """
    )

    with op.batch_alter_table("web_transaction_projection") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("from_account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("to_account_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("category_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("tag_ids_json", sa.JSON(), nullable=True))
        batch_op.create_foreign_key(
            "fk_web_tx_projection_account_id",
            "user_accounts",
            ["account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_web_tx_projection_from_account_id",
            "user_accounts",
            ["from_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_web_tx_projection_to_account_id",
            "user_accounts",
            ["to_account_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_web_tx_projection_category_id",
            "user_categories",
            ["category_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_web_transaction_projection_account_id", ["account_id"], unique=False)
        batch_op.create_index("ix_web_transaction_projection_from_account_id", ["from_account_id"], unique=False)
        batch_op.create_index("ix_web_transaction_projection_to_account_id", ["to_account_id"], unique=False)
        batch_op.create_index("ix_web_transaction_projection_category_id", ["category_id"], unique=False)

    bind = op.get_bind()
    now = _utcnow()
    ledger_owner_rows = bind.execute(sa.text("SELECT id, user_id FROM ledgers")).mappings().all()
    ledger_owner_map = {row["id"]: row["user_id"] for row in ledger_owner_rows if row["id"] and row["user_id"]}

    account_key_to_id: dict[tuple[str, str], str] = {}
    category_key_to_id: dict[tuple[str, str, str], str] = {}
    tag_key_to_id: dict[tuple[str, str], str] = {}

    def normalize_name(raw: str | None) -> str:
        return (raw or "").strip()

    def ensure_account(
        *,
        user_id: str,
        name: str,
        account_type: str | None = None,
        currency: str | None = None,
        initial_balance: float | None = None,
    ) -> str:
        normalized = normalize_name(name)
        if not normalized:
            return ""
        key = (user_id, normalized.lower())
        existing = account_key_to_id.get(key)
        if existing:
            return existing
        row_id = str(uuid4())
        bind.execute(
            sa.text(
                """
                INSERT INTO user_accounts
                (id, user_id, name, account_type, currency, initial_balance, created_at, updated_at, deleted_at)
                VALUES (:id, :user_id, :name, :account_type, :currency, :initial_balance, :created_at, :updated_at, NULL)
                """
            ),
            {
                "id": row_id,
                "user_id": user_id,
                "name": normalized,
                "account_type": account_type,
                "currency": currency,
                "initial_balance": initial_balance,
                "created_at": now,
                "updated_at": now,
            },
        )
        account_key_to_id[key] = row_id
        return row_id

    def ensure_category(
        *,
        user_id: str,
        kind: str,
        name: str,
        level: int | None = None,
        sort_order: int | None = None,
        icon: str | None = None,
        icon_type: str | None = None,
        custom_icon_path: str | None = None,
        icon_cloud_file_id: str | None = None,
        icon_cloud_sha256: str | None = None,
    ) -> str:
        normalized_name = normalize_name(name)
        normalized_kind = normalize_name(kind)
        if not normalized_name or not normalized_kind:
            return ""
        key = (user_id, normalized_kind, normalized_name.lower())
        existing = category_key_to_id.get(key)
        if existing:
            return existing
        row_id = str(uuid4())
        bind.execute(
            sa.text(
                """
                INSERT INTO user_categories
                (id, user_id, name, kind, level, sort_order, icon, icon_type, custom_icon_path, icon_cloud_file_id,
                 icon_cloud_sha256, parent_id, created_at, updated_at, deleted_at)
                VALUES (:id, :user_id, :name, :kind, :level, :sort_order, :icon, :icon_type, :custom_icon_path,
                        :icon_cloud_file_id, :icon_cloud_sha256, NULL, :created_at, :updated_at, NULL)
                """
            ),
            {
                "id": row_id,
                "user_id": user_id,
                "name": normalized_name,
                "kind": normalized_kind,
                "level": level,
                "sort_order": sort_order,
                "icon": icon,
                "icon_type": icon_type,
                "custom_icon_path": custom_icon_path,
                "icon_cloud_file_id": icon_cloud_file_id,
                "icon_cloud_sha256": icon_cloud_sha256,
                "created_at": now,
                "updated_at": now,
            },
        )
        category_key_to_id[key] = row_id
        return row_id

    def ensure_tag(*, user_id: str, name: str, color: str | None = None) -> str:
        normalized = normalize_name(name)
        if not normalized:
            return ""
        key = (user_id, normalized.lower())
        existing = tag_key_to_id.get(key)
        if existing:
            return existing
        row_id = str(uuid4())
        bind.execute(
            sa.text(
                """
                INSERT INTO user_tags
                (id, user_id, name, color, created_at, updated_at, deleted_at)
                VALUES (:id, :user_id, :name, :color, :created_at, :updated_at, NULL)
                """
            ),
            {
                "id": row_id,
                "user_id": user_id,
                "name": normalized,
                "color": color,
                "created_at": now,
                "updated_at": now,
            },
        )
        tag_key_to_id[key] = row_id
        return row_id

    account_rows = bind.execute(
        sa.text(
            """
            SELECT p.id, p.ledger_id, p.created_by_user_id, p.name, p.account_type, p.currency, p.initial_balance
            FROM web_account_projection AS p
            ORDER BY p.id ASC
            """
        )
    ).mappings().all()
    for row in account_rows:
        user_id = row["created_by_user_id"] or ledger_owner_map.get(row["ledger_id"])
        if not user_id:
            continue
        ensure_account(
            user_id=user_id,
            name=row["name"],
            account_type=row["account_type"],
            currency=row["currency"],
            initial_balance=row["initial_balance"],
        )

    category_rows = bind.execute(
        sa.text(
            """
            SELECT p.id, p.ledger_id, p.created_by_user_id, p.name, p.kind, p.level, p.sort_order, p.icon, p.icon_type,
                   p.custom_icon_path, p.icon_cloud_file_id, p.icon_cloud_sha256
            FROM web_category_projection AS p
            ORDER BY p.id ASC
            """
        )
    ).mappings().all()
    for row in category_rows:
        user_id = row["created_by_user_id"] or ledger_owner_map.get(row["ledger_id"])
        if not user_id:
            continue
        ensure_category(
            user_id=user_id,
            kind=row["kind"],
            name=row["name"],
            level=row["level"],
            sort_order=row["sort_order"],
            icon=row["icon"],
            icon_type=row["icon_type"],
            custom_icon_path=row["custom_icon_path"],
            icon_cloud_file_id=row["icon_cloud_file_id"],
            icon_cloud_sha256=row["icon_cloud_sha256"],
        )

    tag_rows = bind.execute(
        sa.text(
            """
            SELECT p.id, p.ledger_id, p.created_by_user_id, p.name, p.color
            FROM web_tag_projection AS p
            ORDER BY p.id ASC
            """
        )
    ).mappings().all()
    for row in tag_rows:
        user_id = row["created_by_user_id"] or ledger_owner_map.get(row["ledger_id"])
        if not user_id:
            continue
        ensure_tag(user_id=user_id, name=row["name"], color=row["color"])

    tx_rows = bind.execute(
        sa.text(
            """
            SELECT id, ledger_id, created_by_user_id, account_name, from_account_name, to_account_name,
                   category_name, category_kind, tags
            FROM web_transaction_projection
            ORDER BY id ASC
            """
        )
    ).mappings().all()
    for row in tx_rows:
        user_id = row["created_by_user_id"] or ledger_owner_map.get(row["ledger_id"])
        if not user_id:
            continue
        account_id = ensure_account(user_id=user_id, name=row["account_name"])
        from_account_id = ensure_account(user_id=user_id, name=row["from_account_name"])
        to_account_id = ensure_account(user_id=user_id, name=row["to_account_name"])
        category_id = ensure_category(
            user_id=user_id,
            kind=row["category_kind"] or "",
            name=row["category_name"] or "",
        )
        tag_ids: list[str] = []
        raw_tags = row["tags"] if isinstance(row["tags"], str) else ""
        for name in [part.strip() for part in raw_tags.split(",") if part.strip()]:
            tag_id = ensure_tag(user_id=user_id, name=name)
            if tag_id and tag_id not in tag_ids:
                tag_ids.append(tag_id)
        bind.execute(
            sa.text(
                """
                UPDATE web_transaction_projection
                SET account_id=:account_id,
                    from_account_id=:from_account_id,
                    to_account_id=:to_account_id,
                    category_id=:category_id,
                    tag_ids_json=:tag_ids_json
                WHERE id=:id
                """
            ),
            {
                "id": row["id"],
                "account_id": account_id or None,
                "from_account_id": from_account_id or None,
                "to_account_id": to_account_id or None,
                "category_id": category_id or None,
                "tag_ids_json": json.dumps(tag_ids) if tag_ids else None,
            },
        )


def downgrade() -> None:
    with op.batch_alter_table("web_transaction_projection") as batch_op:
        batch_op.drop_index("ix_web_transaction_projection_category_id")
        batch_op.drop_index("ix_web_transaction_projection_to_account_id")
        batch_op.drop_index("ix_web_transaction_projection_from_account_id")
        batch_op.drop_index("ix_web_transaction_projection_account_id")
        batch_op.drop_constraint("fk_web_tx_projection_category_id", type_="foreignkey")
        batch_op.drop_constraint("fk_web_tx_projection_to_account_id", type_="foreignkey")
        batch_op.drop_constraint("fk_web_tx_projection_from_account_id", type_="foreignkey")
        batch_op.drop_constraint("fk_web_tx_projection_account_id", type_="foreignkey")
        batch_op.drop_column("tag_ids_json")
        batch_op.drop_column("category_id")
        batch_op.drop_column("to_account_id")
        batch_op.drop_column("from_account_id")
        batch_op.drop_column("account_id")

    op.execute("DROP INDEX IF EXISTS uq_user_tags_active_name")
    op.execute("DROP INDEX IF EXISTS uq_user_categories_active_kind_name")
    op.execute("DROP INDEX IF EXISTS uq_user_accounts_active_name")

    op.drop_index("idx_user_tags_user_name", table_name="user_tags")
    op.drop_index("ix_user_tags_deleted_at", table_name="user_tags")
    op.drop_index("ix_user_tags_updated_at", table_name="user_tags")
    op.drop_index("ix_user_tags_created_at", table_name="user_tags")
    op.drop_index("ix_user_tags_name", table_name="user_tags")
    op.drop_index("ix_user_tags_user_id", table_name="user_tags")
    op.drop_table("user_tags")

    op.drop_index("idx_user_categories_user_kind_name", table_name="user_categories")
    op.drop_index("ix_user_categories_deleted_at", table_name="user_categories")
    op.drop_index("ix_user_categories_updated_at", table_name="user_categories")
    op.drop_index("ix_user_categories_created_at", table_name="user_categories")
    op.drop_index("ix_user_categories_parent_id", table_name="user_categories")
    op.drop_index("ix_user_categories_icon_cloud_file_id", table_name="user_categories")
    op.drop_index("ix_user_categories_kind", table_name="user_categories")
    op.drop_index("ix_user_categories_name", table_name="user_categories")
    op.drop_index("ix_user_categories_user_id", table_name="user_categories")
    op.drop_table("user_categories")

    op.drop_index("idx_user_accounts_user_name", table_name="user_accounts")
    op.drop_index("ix_user_accounts_deleted_at", table_name="user_accounts")
    op.drop_index("ix_user_accounts_updated_at", table_name="user_accounts")
    op.drop_index("ix_user_accounts_created_at", table_name="user_accounts")
    op.drop_index("ix_user_accounts_name", table_name="user_accounts")
    op.drop_index("ix_user_accounts_user_id", table_name="user_accounts")
    op.drop_table("user_accounts")

