from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import numpy as np

from src.services.ai.docs_index import get_docs_index, reset_docs_index_cache
from src.services.ai.docs_refresh import DocsRefreshService


def _write_index(path: Path, *, content: str, build_time: str) -> bytes:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY, content TEXT NOT NULL, doc_path TEXT NOT NULL,
                doc_title TEXT, section TEXT, url TEXT, vector BLOB NOT NULL
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        conn.execute(
            "INSERT INTO chunks VALUES (1, ?, 'record/attachment.md', '附件', '删除附件', 'https://example.test', ?)",
            (content, np.asarray([1.0, 0.0], dtype=np.float32).tobytes()),
        )
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [("embedding_model", "test-model"), ("dim", "2"), ("build_time", build_time)],
        )
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def test_refresh_swaps_both_valid_language_indexes(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _write_index(
        bundled / "docs-index.zh.sqlite", content="old zh", build_time="2026-01-01T00:00:00Z"
    )
    _write_index(
        bundled / "docs-index.en.sqlite", content="old en", build_time="2026-01-01T00:00:00Z"
    )
    cache = tmp_path / "cache"
    remote = tmp_path / "remote"
    remote.mkdir()
    payloads = {
        "docs-index.hash": b"new-corpus-hash\n",
        "docs-index.zh.sqlite": _write_index(
            remote / "zh.sqlite", content="new zh", build_time="2026-09-03T12:00:00Z"
        ),
        "docs-index.en.sqlite": _write_index(
            remote / "en.sqlite", content="new en", build_time="2026-09-03T12:00:00Z"
        ),
    }

    async def fetch(name: str) -> bytes:
        return payloads[name]

    reset_docs_index_cache()
    service = DocsRefreshService(
        cache_dir=cache,
        bundled_dir=bundled,
        embedding_model="test-model",
        fetch=fetch,
    )
    status = asyncio.run(service.refresh())

    assert status.corpus_hash == "new-corpus-hash"
    assert status.languages["zh"].build_time == "2026-09-03T12:00:00Z"
    assert get_docs_index("zh").chunks[0].content == "new zh"
    assert get_docs_index("en").chunks[0].content == "new en"


def test_refresh_preserves_active_indexes_when_one_download_is_invalid(tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _write_index(
        bundled / "docs-index.zh.sqlite", content="old zh", build_time="2026-01-01T00:00:00Z"
    )
    _write_index(
        bundled / "docs-index.en.sqlite", content="old en", build_time="2026-01-01T00:00:00Z"
    )

    async def fetch(name: str) -> bytes:
        return {
            "docs-index.hash": b"bad-corpus-hash\n",
            "docs-index.zh.sqlite": b"not sqlite",
            "docs-index.en.sqlite": b"not sqlite",
        }[name]

    reset_docs_index_cache()
    service = DocsRefreshService(
        cache_dir=tmp_path / "cache",
        bundled_dir=bundled,
        embedding_model="test-model",
        fetch=fetch,
    )
    before = get_docs_index("zh").chunks[0].content
    status = asyncio.run(service.refresh())

    assert status.last_error
    assert get_docs_index("zh").chunks[0].content == before


def test_check_latest_reports_remote_hash_without_downloading_indexes(tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    _write_index(
        bundled / "docs-index.zh.sqlite", content="old zh", build_time="2026-01-01T00:00:00Z"
    )
    _write_index(
        bundled / "docs-index.en.sqlite", content="old en", build_time="2026-01-01T00:00:00Z"
    )
    fetched: list[str] = []

    async def fetch(name: str) -> bytes:
        fetched.append(name)
        return {"docs-index.hash": b"published-corpus-hash\n"}[name]

    reset_docs_index_cache()
    service = DocsRefreshService(
        cache_dir=tmp_path / "cache",
        bundled_dir=bundled,
        embedding_model="test-model",
        fetch=fetch,
    )
    status = asyncio.run(service.check_latest())

    assert fetched == ["docs-index.hash"]
    assert status.remote_corpus_hash == "published-corpus-hash"
    assert status.is_latest is False
    assert get_docs_index("zh").chunks[0].content == "old zh"
