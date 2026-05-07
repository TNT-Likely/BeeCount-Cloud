"""测试样本字节 —— 跟 mobile `ai_provider_factory.dart` 的
`_createTestImage` / `_createMinimalWav` 完全对齐。

`POST /api/v1/ai/test-provider` 用这两份字节调上游 LLM 验证 vision /
speech capability,跟 mobile 端测试行为一致(用户能"在 web 编辑 dialog
里跑 mobile 同款一键测试")。
"""
from __future__ import annotations

import base64
import struct

# 64×64 像素红色 JPEG 图片(GLM-4V 要求 ≥28×28)
# base64 字面量从 mobile lib/services/ai/ai_provider_factory.dart:313 复制,
# 改字节请同步两边,否则视觉测试结果可能在 mobile / web 不一致。
_TEST_JPEG_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkM"
    "EQ8SEhEPERETFhwXExQaFRERGCEYGh0dHx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4I"
    "CA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4e"
    "Hh4eHh7/wAARCABAAEADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQF"
    "BgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEI"
    "I0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNk"
    "ZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLD"
    "xMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEB"
    "AQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJB"
    "UQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZH"
    "SElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaan"
    "qKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oA"
    "DAMBAAIRAxEAPwDyyiiivzo/ssKKKKACiiigAooooAKKKKACiiigAooooAKKKKACiiig"
    "AooooAKKKKACiiigAooooAKKKKACiiigAooooA//2Q=="
)
TEST_JPEG_BYTES: bytes = base64.b64decode(_TEST_JPEG_BASE64)
TEST_JPEG_DATA_URL: str = f"data:image/jpeg;base64,{_TEST_JPEG_BASE64}"


def _build_minimal_wav() -> bytes:
    """1 秒 8 kHz 16-bit PCM 静音 WAV —— 44 byte header + 16000 byte zeros。
    跟 mobile `_createMinimalWav` 完全等价。
    """
    sample_rate = 8000
    num_samples = sample_rate  # 1 秒
    data_size = num_samples * 2  # 16-bit
    file_size = 36 + data_size

    parts: list[bytes] = []
    parts.append(b"RIFF")
    parts.append(struct.pack("<I", file_size))
    parts.append(b"WAVE")
    parts.append(b"fmt ")
    parts.append(struct.pack("<I", 16))  # chunk size
    parts.append(struct.pack("<H", 1))  # audio format = PCM
    parts.append(struct.pack("<H", 1))  # num channels
    parts.append(struct.pack("<I", sample_rate))
    parts.append(struct.pack("<I", sample_rate * 2))  # byte rate
    parts.append(struct.pack("<H", 2))  # block align
    parts.append(struct.pack("<H", 16))  # bits per sample
    parts.append(b"data")
    parts.append(struct.pack("<I", data_size))
    parts.append(b"\x00\x00" * num_samples)
    return b"".join(parts)


TEST_WAV_BYTES: bytes = _build_minimal_wav()
