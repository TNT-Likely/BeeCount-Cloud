"""#93：MCP 写入时区、Web 读回和手机增量同步的回归测试。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import _mcp_internal_client
from src.config import Settings, get_settings
from src.database import Base, get_db
from src.deps import require_any_scopes
from src.main import app
from src.mcp.datetime_utils import parse_transaction_datetime
from src.mcp.tools import read_tools, write_tools
from src.models import ReadTxProjection, User, UserTagProjection
from src.routers.write import _shared as write_shared
from src.security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE


@pytest.fixture(autouse=True)
def explicit_cloud_timezone(monkeypatch):
    monkeypatch.setattr(get_settings(), "cloud_timezone", "Asia/Shanghai")
    monkeypatch.setattr(get_settings(), "scheduler_timezone", "")


@pytest.mark.parametrize("value,expected", [
    ("2026-05-23T21:28:52", "2026-05-23T13:28:52+00:00"),
    ("2026-05-23 21:28:52", "2026-05-23T13:28:52+00:00"),
    ("2026-05-23", "2026-05-22T16:00:00+00:00"),
    ("2026-05-23T21:28:52+08:00", "2026-05-23T13:28:52+00:00"),
    ("2026-05-23T13:28:52Z", "2026-05-23T13:28:52+00:00"),
    ("2026-05-23T21:28:52-04:00", "2026-05-24T01:28:52+00:00"),
])
def test_cloud_local_times_and_explicit_offsets(value, expected):
    assert parse_transaction_datetime(value).isoformat() == expected


def test_cloud_timezone_has_priority_over_agent_fallback():
    result = parse_transaction_datetime("2026-05-23T21:28:52", time_zone="UTC")
    assert result.hour == 13


def test_explicit_scheduler_timezone_has_priority(monkeypatch):
    monkeypatch.setattr(get_settings(), "scheduler_timezone", "Asia/Tokyo")
    result = parse_transaction_datetime("2026-05-23T21:28:52")
    assert result.hour == 12


def test_env_file_tz_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TZ=America/New_York\n")
    assert Settings(_env_file=env_file).cloud_timezone == "America/New_York"


@pytest.mark.parametrize("date,hour", [("2026-01-15T12:00:00", 17), ("2026-07-15T12:00:00", 16)])
def test_caller_timezone_when_cloud_has_none(monkeypatch, date, hour):
    monkeypatch.setattr(get_settings(), "cloud_timezone", "")
    assert parse_transaction_datetime(date, time_zone="America/New_York").hour == hour


def test_unconfigured_cloud_does_not_guess_utc(monkeypatch):
    monkeypatch.setattr(get_settings(), "cloud_timezone", "")
    with pytest.raises(ValueError, match="Timezone required"):
        parse_transaction_datetime("2026-05-23T21:28:52")


def test_invalid_cloud_timezone_is_not_silently_ignored(monkeypatch):
    monkeypatch.setattr(get_settings(), "cloud_timezone", "Invalid/Zone")
    with pytest.raises(ValueError, match="Invalid IANA"):
        parse_transaction_datetime("2026-05-23T21:28:52", time_zone="UTC")
    # 明确给出的时间不依赖 Cloud 的默认时区。
    assert parse_transaction_datetime("2026-05-23T13:28:52Z").hour == 13


@pytest.mark.parametrize("date,reason", [
    ("2026-03-08T02:30:00", "nonexistent"),
    ("2026-11-01T01:30:00", "ambiguous"),
])
def test_dst_gaps_and_overlaps_require_explicit_offset(monkeypatch, date, reason):
    monkeypatch.setattr(get_settings(), "cloud_timezone", "America/New_York")
    with pytest.raises(ValueError, match=reason):
        parse_transaction_datetime(date)
    assert parse_transaction_datetime("2026-11-01T01:30:00-05:00").hour == 6


@pytest.fixture
def ledger(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    # 全局测试默认禁用 App 写入；此处恢复部署默认的双 scope 校验，保留 JWT 校验。
    app.dependency_overrides[write_shared._WRITE_SCOPE_DEP] = require_any_scopes(SCOPE_WEB_WRITE, SCOPE_APP_WRITE)
    monkeypatch.setattr(write_tools, "SessionLocal", sessions)
    monkeypatch.setattr(read_tools, "SessionLocal", sessions)
    client = TestClient(app)
    response = client.post("/api/v1/auth/register", json={
        "email": "timezone@example.com", "password": "local-test-only",
        "client_type": "web", "device_name": "timezone-test", "platform": "web",
    })
    assert response.status_code == 200, response.text
    auth = response.json()
    headers = {"Authorization": f"Bearer {auth['access_token']}", "X-Device-ID": auth["device_id"]}
    response = client.post("/api/v1/write/ledgers", json={"ledger_name": "Timezone", "currency": "CNY"}, headers=headers)
    assert response.status_code == 200, response.text
    ledger_id = response.json()["entity_id"]
    with sessions() as db:
        user = db.scalar(select(User).where(User.email == "timezone@example.com"))
        db.expunge(user)
    try:
        yield user, ledger_id, client, headers, sessions
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def run_tool(coro):
    async def invoke():
        try:
            return await coro
        finally:
            await _mcp_internal_client.close_internal_client()
    return asyncio.run(invoke())


@pytest.mark.parametrize("bulk", [False, True])
def test_mcp_write_web_read_and_mobile_sync_keep_source_wall_time(ledger, bulk):
    user, ledger_id, client, headers, sessions = ledger
    if bulk:
        result = run_tool(write_tools.create_transactions(user, ledger_id=ledger_id, transactions=[
            {"amount": 18, "happened_at": "2026-05-23T21:28:52"},
            {"amount": 20, "happened_at": "2026-05-23T13:28:52Z"},
        ]))
        assert result["created_count"] == 2
    else:
        run_tool(write_tools.create_transaction(user, ledger_id=ledger_id, amount=18, happened_at="2026-05-23T21:28:52"))
    rows = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=headers).json()
    assert len(rows) == (2 if bulk else 1)
    for row in rows:
        happened = datetime.fromisoformat(row["happened_at"].replace("Z", "+00:00"))
        assert happened.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%H:%M:%S") == "21:28:52"
    mcp_rows = read_tools.list_transactions(user, ledger_id=ledger_id)["items"]
    assert all(row["happened_at"].endswith("+00:00") for row in mcp_rows)
    changes = client.get("/api/v1/sync/pull?since=0&limit=500", headers=headers).json()["changes"]
    tx_changes = [c for c in changes if c["entity_type"] == "transaction"]
    assert len(tx_changes) == len(rows)
    for change in tx_changes:
        happened = datetime.fromisoformat(change["payload"]["happenedAt"].replace("Z", "+00:00"))
        assert happened.astimezone(ZoneInfo("Asia/Shanghai")).hour == 21


def test_update_uses_cloud_timezone(ledger):
    user, ledger_id, client, headers, sessions = ledger
    result = run_tool(write_tools.create_transaction(user, ledger_id=ledger_id, amount=18, happened_at="2026-05-23T13:28:52Z"))
    run_tool(write_tools.update_transaction(user, sync_id=result["sync_id"], happened_at="2026-05-24T09:00:00"))
    rows = client.get(f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=headers).json()
    assert datetime.fromisoformat(rows[0]["happened_at"].replace("Z", "+00:00")) == datetime(2026, 5, 24, 1, tzinfo=timezone.utc)


def test_invalid_batch_has_no_partial_transactions_or_mcp_tag(ledger):
    user, ledger_id, client, headers, sessions = ledger
    with pytest.raises(ValueError, match=r"transactions\[1\]"):
        run_tool(write_tools.create_transactions(user, ledger_id=ledger_id, transactions=[
            {"amount": 18, "happened_at": "2026-05-23T21:28:52"},
            {"amount": 20, "happened_at": "bad-date"},
        ]))
    with sessions() as db:
        assert db.scalar(select(ReadTxProjection)) is None
        assert db.scalar(select(UserTagProjection).where(UserTagProjection.name == "MCP")) is None


def test_invalid_single_has_no_mcp_tag_side_effect(ledger):
    user, ledger_id, client, headers, sessions = ledger
    with pytest.raises(ValueError, match="ISO"):
        run_tool(write_tools.create_transaction(user, ledger_id=ledger_id, amount=18, happened_at="bad-date"))
    with sessions() as db:
        assert db.scalar(select(UserTagProjection).where(UserTagProjection.name == "MCP")) is None


def test_web_config_public_and_operator_controlled(monkeypatch):
    with TestClient(app) as client:
        for enabled in [False, True]:
            monkeypatch.setattr(get_settings(), "project_partnerships_enabled", enabled)
            response = client.get("/api/v1/web-config")
            assert response.status_code == 200
            assert response.json() == {"project_partnerships_enabled": enabled}
