"""定期存款「到息计划」(0028):多笔 / 多次结息。

计划内嵌在存款实体上(payout_plans_json,与退款腿同款),每条 = 到息日 +
金额(自由金额,不区分利息/本息)+ 可选备注。

覆盖范围:
- 多笔往返:按到息日升序返回、金额与备注保留、合计正确
- is_due 按「今天」派生:过去已到息 / 未来未到息;next_payout_date 取最早未到息
- 不传计划 → 空数组;显式 [] / null → 清空
- PATCH 不带该字段 → 保留现值(不能被清掉)
- 脏数据(日期不可解析)整条丢弃,不污染列表
- 计划不生成任何交易、不进余额与收支统计
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest-app",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


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


def _latest_change_id(client: TestClient, token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def _setup(email: str, ledger_id: str):
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, web["access_token"]


def _create_deposit(client: TestClient, token: str, ledger_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/time-deposits",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "principal": 10000.0,
            "start_date": "2026-01-01",
            **body,
        },
    )


def _patch_deposit(client: TestClient, token: str, ledger_id: str, deposit_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    return client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/time-deposits/{deposit_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )


def _deposits(client: TestClient, token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/time-deposits",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _days(n: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- #


def test_multiple_payout_plans_roundtrip() -> None:
    """多笔计划往返:按到息日升序、金额/备注保留、合计正确。"""
    client, TS, token = _setup("td1@example.com", "L_TD1")
    try:
        res = _create_deposit(
            client, token, "L_TD1",
            payout_plans=[
                {"date": "2026-07-01", "amount": 162.5},
                {"date": "2026-01-01", "amount": 100.0, "note": "首期"},
                {"date": "2026-04-01", "amount": 50.25},
            ],
        )
        assert res.status_code == 200, res.text
        row = _deposits(client, token, "L_TD1")[0]
        plans = row["payout_plans"]

        # 按到息日升序(写入顺序无所谓)
        assert [p["date"] for p in plans] == ["2026-01-01", "2026-04-01", "2026-07-01"]
        assert [p["amount"] for p in plans] == pytest.approx([100.0, 50.25, 162.5])
        assert plans[0]["note"] == "首期"
        assert plans[1]["note"] is None
        assert row["payout_plans_total"] == pytest.approx(312.75)
    finally:
        app.dependency_overrides.clear()


def test_payout_plan_is_due_and_next_date() -> None:
    """is_due 按今天派生;next_payout_date 取最早的一条未到息。"""
    client, TS, token = _setup("td2@example.com", "L_TD2")
    try:
        res = _create_deposit(
            client, token, "L_TD2",
            payout_plans=[
                {"date": _days(-10), "amount": 10.0},
                {"date": _days(60), "amount": 30.0},
                {"date": _days(30), "amount": 20.0},
            ],
        )
        assert res.status_code == 200, res.text
        row = _deposits(client, token, "L_TD2")[0]
        plans = row["payout_plans"]

        assert [p["is_due"] for p in plans] == [True, False, False]
        assert row["next_payout_date"] == _days(30)
        assert row["payout_plans_total"] == pytest.approx(60.0)
    finally:
        app.dependency_overrides.clear()


def test_no_plans_and_clear_plans() -> None:
    """不传 → 空数组;显式 [] → 清空已有计划。"""
    client, TS, token = _setup("td3@example.com", "L_TD3")
    try:
        res = _create_deposit(client, token, "L_TD3")
        assert res.status_code == 200, res.text
        dep_id = res.json()["entity_id"]
        row = _deposits(client, token, "L_TD3")[0]
        assert row["payout_plans"] == []
        assert row["payout_plans_total"] == pytest.approx(0.0)
        assert row["next_payout_date"] is None

        # 加两笔
        res = _patch_deposit(
            client, token, "L_TD3", dep_id,
            payout_plans=[
                {"date": "2026-03-01", "amount": 15.0},
                {"date": "2026-06-01", "amount": 25.0},
            ],
        )
        assert res.status_code == 200, res.text
        assert len(_deposits(client, token, "L_TD3")[0]["payout_plans"]) == 2

        # 清空
        res = _patch_deposit(client, token, "L_TD3", dep_id, payout_plans=[])
        assert res.status_code == 200, res.text
        row = _deposits(client, token, "L_TD3")[0]
        assert row["payout_plans"] == []
        assert row["payout_plans_total"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_patch_without_plans_keeps_existing() -> None:
    """PATCH 不带 payout_plans(只改别的字段)→ 计划保留,不能被清掉。"""
    client, TS, token = _setup("td4@example.com", "L_TD4")
    try:
        res = _create_deposit(
            client, token, "L_TD4",
            payout_plans=[{"date": "2026-05-01", "amount": 88.0}],
        )
        assert res.status_code == 200, res.text
        dep_id = res.json()["entity_id"]

        # 只改备注,不提计划
        res = _patch_deposit(client, token, "L_TD4", dep_id, note="改个备注")
        assert res.status_code == 200, res.text
        row = _deposits(client, token, "L_TD4")[0]
        assert row["note"] == "改个备注"
        assert len(row["payout_plans"]) == 1
        assert row["payout_plans"][0]["amount"] == pytest.approx(88.0)
    finally:
        app.dependency_overrides.clear()


def test_invalid_plan_items_dropped() -> None:
    """日期不可解析的脏项整条丢弃,不影响其它条目。"""
    client, TS, token = _setup("td5@example.com", "L_TD5")
    try:
        res = _create_deposit(
            client, token, "L_TD5",
            payout_plans=[
                {"date": "not-a-date", "amount": 50.0},
                {"date": "2026-08-01", "amount": 70.0},
            ],
        )
        assert res.status_code == 200, res.text
        row = _deposits(client, token, "L_TD5")[0]
        assert len(row["payout_plans"]) == 1
        assert row["payout_plans"][0]["date"] == "2026-08-01"
        assert row["payout_plans_total"] == pytest.approx(70.0)
    finally:
        app.dependency_overrides.clear()


def test_plans_do_not_affect_stats_or_balance() -> None:
    """计划只是提醒:不生成交易,不进余额与收支统计。"""
    client, TS, token = _setup("td6@example.com", "L_TD6")
    try:
        _create_deposit(
            client, token, "L_TD6",
            payout_plans=[
                {"date": _days(-1), "amount": 500.0},
                {"date": _days(30), "amount": 300.0},
            ],
        )
        res = client.get(
            "/api/v1/read/summary?ledger_id=L_TD6",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        summary = res.json()
        assert summary["income_total"] == pytest.approx(0.0)
        assert summary["expense_total"] == pytest.approx(0.0)
        assert summary["balance"] == pytest.approx(0.0)
        # 计划到息不生成任何交易:流水仍为空。
        page = client.get(
            "/api/v1/read/workspace/transactions?ledger_id=L_TD6",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert page["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_change_term_months_recomputes_expected_interest() -> None:
    """0031 回归:改期限(月)应重算到期日与预期利息。

    旧逻辑仅在 maturityDate 缺省时才按 termMonths 推算,而客户端编辑总会带上
    旧 maturityDate,导致「改期限 → 到期日不变 → 预期利息不变」。
    修复后:payload 带 termMonths 就按 start + termMonths 重算到期日。
    """
    client, TS, token = _setup("td7@example.com", "L_TD7")
    try:
        # 12 个月,本金 10000,年利率 3% → 预期利息 10000*0.03*365/365 = 300.0
        res = _create_deposit(
            client, token, "L_TD7",
            annual_rate=0.03,
            term_months=12,
            start_date="2026-01-01",
        )
        assert res.status_code == 200, res.text
        dep_id = res.json()["entity_id"]
        row = _deposits(client, token, "L_TD7")[0]
        assert row["maturity_date"][:10] == "2027-01-01"
        assert row["expected_interest"] == pytest.approx(300.0)

        # 改成 24 个月(前端为整表重提交,这里带全套字段)→ 到期日 2028-01-01,
        # 预期利息翻倍 600.0。
        res = _patch_deposit(
            client, token, "L_TD7", dep_id,
            principal=10000.0,
            annual_rate=0.03,
            term_months=24,
            start_date="2026-01-01",
        )
        assert res.status_code == 200, res.text
        row = _deposits(client, token, "L_TD7")[0]
        assert row["maturity_date"][:10] == "2028-01-01"
        assert row["expected_interest"] == pytest.approx(600.0)
    finally:
        app.dependency_overrides.clear()
