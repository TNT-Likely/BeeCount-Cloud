"""Repair resolvable category parent links without guessing orphan ownership.

Revision ID: 0020_category_parent_links
Revises: 0019_account_hidden
"""
import sqlalchemy as sa
from alembic import op

revision = "0020_category_parent_links"
down_revision = "0019_account_hidden"
branch_labels = None
depends_on = None

# Correlated scalar subqueries work on both SQLite and PostgreSQL. Ambiguous
# names, deleted parents and cross-user/kind links remain unchanged for repair.
BACKFILL_STATEMENTS = [
    """
    UPDATE user_category_projection AS child
    SET parent_sync_id = (
        SELECT MIN(parent.sync_id) FROM user_category_projection AS parent
        WHERE parent.user_id = child.user_id AND parent.kind = child.kind
          AND parent.name = child.parent_name AND parent.sync_id <> child.sync_id
          AND COALESCE(parent.level, 1) = 1
          AND parent.parent_sync_id IS NULL AND parent.parent_name IS NULL
    )
    WHERE child.parent_sync_id IS NULL AND child.parent_name IS NOT NULL
      AND (SELECT COUNT(*) FROM user_category_projection AS parent
           WHERE parent.user_id = child.user_id AND parent.kind = child.kind
             AND parent.name = child.parent_name AND parent.sync_id <> child.sync_id
             AND COALESCE(parent.level, 1) = 1
             AND parent.parent_sync_id IS NULL AND parent.parent_name IS NULL) = 1
    """,
    """
    UPDATE user_category_projection AS child
    SET parent_name = (
        SELECT parent.name FROM user_category_projection AS parent
        WHERE parent.user_id = child.user_id AND parent.kind = child.kind
          AND parent.sync_id = child.parent_sync_id AND parent.sync_id <> child.sync_id
          AND COALESCE(parent.level, 1) = 1
    )
    WHERE child.parent_sync_id IS NOT NULL
      AND EXISTS (SELECT 1 FROM user_category_projection AS parent
                  WHERE parent.user_id = child.user_id AND parent.kind = child.kind
                    AND parent.sync_id = child.parent_sync_id AND parent.sync_id <> child.sync_id
                    AND COALESCE(parent.level, 1) = 1)
    """,
]


def upgrade() -> None:
    for statement in BACKFILL_STATEMENTS:
        op.get_bind().execute(sa.text(statement))


def downgrade() -> None:
    # Restoring stale names would discard valid relationships.
    pass
