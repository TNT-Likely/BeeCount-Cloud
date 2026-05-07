"""数据结构 —— 解析中间结果 / 字段映射 / 错误。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

SUPPORTED_FORMATS = ("beecount", "generic")
DEFAULT_DEDUP_STRATEGY = "skip_duplicates"

DedupStrategy = Literal["skip_duplicates", "insert_all"]
SourceFormat = Literal["beecount", "generic"]


@dataclass
class ParseWarning:
    """非致命解析警告 —— 字段缺失但能 fallback / 时间超出合理范围 / 等。"""

    code: str  # 'TIME_OUT_OF_RANGE' / 'MISSING_FIELD' / 'AMBIGUOUS_DATE' / ...
    row_number: int  # 1-based,跟 CSV 文件里的行号对应(含 header)
    message: str
    raw_line: str = ""


@dataclass
class ImportError:
    """致命解析 / 写入失败,带行号 + 原始内容方便排错。"""

    code: str  # 'PARSE_INVALID_FIELD' / 'PARSE_MISSING_REQUIRED' / 'WRITE_FAILED' / ...
    row_number: int
    message: str
    raw_line: str = ""
    field_name: str | None = None


@dataclass
class ParsedRow:
    """解析后的中间行 —— rows[i] = headers → cell value 的 dict。
    保留原始 row_number(基于 CSV 文件 1-based 行号)给错误定位用。
    """

    row_number: int
    cells: dict[str, str]
    raw_line: str


@dataclass
class ImportFieldMapping:
    """字段映射 —— 用户可在 preview 页编辑,然后 server 重新走 transformer。

    `tx_type` / `amount` / `happened_at` 必填。其它可选;空 = 该字段不导入。
    `tags` 支持多列合并(逗号 / 分号 / 顿号分隔)。

    Transformer 选项:
    - `datetime_format`:None = auto-try multiple formats;否则按 strptime 解析
    - `strip_currency_symbols`:剥 ¥ / $ / € / 等 + 千分位 ,
    - `expense_is_negative`:负数视为支出(且 abs(amount));对兼容 "金额" 列
      含正负号的 CSV 关键
    """

    # 必填
    tx_type: str | None = None
    amount: str | None = None
    happened_at: str | None = None
    # 可选 —— 跟 mobile 命名对齐:`category_name` = 一级分类(broad),
    # `subcategory_name` = 二级分类(specific leaf,可选)。transformer 里:
    # 有 sub 时 tx 记录 leaf=sub,parent=category;只有 category 时
    # tx 记录 leaf=category 无 parent。
    category_name: str | None = None
    subcategory_name: str | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    note: str | None = None
    tags: list[str] = field(default_factory=list)
    # transformer 选项
    datetime_format: str | None = None
    strip_currency_symbols: bool = True
    expense_is_negative: bool = False

    @property
    def required_complete(self) -> bool:
        """4 个必填字段都已映射 —— 跟 mobile 单笔录入一致(类型 / 金额 / 时间
        / 一级分类),用来判断默认 mapping 是否够用,前端徽章 / 后端 transformer
        共享。"""
        return bool(
            self.tx_type and self.amount and self.happened_at and self.category_name
        )


@dataclass
class ImportAccount:
    name: str
    type: str | None = None
    currency: str | None = None


@dataclass
class ImportCategory:
    name: str
    kind: Literal["expense", "income", "transfer"]
    parent_name: str | None = None
    level: int = 1


@dataclass
class ImportTag:
    name: str
    color: str | None = None


@dataclass
class ImportTransaction:
    tx_type: Literal["expense", "income", "transfer"]
    amount: Decimal
    happened_at: datetime
    note: str | None = None
    category_name: str | None = None
    parent_category_name: str | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    tag_names: list[str] = field(default_factory=list)
    # 原始 CSV 行号(从 header+1 开始),错误时定位用
    source_row_number: int = 0
    source_raw_line: str = ""


@dataclass
class ImportData:
    """server 内存 cache 里的完整解析结果。"""

    source_format: SourceFormat
    headers: list[str]
    rows: list[ParsedRow]  # 解析后的 row(已经移除 header / 描述行,只有数据)
    suggested_mapping: ImportFieldMapping
    parse_warnings: list[ParseWarning] = field(default_factory=list)
