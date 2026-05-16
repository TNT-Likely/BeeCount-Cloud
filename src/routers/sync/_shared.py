"""sync.py 的共享层。

原 src/routers/sync.py 按 endpoint 拆成 4 个子模块后,各 endpoint 都依赖的
import + 轻量 helper(_to_utc / _max_cursor_for_ledgers)+ router 实例本身
集中在这里。

真正的业务重头 —— "一条 SyncChange 怎么落到 projection 表" —— 在独立的
src/sync_applier.py 里(push.py 从那里 import apply_change_to_projection +
INDIVIDUAL_ENTITY_TYPES)。本模块只管 HTTP / LWW / 事务 / 游标推进这些
跨 endpoint 共用的样板。
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...concurrency import lock_ledger_for_materialize
from ...database import get_db
from ...deps import get_current_user, require_any_scopes, require_scopes
from ...ledger_access import (
    ensure_owner_member,
    get_accessible_ledger_by_external_id,
    list_accessible_ledgers,
)
from ...metrics import metrics
from ...models import (
    AuditLog,
    Device,
    Ledger,
    ReadTxProjection,  # list_ledgers 端点做 tx 计数估算用
    SyncChange,
    SyncCursor,
    User,
)
from ...schemas import (
    SyncChangeOut,
    SyncFullResponse,
    SyncLedgerOut,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from ...security import SCOPE_APP_WRITE, SCOPE_WEB_READ
from ...sync_applier import apply_change_to_projection, INDIVIDUAL_ENTITY_TYPES
from ... import snapshot_builder, snapshot_cache

logger = logging.getLogger(__name__)

# Router 在这里创建一次,各 endpoint 子模块 `from ._shared import *` 拉到
# 同一个实例,装饰器挂在同一个 router 上。__init__.py re-export。
router = APIRouter()


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _max_cursor_for_ledgers(db: Session, ledger_ids: list[str]) -> int:
    if not ledger_ids:
        return 0
    max_cursor = db.scalar(select(func.max(SyncChange.change_id)).where(SyncChange.ledger_id.in_(ledger_ids)))
    return int(max_cursor or 0)


__all__ = [
    'json',
    'logging',
    'datetime',
    'timedelta',
    'timezone',
    'Any',
    'cast',
    'APIRouter',
    'Depends',
    'HTTPException',
    'Query',
    'Request',
    'status',
    'func',
    'select',
    'Session',
    'lock_ledger_for_materialize',
    'get_db',
    'get_current_user',
    'require_any_scopes',
    'require_scopes',
    'ensure_owner_member',
    'get_accessible_ledger_by_external_id',
    'list_accessible_ledgers',
    'metrics',
    'AuditLog',
    'Device',
    'Ledger',
    'ReadTxProjection',
    'SyncChange',
    'SyncCursor',
    'User',
    'SyncChangeOut',
    'SyncFullResponse',
    'SyncLedgerOut',
    'SyncPullResponse',
    'SyncPushRequest',
    'SyncPushResponse',
    'SCOPE_APP_WRITE',
    'SCOPE_WEB_READ',
    'apply_change_to_projection',
    'INDIVIDUAL_ENTITY_TYPES',
    'snapshot_builder',
    'snapshot_cache',
    'logger',
    'router',
    '_to_utc',
    '_max_cursor_for_ledgers',
]
