"""文档索引 — load sqlite + 内存 cosine 检索。

设计:
- 启动时 load 全部 chunks 进 numpy 矩阵(200-300 chunks * 1024 维 ~ 1MB,负担极小)
- query 时 cosine similarity 一次矩阵乘,top-K argpartition 取最相关 — 几毫秒
- 不上 sqlite-vss(native 依赖跨平台麻烦),数据量上来再换

索引文件 schema 见 BeeCount-Website/scripts/README.md。
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable

import numpy as np


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocChunk:
    """一段文档 chunk 的元信息(不含向量,向量在 matrix 里按 idx 索引)。"""

    id: int
    content: str
    doc_path: str
    doc_title: str
    section: str
    url: str


@dataclass(frozen=True)
class RetrievedChunk:
    """检索结果 — 带 similarity score。"""

    chunk: DocChunk
    score: float


class DocsIndex:
    """单语言索引(zh / en 各一个 instance)。"""

    def __init__(self, *, lang: str, sqlite_path: Path) -> None:
        self.lang = lang
        self.sqlite_path = sqlite_path
        self.chunks: list[DocChunk] = []
        # 行向量已 L2-normalize,query 时直接 dot product 就是 cosine similarity
        self.matrix: np.ndarray = np.empty((0, 0), dtype=np.float32)
        self.dim: int = 0
        self.embedding_model: str | None = None
        self._load()

    def _load(self) -> None:
        if not self.sqlite_path.exists():
            logger.warning(
                "docs index missing for lang=%s path=%s — AI ask will degrade",
                self.lang, self.sqlite_path,
            )
            return
        conn = sqlite3.connect(self.sqlite_path)
        try:
            # meta
            for key, value in conn.execute("SELECT key, value FROM meta"):
                if key == "embedding_model":
                    self.embedding_model = value
                elif key == "dim":
                    try:
                        self.dim = int(value)
                    except (ValueError, TypeError):
                        pass

            chunks: list[DocChunk] = []
            vectors: list[np.ndarray] = []
            for row in conn.execute(
                "SELECT id, content, doc_path, doc_title, section, url, vector FROM chunks ORDER BY id"
            ):
                cid, content, path, title, section, url, vec_bytes = row
                chunks.append(DocChunk(
                    id=int(cid),
                    content=content or "",
                    doc_path=path or "",
                    doc_title=title or "",
                    section=section or "",
                    url=url or "",
                ))
                vectors.append(np.frombuffer(vec_bytes, dtype=np.float32))
            self.chunks = chunks

            if vectors:
                m = np.vstack(vectors).astype(np.float32)
                # L2-normalize 行向量,后续 cosine 退化成 dot product
                norms = np.linalg.norm(m, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                self.matrix = m / norms
                if not self.dim:
                    self.dim = self.matrix.shape[1]
        finally:
            conn.close()

        logger.info(
            "docs index loaded lang=%s path=%s chunks=%d dim=%d model=%s",
            self.lang, self.sqlite_path, len(self.chunks), self.dim,
            self.embedding_model,
        )

        # 校验 build 时和 runtime 用的 embedding 模型一致 — 不一致检索会错乱
        # (向量空间不同,cosine 算出来无意义)
        try:
            from ...config import get_settings
            runtime_model = get_settings().embedding_model
            if (
                self.embedding_model
                and runtime_model
                and self.embedding_model != runtime_model
            ):
                logger.warning(
                    "embedding model mismatch: index built with %s but server EMBEDDING_MODEL=%s "
                    "→ retrieval results will be invalid; rebuild docs index or change settings",
                    self.embedding_model, runtime_model,
                )
        except Exception:  # noqa: BLE001  — 配置读取失败不阻塞 index 加载
            pass

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    def search(self, query_vector: Iterable[float], k: int = 4) -> list[RetrievedChunk]:
        """cosine similarity top-K。query_vector 不需要预先 normalize。"""
        if self.is_empty:
            return []
        q = np.asarray(list(query_vector), dtype=np.float32)
        n = float(np.linalg.norm(q))
        if n == 0:
            return []
        q = q / n
        # 维度兼容性检查 — 防 build / runtime 用了不同 embedding 模型
        if q.shape[0] != self.dim:
            logger.warning(
                "embedding dim mismatch: query=%d index=%d (lang=%s, model=%s)",
                q.shape[0], self.dim, self.lang, self.embedding_model,
            )
            return []
        sims = self.matrix @ q  # (N,)
        actual_k = min(k, len(self.chunks))
        # argpartition 比完全 sort 快;再对 top-K 内部排序保证 score 降序
        top_idx = np.argpartition(-sims, actual_k - 1)[:actual_k]
        top_idx = top_idx[np.argsort(-sims[top_idx])]
        return [
            RetrievedChunk(chunk=self.chunks[i], score=float(sims[i]))
            for i in top_idx
        ]


# 单例缓存 ─────────────────────────────────────────────────────────────────


_cache: dict[str, DocsIndex] = {}
_cache_lock = Lock()
_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def get_docs_index(lang: str) -> DocsIndex:
    """按 lang 取 / load 索引,first-call 时加载,后续从缓存返回。

    lang:'zh' / 'zh-CN' / 'zh-TW' → 都走 zh 索引(中文文档没分繁简)
          其它 / 'en' / 未识别 → 走 en
    """
    key = _normalize_lang(lang)
    with _cache_lock:
        idx = _cache.get(key)
        if idx is None:
            sqlite_path = _DATA_DIR / f"docs-index.{key}.sqlite"
            idx = DocsIndex(lang=key, sqlite_path=sqlite_path)
            _cache[key] = idx
        return idx


def reset_docs_index_cache() -> None:
    """测试用 — 清空缓存,下次 get_docs_index 会重新 load。"""
    with _cache_lock:
        _cache.clear()


def _normalize_lang(lang: str | None) -> str:
    if not lang:
        return "en"
    s = lang.strip().lower().replace("_", "-")
    if s.startswith("zh"):
        return "zh"
    return "en"
