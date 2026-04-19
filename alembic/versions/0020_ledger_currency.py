"""add ledgers.currency + backfill from snapshots

Revision ID: 0020_ledger_currency
Revises: 0019_read_projection_tables
Create Date: 2026-04-19

方案 B 阶段 1:把 currency 从 snapshot 拆到 ledgers 表列。之后 /read/* 完全不用
parse snapshot,读路径最后一处 snapshot 依赖移除。

回填:扫每个 ledger 的最新 ledger_snapshot,抓 snapshot.currency 填进去;
snapshot.ledgerName 如果 Ledger.name 为空也顺手回填。
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0020_ledger_currency"
down_revision = "0019_read_projection_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 加列
    op.add_column(
        "ledgers",
        sa.Column("currency", sa.String(length=16), nullable=False, server_default="CNY"),
    )

    # 2. 回填:从 latest ledger_snapshot 抽 currency + ledgerName
    bind = op.get_bind()
    ledgers = bind.execute(
        sa.text("SELECT id, name FROM ledgers")
    ).fetchall()
    for ledger_id, current_name in ledgers:
        snap_row = bind.execute(
            sa.text(
                """
                SELECT payload_json FROM sync_changes
                WHERE ledger_id = :lid AND entity_type = 'ledger_snapshot'
                ORDER BY change_id DESC LIMIT 1
                """
            ),
            {"lid": ledger_id},
        ).fetchone()
        if snap_row is None:
            continue
        payload_raw = snap_row[0]
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

        currency = (snapshot.get("currency") or "").strip() or "CNY"
        ledger_name = (snapshot.get("ledgerName") or "").strip()

        if currency and currency != "CNY":
            bind.execute(
                sa.text("UPDATE ledgers SET currency = :c WHERE id = :lid"),
                {"c": currency[:16], "lid": ledger_id},
            )
        if ledger_name and not (current_name or "").strip():
            bind.execute(
                sa.text("UPDATE ledgers SET name = :n WHERE id = :lid"),
                {"n": ledger_name[:255], "lid": ledger_id},
            )


def downgrade() -> None:
    op.drop_column("ledgers", "currency")
