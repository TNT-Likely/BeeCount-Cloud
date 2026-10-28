"""退款日期时间:validate_refund 与 upsert_refund 必须保留时分秒。

对应「退款日期加上时间,默认当前时间」需求 —— 之前服务端只取日期部分,
把时间清零;现在要保留(退款精确到分钟)。
"""

from __future__ import annotations

from src.entity_extensions import parse_datetime_value, validate_refund


def test_parse_datetime_value_keeps_time():
    dt = parse_datetime_value("2026-10-26T11:53:00+00:00")
    assert dt is not None
    assert (dt.hour, dt.minute) == (11, 53)


def test_parse_datetime_value_date_only_gets_zero_time():
    dt = parse_datetime_value("2026-10-26")
    assert dt is not None
    assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)


def test_parse_datetime_value_none():
    assert parse_datetime_value(None) is None


def test_validate_refund_preserves_time():
    payload = {
        "originalTxId": "tx-1",
        "amount": 100.0,
        "happenedAt": "2026-10-26T11:53:42+00:00",
        "status": "received",
    }
    out = validate_refund(payload, creating=True)
    # 时间必须原样保留(不再被抹成 00:00)。
    assert "11:53" in out["happenedAt"]
