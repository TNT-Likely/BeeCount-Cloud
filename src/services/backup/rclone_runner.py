"""rclone subprocess 包装 —— `rclone copy` / `rclone copyto` / `rclone lsjson`
/ `rclone purge` 等命令的 Python 调用 + 进度解析。

进度通过 `--use-json-log --stats=2s --stats-log-level INFO` 拿到 stderr 行
JSON,每行包含 `stats.transferredBytes` / `stats.totalBytes` / `stats.speed`
等字段。我们把它解析成 RcloneProgress dataclass,callback 推给上层(WS)。
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


logger = logging.getLogger(__name__)


class RcloneError(RuntimeError):
    """rclone 子进程返回非零或被 kill。"""


@dataclass
class RcloneProgress:
    """单次进度事件。"""

    bytes_transferred: int = 0
    bytes_total: int = 0
    speed: float = 0.0  # bytes/sec
    eta_seconds: int | None = None
    files_transferred: int = 0
    files_total: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def percent(self) -> float:
        if self.bytes_total <= 0:
            return 0.0
        return min(100.0, self.bytes_transferred / self.bytes_total * 100)


def _parse_log_line(line: str) -> RcloneProgress | None:
    """rclone --use-json-log 的 stderr 一行,如果含 stats 就解析。"""
    line = line.strip()
    if not line:
        return None
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None
    stats = ev.get("stats")
    if not isinstance(stats, dict):
        return None
    return RcloneProgress(
        bytes_transferred=int(stats.get("bytes") or 0),
        bytes_total=int(stats.get("totalBytes") or 0),
        speed=float(stats.get("speed") or 0.0),
        eta_seconds=int(stats["eta"]) if isinstance(stats.get("eta"), (int, float)) else None,
        files_transferred=int(stats.get("transfers") or 0),
        files_total=int(stats.get("totalTransfers") or 0),
        raw=stats,
    )


class RcloneRunner:
    """单条 rclone 命令的 subprocess 调用 + 进度。

    用法:
        runner = RcloneRunner(binary="rclone", config_path="/data/rclone.conf")
        runner.run(["copyto", "/tmp/x.tar.gz", "remote:x.tar.gz"],
                   on_progress=lambda p: print(p.percent))
    """

    def __init__(
        self,
        *,
        binary: str = "rclone",
        config_path: str | Path,
        log_capture_max_bytes: int = 1024 * 1024,
    ):
        self.binary = binary
        self.config_path = str(config_path)
        self.log_capture_max_bytes = log_capture_max_bytes

    def run(
        self,
        args: list[str],
        *,
        on_progress: Callable[[RcloneProgress], None] | None = None,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """跑一条 rclone 命令。返回 (exit_code, captured_log)。"""
        cmd = [
            self.binary,
            "--config",
            self.config_path,
            "--use-json-log",
            "--stats=2s",
            "--stats-log-level",
            "INFO",
            *args,
        ]
        logger.info("rclone exec: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env={**(extra_env or {})} if extra_env else None,
        )

        captured: list[str] = []
        captured_size = 0

        def _consume(stream, is_stderr: bool) -> None:
            nonlocal captured_size
            assert stream is not None
            for line in iter(stream.readline, ""):
                # 抓 stderr 上的进度 / log
                if is_stderr:
                    progress = _parse_log_line(line)
                    if progress is not None and on_progress is not None:
                        try:
                            on_progress(progress)
                        except Exception:
                            logger.exception("on_progress callback raised")
                # log 截断到 max bytes
                if captured_size < self.log_capture_max_bytes:
                    chunk = line[: max(0, self.log_capture_max_bytes - captured_size)]
                    captured.append(chunk)
                    captured_size += len(chunk)
            stream.close()

        t_out = threading.Thread(target=_consume, args=(proc.stdout, False), daemon=True)
        t_err = threading.Thread(target=_consume, args=(proc.stderr, True), daemon=True)
        t_out.start()
        t_err.start()

        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            t_out.join(timeout=2)
            t_err.join(timeout=2)
            raise RcloneError("rclone timed out")
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        return rc, "".join(captured)

    # ---------------------------------------------------------- convenience

    def copyto(
        self,
        local_path: str,
        remote_path: str,
        *,
        on_progress: Callable[[RcloneProgress], None] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        return self.run(
            ["copyto", local_path, remote_path],
            on_progress=on_progress,
            timeout=timeout,
        )

    def copy(
        self,
        src: str,
        dst: str,
        *,
        on_progress: Callable[[RcloneProgress], None] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        return self.run(
            ["copy", src, dst],
            on_progress=on_progress,
            timeout=timeout,
        )

    def lsjson(self, remote: str, *, max_depth: int = 1) -> list[dict]:
        rc, out = self.run(
            ["lsjson", remote, "--max-depth", str(max_depth)],
            timeout=60,
        )
        if rc != 0:
            raise RcloneError(f"rclone lsjson failed: {out[-500:]}")
        # 注:lsjson 的 stdout 才是 JSON 数组,但我们 captured 把 stderr 也合并了。
        # 重新跑一次只读 stdout。
        result = subprocess.run(
            [
                self.binary,
                "--config",
                self.config_path,
                "lsjson",
                remote,
                "--max-depth",
                str(max_depth),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RcloneError(f"rclone lsjson failed: {result.stderr[-500:]}")
        try:
            return json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RcloneError(f"rclone lsjson returned invalid JSON: {exc}") from exc

    def deletefile(self, remote_path: str) -> tuple[int, str]:
        return self.run(["deletefile", remote_path], timeout=60)
