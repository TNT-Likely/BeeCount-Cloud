"""账本数据导入服务 —— CSV 解析 + token cache + 字段映射 + stats 计算。

设计:.docs/web-ledger-import.md

- `schema`:ImportData / ImportTransaction / ImportFieldMapping 等数据结构
- `cache`:token → ImportData / mapping 内存 cache,30min TTL
- `parser`:main entry,sniff source format → 派发到具体 parser
- `parsers/`:BeeCount / 支付宝 / 微信 / generic 四种来源
- `transformer`:apply_mapping(rows + mapping → list[ImportTransaction])
- `stats`:对照 target ledger snapshot 计算 dedup / new vs match
"""
from __future__ import annotations

from .cache import (
    consume_token,
    get_token_data,
    save_token_data,
    cancel_token,
)
from .parser import (
    detect_source_format,
    parse_csv_text,
    parse_excel_bytes,
    suggest_mapping,
)
from .schema import (
    DEFAULT_DEDUP_STRATEGY,
    SUPPORTED_FORMATS,
    ImportAccount,
    ImportCategory,
    ImportData,
    ImportError,
    ImportFieldMapping,
    ImportTag,
    ImportTransaction,
    ParsedRow,
    ParseWarning,
)
from .stats import compute_stats
from .transformer import apply_mapping

__all__ = [
    "DEFAULT_DEDUP_STRATEGY",
    "SUPPORTED_FORMATS",
    "ImportAccount",
    "ImportCategory",
    "ImportData",
    "ImportError",
    "ImportFieldMapping",
    "ImportTag",
    "ImportTransaction",
    "ParseWarning",
    "ParsedRow",
    "apply_mapping",
    "cancel_token",
    "compute_stats",
    "consume_token",
    "detect_source_format",
    "get_token_data",
    "parse_csv_text",
    "parse_excel_bytes",
    "save_token_data",
    "suggest_mapping",
]
