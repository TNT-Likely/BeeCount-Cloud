"""backfill null category icons via Flutter byName rules

Revision ID: 0002_backfill_category_icons
Revises: 0001_init
Create Date: 2026-04-20

移动端历史上渲染分类图标有两级 fallback(lib/widgets/category_icon.dart):
  1. `category.icon` 非空 → 走 switch 渲染
  2. `category.icon` 空 → 按 **分类名字** 模糊匹配推导图标(getCategoryIconByName)

这个 name 推导只在 Flutter 客户端存在。web 拿到 icon 字段空就只能兜底通用图标
 —— 用户看到"爱车养车"没车图标、"母婴2"没婴儿车图标等一堆问题。

本迁移做 **write-time migration**:
  - 扫 read_category_projection,`icon IS NULL OR icon = ''` → byName 填回
  - 扫 sync_changes 里每个 ledger 的最新 ledger_snapshot,其 payload_json 的
    categories 数组里 icon 空的条目也 byName 填回,并写新的 SyncChange 行
    (新 change_id → mobile 下次 /sync/pull 会拉到更新)

对新建账本 / 新分类零影响:seed_service.dart 所有 insert 都显式 `icon: Value(
getDefaultIcon(key))`(203 个 seed key 全覆盖,零 fallback 到空)、新建分类页
initialValue='category'(也非空)。只有历史老数据 / 跨端同步下来的 null icon
分类会被动到。

参考:src/services/category_icon.py 里 `resolve_icon_by_name` 是 Flutter
`getCategoryIconByName` (lib/services/data/category_service.dart:8-201) 的 1:1
复刻。
"""
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

from src.services.category_icon import resolve_icon_by_name


revision = '0002_backfill_category_icons'
down_revision = '0001_init'
branch_labels = None
depends_on = None


def _backfill_projection(conn) -> int:
    """扫 read_category_projection,icon 空的按 byName 填回。返回处理行数。"""
    rows = conn.execute(
        sa.text(
            "SELECT ledger_id, sync_id, name FROM read_category_projection "
            "WHERE icon IS NULL OR icon = ''"
        )
    ).fetchall()
    updated = 0
    for ledger_id, sync_id, name in rows:
        resolved = resolve_icon_by_name(name)
        conn.execute(
            sa.text(
                "UPDATE read_category_projection SET icon = :icon "
                "WHERE ledger_id = :ledger_id AND sync_id = :sync_id"
            ),
            {"icon": resolved, "ledger_id": ledger_id, "sync_id": sync_id},
        )
        updated += 1
    return updated


def _latest_snapshot_per_ledger(conn):
    """每个 ledger 拿最新一条 entity_type='ledger_snapshot' action='upsert' 的 change。

    用窗口函数/相关子查询都行,这里用 subquery 兼容 SQLite / PostgreSQL 两边。
    """
    return conn.execute(
        sa.text(
            """
            SELECT sc.change_id, sc.ledger_id, sc.user_id, sc.payload_json
              FROM sync_changes sc
              JOIN (
                SELECT ledger_id, MAX(change_id) AS max_change_id
                  FROM sync_changes
                 WHERE entity_type = 'ledger_snapshot'
                   AND action = 'upsert'
                 GROUP BY ledger_id
              ) latest
                ON latest.ledger_id = sc.ledger_id
               AND latest.max_change_id = sc.change_id
            """
        )
    ).fetchall()


def _backfill_snapshots(conn) -> int:
    """扫每个 ledger 最新 snapshot,categories 数组里 icon 空的 byName 填回。

    改动后**新插入**一条 SyncChange(不原地 UPDATE 老行),确保 mobile 下次拉
    /sync/pull 能通过 cursor 拿到变更。
    """
    touched_ledgers = 0
    now = datetime.now(timezone.utc)

    for change_id, ledger_id, user_id, payload_json in _latest_snapshot_per_ledger(conn):
        if payload_json is None:
            continue

        # payload_json 在 SQLite 是 TEXT、在 PG 是 JSONB。driver 会自动 dict 化 PG 的;
        # SQLite 返回字符串,我们判断一下。
        payload = payload_json
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue

        # snapshot 真正的内容在 payload['content'](是 JSON 字符串,不是 dict)
        content_raw = payload.get("content")
        if not content_raw:
            continue
        if isinstance(content_raw, (bytes, bytearray)):
            content_raw = content_raw.decode("utf-8")
        if isinstance(content_raw, str):
            try:
                content = json.loads(content_raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(content_raw, dict):
            content = content_raw
        else:
            continue

        categories = content.get("categories")
        if not isinstance(categories, list):
            continue

        dirty = False
        for cat in categories:
            if not isinstance(cat, dict):
                continue
            icon = cat.get("icon")
            if icon is None or (isinstance(icon, str) and not icon.strip()):
                cat["icon"] = resolve_icon_by_name(cat.get("name"))
                dirty = True

        if not dirty:
            continue

        # 重新序列化回去
        new_content = json.dumps(content, ensure_ascii=False)
        new_payload = {
            **payload,
            "content": new_content,
            "metadata": {
                **(payload.get("metadata") or {}),
                "backfill": "0002_category_icons",
            },
        }

        # 插入新 SyncChange,同 entity_sync_id / entity_type,change_id 自增
        # 先 look up ledger external_id 做 entity_sync_id
        lid_row = conn.execute(
            sa.text("SELECT external_id FROM ledgers WHERE id = :id"),
            {"id": ledger_id},
        ).fetchone()
        if lid_row is None:
            continue
        external_id = lid_row[0]

        conn.execute(
            sa.text(
                """
                INSERT INTO sync_changes
                    (user_id, ledger_id, entity_type, entity_sync_id, action,
                     payload_json, updated_at, updated_by_device_id, updated_by_user_id)
                VALUES (:user_id, :ledger_id, 'ledger_snapshot', :entity_sync_id, 'upsert',
                        :payload_json, :updated_at, NULL, :user_id)
                """
            ),
            {
                "user_id": user_id,
                "ledger_id": ledger_id,
                "entity_sync_id": external_id,
                "payload_json": json.dumps(new_payload, ensure_ascii=False),
                "updated_at": now,
            },
        )
        touched_ledgers += 1

    return touched_ledgers


def upgrade() -> None:
    conn = op.get_bind()
    proj_count = _backfill_projection(conn)
    snap_count = _backfill_snapshots(conn)
    # alembic 的 output_encoding 默认是 stdout,docker logs 能看到
    print(
        f"[0002] backfilled {proj_count} projection rows, "
        f"{snap_count} ledger snapshots"
    )


def downgrade() -> None:
    # 回滚不可靠(backfill 是数据 patch,老数据已经没备份)。如果要回退只能
    # 手工恢复或重建 projection —— 这里空实现,并在日志留一笔。
    print("[0002] downgrade is a no-op; backfill cannot be reversed cleanly")
