"""Runtime refresh for the BeeCount-Website RAG index pair."""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ...config import get_settings
from .docs_index import (
    DocsIndex,
    configure_docs_index_dirs,
    get_docs_index,
    replace_docs_indexes,
)

logger = logging.getLogger(__name__)
_INDEX_FILES = ("docs-index.zh.sqlite", "docs-index.en.sqlite")


@dataclass(frozen=True)
class DocsLanguageStatus:
    build_time: str | None
    chunk_count: int
    dim: int


@dataclass(frozen=True)
class DocsIndexStatus:
    source: str
    corpus_hash: str | None
    embedding_model: str | None
    languages: dict[str, DocsLanguageStatus]
    last_checked_at: str | None = None
    last_updated_at: str | None = None
    last_error: str | None = None
    remote_corpus_hash: str | None = None
    is_latest: bool | None = None

    def as_dict(self) -> dict:
        return asdict(self)


Fetch = Callable[[str], Awaitable[bytes]]


class DocsRefreshService:
    """Fetch, validate and atomically publish a complete language index pair."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        bundled_dir: Path,
        embedding_model: str,
        source_url: str = "",
        timeout: float = 15.0,
        fetch: Fetch | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.bundled_dir = bundled_dir
        self.embedding_model = embedding_model
        self.source_url = source_url.rstrip("/")
        self.timeout = timeout
        self._fetch_override = fetch
        # A cache can outlive a crashed download or a manual filesystem edit;
        # never let an unreadable cache prevent the packaged fallback from booting.
        configure_docs_index_dirs(cache_dir=None, bundled_dir=bundled_dir)
        source = "bundled-image"
        corpus_hash: str | None = None
        active_indexes: dict[str, DocsIndex] | None = None
        if self._has_complete_cache():
            try:
                active_indexes = self._load_indexes(self.cache_dir)
                configure_docs_index_dirs(cache_dir=cache_dir, bundled_dir=bundled_dir)
                replace_docs_indexes(active_indexes)
                source = "runtime-cache"
                corpus_hash = self._read_cached_hash()
            except Exception as exc:  # noqa: BLE001 - bundled data is the recovery path
                logger.warning("ignoring invalid persisted RAG cache: %s", exc)
        self._status = self._status_for_indexes(
            active_indexes or {lang: get_docs_index(lang) for lang in ("zh", "en")},
            corpus_hash=corpus_hash,
            source=source,
        )

    def status(self) -> DocsIndexStatus:
        return self._status

    async def refresh(self) -> DocsIndexStatus:
        checked_at = _now()
        remote_hash: str | None = None
        try:
            remote_hash = await self._fetch_remote_hash()
            if remote_hash == self._status.corpus_hash:
                self._status = _with_check(
                    self._status,
                    checked_at=checked_at,
                    remote_hash=remote_hash,
                    is_latest=True,
                    error=None,
                )
                return self._status

            indexes = await self._download_and_validate()
            self._persist(indexes, remote_hash)
            replace_docs_indexes(indexes)
            self._status = self._status_for_indexes(
                indexes,
                corpus_hash=remote_hash,
                source="runtime-cache",
                checked_at=checked_at,
                updated_at=checked_at,
                remote_corpus_hash=remote_hash,
                is_latest=True,
            )
        except Exception as exc:  # noqa: BLE001 - refresh must not break active RAG
            logger.warning("rag docs refresh failed: %s", exc)
            self._status = _with_check(
                self._status,
                checked_at=checked_at,
                remote_hash=remote_hash,
                is_latest=False if remote_hash else None,
                error=str(exc)[:300],
            )
        return self._status

    async def check_latest(self) -> DocsIndexStatus:
        """Check the small remote hash file without downloading either index."""
        checked_at = _now()
        try:
            remote_hash = await self._fetch_remote_hash()
            self._status = _with_check(
                self._status,
                checked_at=checked_at,
                remote_hash=remote_hash,
                is_latest=bool(
                    self._status.corpus_hash and remote_hash == self._status.corpus_hash
                ),
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - a status check must not affect active RAG
            logger.warning("rag docs latest-version check failed: %s", exc)
            self._status = _with_check(
                self._status,
                checked_at=checked_at,
                remote_hash=None,
                is_latest=None,
                error=str(exc)[:300],
            )
        return self._status

    async def _fetch_remote_hash(self) -> str:
        remote_hash = (await self._fetch("docs-index.hash")).decode("utf-8").strip()
        if not remote_hash:
            raise ValueError("remote docs-index.hash is empty")
        return remote_hash

    async def _download_and_validate(self) -> dict[str, DocsIndex]:
        self.cache_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.cache_dir.parent, prefix="rag-index-") as raw_tmp:
            tmp = Path(raw_tmp)
            for filename in _INDEX_FILES:
                target = tmp / filename
                target.write_bytes(await self._fetch(filename))
            indexes = self._load_indexes(tmp)
            # Keep temp files after context only long enough to read them again in _persist.
            self._candidate_bytes = {name: (tmp / name).read_bytes() for name in _INDEX_FILES}
            return indexes

    def _load_indexes(self, directory: Path) -> dict[str, DocsIndex]:
        indexes: dict[str, DocsIndex] = {}
        for filename in _INDEX_FILES:
            lang = "zh" if ".zh." in filename else "en"
            index = DocsIndex(lang=lang, sqlite_path=directory / filename)
            if index.is_empty:
                raise ValueError(f"{filename} has no chunks")
            if index.embedding_model != self.embedding_model:
                raise ValueError(
                    f"{filename} model={index.embedding_model!r} "
                    f"does not match runtime model={self.embedding_model!r}"
                )
            if index.dim <= 0:
                raise ValueError(f"{filename} has invalid vector dimension")
            indexes[lang] = index
        return indexes

    def _persist(self, indexes: dict[str, DocsIndex], corpus_hash: str) -> None:
        del indexes  # Validation completed before any persistent file is replaced.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for filename, payload in self._candidate_bytes.items():
            tmp = self.cache_dir / f".{filename}.tmp"
            tmp.write_bytes(payload)
            os.replace(tmp, self.cache_dir / filename)
        hash_tmp = self.cache_dir / ".docs-index.hash.tmp"
        hash_tmp.write_text(corpus_hash + "\n", encoding="utf-8")
        os.replace(hash_tmp, self.cache_dir / "docs-index.hash")

    async def _fetch(self, filename: str) -> bytes:
        if self._fetch_override is not None:
            return await self._fetch_override(filename)
        if not self.source_url:
            raise RuntimeError("RAG_INDEX_SOURCE_URL is empty")
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            response = await client.get(f"{self.source_url}/{filename}")
            response.raise_for_status()
            return response.content

    def _has_complete_cache(self) -> bool:
        return all((self.cache_dir / name).exists() for name in (*_INDEX_FILES, "docs-index.hash"))

    def _read_cached_hash(self) -> str | None:
        path = self.cache_dir / "docs-index.hash"
        return path.read_text(encoding="utf-8").strip() if path.exists() else None

    def _current_status(self, *, corpus_hash: str | None, source: str) -> DocsIndexStatus:
        return self._status_for_indexes(
            {lang: get_docs_index(lang) for lang in ("zh", "en")},
            corpus_hash=corpus_hash,
            source=source,
        )

    def _status_for_indexes(
        self,
        indexes: dict[str, DocsIndex],
        *,
        corpus_hash: str | None,
        source: str,
        checked_at: str | None = None,
        updated_at: str | None = None,
        remote_corpus_hash: str | None = None,
        is_latest: bool | None = None,
    ) -> DocsIndexStatus:
        return DocsIndexStatus(
            source=source,
            corpus_hash=corpus_hash,
            embedding_model=self.embedding_model,
            languages={
                lang: DocsLanguageStatus(index.build_time, len(index.chunks), index.dim)
                for lang, index in indexes.items()
            },
            last_checked_at=checked_at,
            last_updated_at=updated_at,
            remote_corpus_hash=remote_corpus_hash,
            is_latest=is_latest,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _with_check(
    status: DocsIndexStatus,
    *,
    checked_at: str,
    remote_hash: str | None,
    is_latest: bool | None,
    error: str | None,
) -> DocsIndexStatus:
    return DocsIndexStatus(
        source=status.source,
        corpus_hash=status.corpus_hash,
        embedding_model=status.embedding_model,
        languages=status.languages,
        last_checked_at=checked_at,
        last_updated_at=status.last_updated_at,
        last_error=error,
        remote_corpus_hash=remote_hash,
        is_latest=is_latest,
    )


_service: DocsRefreshService | None = None


def get_docs_refresh_service() -> DocsRefreshService:
    global _service
    if _service is None:
        settings = get_settings()
        bundled_dir = Path(__file__).resolve().parents[3] / "data"
        _service = DocsRefreshService(
            cache_dir=Path(settings.rag_index_cache_dir),
            bundled_dir=bundled_dir,
            embedding_model=settings.embedding_model,
            source_url=settings.rag_index_source_url,
            timeout=settings.rag_index_refresh_timeout,
        )
    return _service


def reset_docs_refresh_service() -> None:
    global _service
    _service = None
