"""ledgers.month_start_day(自定义每月起始日)同步契约 — push 侧:

- mobile push 的 ledger upsert payload 带 `monthStartDay` → 落 Ledger 列(clamp 1-28)
- payload 不带该 key 时保持原值(partial-update merge 契约,防漏 merge 类 bug)
- 非 int(含 bool)忽略

read 端 / 快照 / web 写端的契约测试由后续任务追加到本文件。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, SyncChange
from src.snapshot_builder import build


def _make_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TS = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    return TestClient(app), TS


def _register(client: TestClient, email: str, client_type: str = "app") -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": client_type,
            "device_name": f"pytest-{client_type}",
            "platform": client_type,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    content = (
        f'{{"ledgerName":"{ledger_id}","currency":"CNY","count":0,'
        '"items":[],"accounts":[],"categories":[],"tags":[]}'
    )
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger_snapshot",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": {"content": content},
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _push_ledger_upsert(
    client: TestClient, token: str, device_id: str, ledger_id: str, payload: dict
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": "ledger",
                    "entity_sync_id": ledger_id,
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _login_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest-web",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _ledger_row(TS, external_id: str) -> Ledger:
    with TS() as db:
        row = db.scalar(select(Ledger).where(Ledger.external_id == external_id))
        assert row is not None
        db.expunge(row)
        return row


def test_mobile_push_ledger_month_start_day_applies() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd1@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD1")
        _push_ledger_upsert(
            client, token, device, "L_MSD1",
            {"syncId": "L_MSD1", "ledgerName": "L_MSD1", "currency": "CNY",
             "monthStartDay": 15},
        )
        assert _ledger_row(TS, "L_MSD1").month_start_day == 15
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_ledger_partial_update_keeps_month_start_day() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd2@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD2")
        _push_ledger_upsert(
            client, token, device, "L_MSD2",
            {"syncId": "L_MSD2", "ledgerName": "L_MSD2", "currency": "CNY",
             "monthStartDay": 15},
        )
        # 老版本 App 改名:payload 不带 monthStartDay → 不得被重置
        _push_ledger_upsert(
            client, token, device, "L_MSD2",
            {"syncId": "L_MSD2", "ledgerName": "改名后", "currency": "CNY"},
        )
        row = _ledger_row(TS, "L_MSD2")
        assert row.name == "改名后"
        assert row.month_start_day == 15
    finally:
        app.dependency_overrides.clear()


def test_mobile_push_ledger_month_start_day_clamped() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd3@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD3")
        _push_ledger_upsert(
            client, token, device, "L_MSD3",
            {"syncId": "L_MSD3", "ledgerName": "L_MSD3", "currency": "CNY",
             "monthStartDay": 99},
        )
        assert _ledger_row(TS, "L_MSD3").month_start_day == 28
        _push_ledger_upsert(
            client, token, device, "L_MSD3",
            {"syncId": "L_MSD3", "ledgerName": "L_MSD3", "currency": "CNY",
             "monthStartDay": 0},
        )
        assert _ledger_row(TS, "L_MSD3").month_start_day == 1
        # 非 int(bool 是 int 子类,显式排除)忽略,保持原值
        _push_ledger_upsert(
            client, token, device, "L_MSD3",
            {"syncId": "L_MSD3", "ledgerName": "L_MSD3", "currency": "CNY",
             "monthStartDay": True},
        )
        assert _ledger_row(TS, "L_MSD3").month_start_day == 1
    finally:
        app.dependency_overrides.clear()


def test_read_ledgers_returns_month_start_day() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd5@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD5")
        _push_ledger_upsert(
            client, token, device, "L_MSD5",
            {"syncId": "L_MSD5", "ledgerName": "L_MSD5", "currency": "CNY",
             "monthStartDay": 20},
        )
        # /read/ledgers 要求 web scope 令牌
        web_token = _login_web(client, "msd5@example.com")["access_token"]
        res = client.get(
            "/api/v1/read/ledgers",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200, res.text
        rows = [r for r in res.json() if r["ledger_id"] == "L_MSD5"]
        assert rows and rows[0]["month_start_day"] == 20
        res = client.get(
            "/api/v1/read/ledgers/L_MSD5",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["month_start_day"] == 20
    finally:
        app.dependency_overrides.clear()


def test_snapshot_includes_month_start_day() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd6@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD6")
        _push_ledger_upsert(
            client, token, device, "L_MSD6",
            {"syncId": "L_MSD6", "ledgerName": "L_MSD6", "currency": "CNY",
             "monthStartDay": 15},
        )
        with TS() as db:
            ledger = db.scalar(select(Ledger).where(Ledger.external_id == "L_MSD6"))
            snapshot = build(db, ledger)
        assert snapshot["monthStartDay"] == 15
    finally:
        app.dependency_overrides.clear()


def test_web_meta_update_month_start_day() -> None:
    client, TS = _make_client()
    try:
        owner = _register(client, "msd4@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD4")

        web_token = _login_web(client, "msd4@example.com")["access_token"]
        res = client.get(
            "/api/v1/read/ledgers/L_MSD4",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200, res.text
        base = int(res.json()["source_change_id"])

        res = client.patch(
            "/api/v1/write/ledgers/L_MSD4/meta",
            headers={"Authorization": f"Bearer {web_token}"},
            json={"base_change_id": base, "month_start_day": 10},
        )
        assert res.status_code == 200, res.text
        assert _ledger_row(TS, "L_MSD4").month_start_day == 10

        # 显式 emit 的 ledger SyncChange payload 必须带 monthStartDay(mobile pull 依赖)
        with TS() as db:
            change = db.scalars(
                select(SyncChange)
                .where(
                    SyncChange.entity_type == "ledger",
                    SyncChange.entity_sync_id == "L_MSD4",
                )
                .order_by(SyncChange.change_id.desc())
            ).first()
            assert change is not None
            payload = change.payload_json
            if isinstance(payload, str):
                import json as _json

                payload = _json.loads(payload)
            assert payload["monthStartDay"] == 10

        # 越界被 pydantic 拒(422 在 handler 之前,base 值无关紧要)
        res = client.patch(
            "/api/v1/write/ledgers/L_MSD4/meta",
            headers={"Authorization": f"Bearer {web_token}"},
            json={"base_change_id": base + 1, "month_start_day": 29},
        )
        assert res.status_code == 422, res.text
    finally:
        app.dependency_overrides.clear()


def test_budget_usage_follows_ledger_month_start_day() -> None:
    """预算用量周期跟随 ledger.month_start_day,无视 budget.start_day(D5)。

    锚定 msd=15:对任意 now,先算包含 now 的 [15号, 次月15号) 周期起点
    period_start,再构造:
      - T_in:  period_start + 1h,金额 50 → 账本口径内
      - T_out: period_start - 1h,金额 70 → 账本口径外(上一周期)

    账本 msd=15,budget.start_day=1:
      - 账本口径 used == 50(只含 T_in)
    然后把账本 msd 改为 1(自然月口径):
      - 若 now.day >= 15:T_out(15日前1小时 = 14日23点)落在自然月内
        → 自然月口径 used == 120,与账本口径不同 → 强断言
      - 若 now.day < 15:T_out 在上月14日,两种口径均不含 → used == 0.0,
        弱断言(此窗口无判别力,可接受 CI 任意日期稳定)
    """
    from datetime import timedelta

    client, TS = _make_client()
    try:
        owner = _register(client, "msd7@example.com")
        token, device = owner["access_token"], owner["device_id"]
        _seed_ledger(client, token, device, "L_MSD7")

        now = datetime.now(timezone.utc)

        # 计算 msd=15 下包含 now 的周期起点
        if now.day >= 15:
            period_start = now.replace(
                day=15, hour=0, minute=0, second=0, microsecond=0
            )
        else:
            prev_month_last = now.replace(day=1) - timedelta(days=1)
            period_start = prev_month_last.replace(
                day=15, hour=0, minute=0, second=0, microsecond=0
            )

        t_in_time = period_start + timedelta(hours=1)   # 周期内
        t_out_time = period_start - timedelta(hours=1)  # 上一周期

        # 设账本 msd=15
        _push_ledger_upsert(
            client, token, device, "L_MSD7",
            {"syncId": "L_MSD7", "ledgerName": "L_MSD7", "currency": "CNY",
             "monthStartDay": 15},
        )

        def _push_change(entity_type: str, sync_id: str, payload: dict) -> None:
            res = client.post(
                "/api/v1/sync/push",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "device_id": device,
                    "changes": [{
                        "ledger_id": "L_MSD7",
                        "entity_type": entity_type,
                        "entity_sync_id": sync_id,
                        "action": "upsert",
                        "updated_at": now.isoformat(),
                        "payload": payload,
                    }],
                },
            )
            assert res.status_code == 200, res.text

        # budget.start_day=1(自然月)—— 正确实现应忽略此字段
        _push_change("budget", "B_MSD7", {
            "syncId": "B_MSD7", "type": "total", "amount": 1000.0,
            "period": "monthly", "startDay": 1, "enabled": True,
        })
        _push_change("transaction", "T_in_MSD7", {
            "syncId": "T_in_MSD7", "type": "expense", "amount": 50.0,
            "happenedAt": t_in_time.isoformat(),
        })
        _push_change("transaction", "T_out_MSD7", {
            "syncId": "T_out_MSD7", "type": "expense", "amount": 70.0,
            "happenedAt": t_out_time.isoformat(),
        })

        web_token = _login_web(client, "msd7@example.com")["access_token"]

        # 第一次 GET:msd=15,期望 used==50(T_in 在内,T_out 在外)
        res = client.get(
            "/api/v1/read/ledgers/L_MSD7/budgets/usage",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200, res.text
        items = res.json()["items"]
        assert items, "no budget usage items returned"
        used_15 = items[0]["used"]
        assert used_15 == 50.0, f"msd=15: expected used=50.0, got {used_15}"

        # 把账本 msd 改为 1(自然月口径)再 GET
        _push_ledger_upsert(
            client, token, device, "L_MSD7",
            {"syncId": "L_MSD7", "ledgerName": "L_MSD7", "currency": "CNY",
             "monthStartDay": 1},
        )
        res = client.get(
            "/api/v1/read/ledgers/L_MSD7/budgets/usage",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200, res.text
        items2 = res.json()["items"]
        assert items2, "no budget usage items returned (msd=1)"
        used_1 = items2[0]["used"]
        if now.day >= 15:
            # period_start = 本月15日。T_out = 14日23点。
            # 自然月口径 [1日, 下月1日) 含 T_in(15日01点) + T_out(14日23点) → used==120
            assert used_1 == 120.0, (
                f"msd=1 (day>=15): expected used=120.0, got {used_1}"
            )
        else:
            # period_start = 上月15日。t_in = 上月15日01点，t_out = 上月14日23点。
            # 自然月口径(msd=1) = [本月1日, 下月1日)：两笔都在上月，均不含。
            # msd=15 口径 = [上月15日, 本月15日)：含 t_in → 第一次 GET 已断言 used==50。
            # 虽然第二次用量为 0，但 msd 切换确实改变了结果(50 → 0)，有判别力。
            assert used_1 == 0.0, (
                f"msd=1 (day<15): expected used=0.0, got {used_1}"
            )
    finally:
        app.dependency_overrides.clear()


def test_web_create_ledger_with_month_start_day() -> None:
    client, TS = _make_client()
    try:
        _register(client, "msd8@example.com")
        web_token = _login_web(client, "msd8@example.com")["access_token"]
        res = client.post(
            "/api/v1/write/ledgers",
            headers={"Authorization": f"Bearer {web_token}"},
            json={"ledger_name": "新账本", "currency": "CNY", "month_start_day": 12},
        )
        assert res.status_code == 200, res.text
        ledger_external_id = res.json()["ledger_id"]
        assert _ledger_row(TS, ledger_external_id).month_start_day == 12

        with TS() as db:
            change = db.scalars(
                select(SyncChange)
                .where(
                    SyncChange.entity_type == "ledger",
                    SyncChange.entity_sync_id == ledger_external_id,
                )
                .order_by(SyncChange.change_id.desc())
            ).first()
            assert change is not None
            payload = change.payload_json
            if isinstance(payload, str):
                import json as _json

                payload = _json.loads(payload)
            assert payload["monthStartDay"] == 12
    finally:
        app.dependency_overrides.clear()
