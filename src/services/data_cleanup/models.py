"""孤儿数据模型 — Scanner 产出 / Cleaner 消费。

Pydantic 模型直接复用为 FastAPI router 的 response/request schema。OrphanType
枚举值跟 plan A1..A5 / B1..B4 / C... 一一对应,后续加新检测项扩枚举 + scanner
/ cleaner 各加一个 case。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OrphanType(str, Enum):
    """孤儿数据类型。值用 snake_case 字符串,FastAPI 序列化 JSON 时为字符串。

    A 类 = DB 引用断链;B 类 = 文件/DB 一致性;C 类 = sync_changes 异常。
    """

    # A 类:DB 引用断链
    TX_MISSING_CATEGORY = "tx_missing_category"
    TX_MISSING_ACCOUNT = "tx_missing_account"
    TX_MISSING_FROM_ACCOUNT = "tx_missing_from_account"
    TX_MISSING_TO_ACCOUNT = "tx_missing_to_account"
    BUDGET_MISSING_CATEGORY = "budget_missing_category"
    SYNC_CHANGE_MISSING_ENTITY = "sync_change_missing_entity"

    # B 类:附件 / 文件
    ATTACHMENT_NO_REF = "attachment_no_ref"  # B1: AttachmentFile 行无 tx/category 引用
    ATTACHMENT_FILE_MISSING = "attachment_file_missing"  # B2: storage_path 文件丢
    DISK_FILE_NO_ROW = "disk_file_no_row"  # B3: 磁盘文件无 DB 行
    TX_REF_BROKEN_ATTACHMENT = "tx_ref_broken_attachment"  # B4: tx 引用 fileId 不存在


class OrphanRecord(BaseModel):
    """单条孤儿数据。

    - DB 类(A/C):`row_id` 或 `sync_id` 非空,`file_path` / `size_bytes` 一般为 None
    - 文件类(B2/B3):`file_path` 非空
    - 文件类(B1):`row_id` 非空 + `file_path` 是 storage_path
    """

    type: OrphanType
    title: str  # UI 主标题
    subtitle: str  # UI 副标题
    user_id: str | None = None
    row_id: str | None = None  # 主表行 id(DB 类)— str 兼容 UUID / int
    sync_id: str | None = None
    file_path: str | None = None  # B 类
    size_bytes: int | None = None
    extra: dict[str, Any] | None = None  # cleaner 内部用

    @property
    def unique_key(self) -> str:
        """UI 勾选集合 + 去重用。"""
        return f"{self.type.value}:{self.row_id or self.sync_id or self.file_path or ''}"


class ScanReport(BaseModel):
    """一次扫描的全部结果。"""

    db_orphans: list[OrphanRecord] = Field(default_factory=list)  # A 类
    file_orphans: list[OrphanRecord] = Field(default_factory=list)  # B 类
    sync_orphans: list[OrphanRecord] = Field(default_factory=list)  # C 类(A5 归这里)

    @property
    def total_count(self) -> int:
        return len(self.db_orphans) + len(self.file_orphans) + len(self.sync_orphans)

    @property
    def total_size_bytes(self) -> int:
        return sum(r.size_bytes or 0 for r in self.file_orphans)


class CleanFailure(BaseModel):
    record_key: str
    error: str


class CleanResult(BaseModel):
    success_count: int
    failures: list[CleanFailure] = Field(default_factory=list)

    @property
    def has_failure(self) -> bool:
        return bool(self.failures)
