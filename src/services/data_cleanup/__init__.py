"""Server 端孤儿数据扫描 / 清理服务(替代旧 IntegrityScan)。

提供"预览 → 勾选 → 删除"完整闭环,跟 mobile 端 OrphanScanner / OrphanCleaner
设计对齐。所有操作 admin scope,跨所有用户。

公开 API:
- [scan_all][src.services.data_cleanup.scanner.scan_all]
- [clean][src.services.data_cleanup.cleaner.clean]
- [OrphanType][src.services.data_cleanup.models.OrphanType]
- [OrphanRecord][src.services.data_cleanup.models.OrphanRecord]
- [ScanReport][src.services.data_cleanup.models.ScanReport]
- [CleanResult][src.services.data_cleanup.models.CleanResult]
"""

from .cleaner import clean
from .models import (
    CleanResult,
    CleanFailure,
    OrphanRecord,
    OrphanType,
    ScanReport,
)
from .scanner import scan_all

__all__ = [
    "clean",
    "scan_all",
    "CleanFailure",
    "CleanResult",
    "OrphanRecord",
    "OrphanType",
    "ScanReport",
]
