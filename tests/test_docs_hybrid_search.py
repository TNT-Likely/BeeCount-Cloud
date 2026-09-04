from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np

from src.services.ai.docs_index import DocsIndex


def _write_hybrid_index(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                doc_path TEXT NOT NULL,
                doc_title TEXT,
                section TEXT,
                url TEXT,
                vector BLOB NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(
                content, doc_title, section, tokenize='trigram'
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        rows = [
            (
                1,
                "# 交易记录\\n\\n编辑一笔交易的通用说明。",
                "record/edit.md",
                "交易记录",
                "编辑交易",
                [1.0, 0.0],
            ),
            (
                2,
                "# 交易附件\\n\\n长按要删除的附件图片并确认删除。",
                "record/attachment.md",
                "交易附件",
                "删除附件",
                [0.0, 1.0],
            ),
        ]
        for chunk_id, content, doc_path, title, section, vector in rows:
            conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    content,
                    doc_path,
                    title,
                    section,
                    f"https://example.test/{chunk_id}",
                    np.asarray(vector, dtype=np.float32).tobytes(),
                ),
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, content, doc_title, section) VALUES (?, ?, ?, ?)",
                (chunk_id, content, title, section),
            )
        conn.execute("INSERT INTO meta VALUES ('dim', '2')")
        conn.commit()
    finally:
        conn.close()


def test_hybrid_search_promotes_exact_section_keyword_over_vector_only_match(tmp_path):
    path = tmp_path / "docs-index.zh.sqlite"
    _write_hybrid_index(path)
    index = DocsIndex(lang="zh", sqlite_path=path)

    result = index.hybrid_search(
        query="删除附件",
        query_vector=[1.0, 0.0],
        k=1,
        vector_k=2,
        keyword_k=2,
    )

    assert [item.chunk.id for item in result] == [2]


def test_hybrid_search_matches_keyword_inside_a_natural_language_question(tmp_path):
    path = tmp_path / "docs-index.zh.sqlite"
    _write_hybrid_index(path)
    index = DocsIndex(lang="zh", sqlite_path=path)

    result = index.hybrid_search(
        query="如何删除附件",
        query_vector=[1.0, 0.0],
        k=1,
        vector_k=2,
        keyword_k=2,
    )

    assert [item.chunk.id for item in result] == [2]


def test_hybrid_search_joins_chinese_keywords_split_by_punctuation(tmp_path):
    path = tmp_path / "docs-index.zh.sqlite"
    _write_hybrid_index(path)
    index = DocsIndex(lang="zh", sqlite_path=path)

    result = index.hybrid_search(
        query="如何删除，附件？",
        query_vector=[1.0, 0.0],
        k=1,
        vector_k=2,
        keyword_k=2,
    )

    assert [item.chunk.id for item in result] == [2]
