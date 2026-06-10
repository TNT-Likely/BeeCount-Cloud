"""_analytics_range / _bucket_key 的 month_start_day 契约(纯函数,不起 app)。

口径:周期按起始月命名 —— "2026-06"(msd=10) = 本地 2026-06-10 ~ 2026-07-10。
年 = [当年1月周期起点, 次年1月周期起点)。默认 month_start_day=1 行为不变。

period=None 的默认标签分支(月/年)依赖 datetime.now 不可注入,暂无法纯函数覆盖;
若引入 freezegun/可注入 now 后应补:msd=10 时 6月5日的默认 month 标签应为 "2026-05"、
1月5日的默认 year 标签应为上一年。
"""

from datetime import datetime, timezone

from src.routers.read._shared import _analytics_range, _bucket_key


def test_month_range_respects_start_day_with_tz():
    start, end, period = _analytics_range(
        scope="month", period="2026-06", tz_offset_minutes=480, month_start_day=10
    )
    # 本地(UTC+8) 2026-06-10 00:00 → UTC 2026-06-09 16:00
    assert start == datetime(2026, 6, 9, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 9, 16, 0, tzinfo=timezone.utc)
    assert period == "2026-06"


def test_month_range_december_rollover():
    start, end, _ = _analytics_range(
        scope="month", period="2026-12", tz_offset_minutes=0, month_start_day=10
    )
    assert start == datetime(2026, 12, 10, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 10, tzinfo=timezone.utc)


def test_year_range_respects_start_day():
    start, end, _ = _analytics_range(
        scope="year", period="2026", tz_offset_minutes=0, month_start_day=10
    )
    assert start == datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 10, tzinfo=timezone.utc)


def test_default_keeps_natural_month():
    start, end, _ = _analytics_range(
        scope="month", period="2026-06", tz_offset_minutes=0
    )
    assert start == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_bucket_key_year_scope_uses_period_label():
    # 2027-01-05(本地=UTC) 属 2026-12 周期标签;2027-01-10 起才属 2027-01
    t1 = datetime(2027, 1, 5, 12, 0, tzinfo=timezone.utc)
    assert _bucket_key("year", t1, 0, 10) == "2026-12"
    t2 = datetime(2027, 1, 10, 12, 0, tzinfo=timezone.utc)
    assert _bucket_key("year", t2, 0, 10) == "2027-01"


def test_bucket_key_month_scope_unchanged():
    # month scope 是按"日"分桶,与起始日无关
    t = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    assert _bucket_key("month", t, 0, 10) == "2026-06-05"
