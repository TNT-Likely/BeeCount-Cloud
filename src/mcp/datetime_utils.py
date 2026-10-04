"""MCP 交易时间：显式偏移优先，裸时间使用 Cloud 配置，不猜测来源时区。"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import get_settings


def parse_transaction_datetime(value: str, *, time_zone: str | None = None) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("happened_at must be a non-empty ISO date or datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("happened_at must be an ISO date or datetime") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)

    settings = get_settings()
    zone_name = (
        settings.scheduler_timezone.strip()
        or settings.cloud_timezone.strip()
        or (time_zone or "").strip()
    )
    if not zone_name:
        raise ValueError(
            "Timezone required for happened_at without an offset. Use an ISO "
            "timestamp with its actual offset (e.g. +08:00), configure Cloud "
            "SCHEDULER_TIMEZONE/TZ, or pass time_zone (e.g. Asia/Shanghai) "
            "after confirming the source timezone. Do not append Z to local time."
        )
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid IANA time zone: {zone_name!r}") from exc

    # 夏令时回拨可能对应两个瞬间，跳时可能不存在；需要来源的显式偏移。
    candidates = set()
    for fold in (0, 1):
        utc = parsed.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == parsed:
            candidates.add(utc)
    if len(candidates) != 1:
        reason = "ambiguous" if candidates else "nonexistent"
        raise ValueError(
            f"happened_at is {reason} in {zone_name}; provide its actual ISO UTC offset"
        )
    return candidates.pop()
