"""把 staging 目录打包成 tar.gz / encrypted-zip(本地落盘),供 rclone
copyto 推到远端。

格式选择:
  - encrypted=False: tar.gz(标准 unix 打包)
  - encrypted=True : AES-256 password-protected zip(WinZip AES,macOS Archive
    Utility / Keka / 7-Zip / The Unarchiver / WinRAR 都原生支持自动弹出
    密码框),用 pyzipper 库实现。用户从 R2 下载 .zip 双击 → 输密码 → 解压 → 文件
    直接铺平,无中间 tar 层。

第一版选 "本地落文件" 而不是 "stream 进 rclone rcat" 的原因:
  - 同一份打包要 fan-out 到 N 个 remote(stream 单消费者)
  - 简单可观测 + 出错可回看

local copy of attachments 走 hardlink(`os.link`),零空间占用 —— attachments
是内容寻址 immutable 的,hardlink 拷贝跟原文件指向同一 inode。
"""
from __future__ import annotations

import logging
import os
import tarfile
from pathlib import Path

import pyzipper


logger = logging.getLogger(__name__)


def hardlink_tree(src: str | Path, dst: str | Path) -> int:
    """把 src 目录 hardlink 拷贝到 dst。返回处理的文件数。

    跨文件系统会失败 —— caller 自己判断,失败时回退到 shutil.copytree。
    """
    src_p = Path(src)
    dst_p = Path(dst)
    dst_p.mkdir(parents=True, exist_ok=True)
    n = 0
    for root, _dirs, files in os.walk(src_p):
        rel_root = Path(root).relative_to(src_p)
        target_root = dst_p / rel_root
        target_root.mkdir(parents=True, exist_ok=True)
        for f in files:
            src_file = Path(root) / f
            dst_file = target_root / f
            if dst_file.exists():
                continue
            os.link(src_file, dst_file)
            n += 1
    return n


def build_targz(src_dir: str | Path, output_path: str | Path) -> int:
    """把 src_dir 整棵打包成 output_path (tar.gz)。返回 output 大小。

    arcname 用 `.` 让解压时根目录就是 src_dir 内容,便于 rsync 替换。
    """
    src_p = Path(src_dir)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    if out_p.exists():
        out_p.unlink()
    with tarfile.open(out_p, "w:gz", compresslevel=6) as tf:
        # arcname='.' 让 tar 内的根是 ./db.sqlite3 ./attachments/...
        # 解压后没有多余的 timestamp 父目录。
        tf.add(src_p, arcname=".", recursive=True)
    size = out_p.stat().st_size
    logger.info("tar.gz built: %s (%d bytes)", out_p, size)
    return size


def build_encrypted_zip(
    src_dir: str | Path,
    output_path: str | Path,
    passphrase: str,
) -> int:
    """把 src_dir 整棵打包成 AES-256 加密 zip(WinZip AES extension)。

    用户期望:macOS Archive Utility / Keka / 7-Zip / The Unarchiver /
    WinRAR 这类常见工具能识别并弹出密码框。pyzipper 输出的 WZ_AES zip
    格式正是这套。passphrase 错时打开会报错(各家 UI 自己处理)。
    """
    if not passphrase:
        raise ValueError("passphrase is empty")
    src_p = Path(src_dir)
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    if out_p.exists():
        out_p.unlink()
    with pyzipper.AESZipFile(
        out_p,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as zf:
        zf.setpassword(passphrase.encode("utf-8"))
        for path in src_p.rglob("*"):
            if path.is_file():
                arcname = str(path.relative_to(src_p))
                zf.write(path, arcname=arcname)
    size = out_p.stat().st_size
    logger.info("encrypted zip built: %s (%d bytes)", out_p, size)
    return size


def extract_zip(
    zip_path: str | Path,
    dst_dir: str | Path,
    passphrase: str | None = None,
) -> None:
    """解 AES-256 加密(或不加密)zip 到 dst_dir。restore 流程用。"""
    zp = Path(zip_path)
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    with pyzipper.AESZipFile(zp) as zf:
        if passphrase:
            zf.setpassword(passphrase.encode("utf-8"))
        zf.extractall(dst)
