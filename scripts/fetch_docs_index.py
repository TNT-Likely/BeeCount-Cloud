"""Fetch RAG docs index from BeeCount-Website.

本地开发用:从 sibling repo 或 GitHub clone 拉索引到 ./data/。

Docker build 时不用这个 — Dockerfile 的 docs-index-fetcher stage 已经处理。

用法:
    # 从 sibling repo(默认,~/code/.../BeeCount-Website)
    python scripts/fetch_docs_index.py

    # 从 GitHub clone(CI / fresh checkout 场景)
    python scripts/fetch_docs_index.py --remote

    # 自定义 sibling 路径
    python scripts/fetch_docs_index.py --source /path/to/BeeCount-Website

设计:.docs/web-cmdk-ai-doc-search.md
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_REMOTE = "https://github.com/TNT-Likely/BeeCount-Website.git"


def _copy_data(source_data_dir: Path) -> int:
    if not source_data_dir.exists():
        print(f"ERROR: {source_data_dir} not found — Website 端跑过 build_docs_index.py 了吗?", file=sys.stderr)
        return 2
    DATA_DIR.mkdir(exist_ok=True)
    copied = 0
    for fname in ("docs-index.zh.sqlite", "docs-index.en.sqlite", "docs-index.hash"):
        src = source_data_dir / fname
        if src.exists():
            shutil.copy2(src, DATA_DIR / fname)
            print(f"  ✓ {fname} ({src.stat().st_size / 1024:.1f} KB)")
            copied += 1
        else:
            print(f"  - {fname} missing in source")
    if copied == 0:
        print("ERROR: 没拉到任何索引文件,A1 文档 Q&A 不可用", file=sys.stderr)
        return 2
    print(f"\ndone; {copied} files in {DATA_DIR}")
    return 0


def from_local(source: Path) -> int:
    if not source.exists():
        print(f"ERROR: {source} 不存在;请确认 BeeCount-Website 已 clone", file=sys.stderr)
        return 2
    return _copy_data(source / "data")


def from_remote(repo_url: str, branch: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "website"
        print(f"cloning {repo_url} (branch={branch}) → {tmp_path}...")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(tmp_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"ERROR: git clone failed:\n{result.stderr}", file=sys.stderr)
            return 2
        return _copy_data(tmp_path / "data")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path,
        default=Path("../BeeCount-Website").resolve(),
        help="sibling BeeCount-Website 仓库路径(默认: ../BeeCount-Website)",
    )
    parser.add_argument(
        "--remote", action="store_true",
        help="改从 GitHub clone(忽略 --source)",
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REMOTE,
        help=f"远程 repo URL(默认: {DEFAULT_REMOTE})",
    )
    parser.add_argument(
        "--branch", default="main",
        help="远程 branch(默认: main)",
    )
    args = parser.parse_args()

    if args.remote:
        return from_remote(args.repo, args.branch)
    return from_local(args.source)


if __name__ == "__main__":
    sys.exit(main())
