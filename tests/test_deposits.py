"""定期存款(web-first)REST + 利息/到期日派生 + 流水联动测试。

Covers:存单 CRUD、到期日/利息公式与手动覆盖、状态派生(holding/matured/
withdrawn/renewed)、存入划转流水(transfer 活期→虚拟定期账户)、支取(本金
转回 + 利息 income)、转存(新存单 + 三流水)、周期结息 + 删除联动、summary
虚拟账户口径、到期提醒(7 天/当天/已到期)。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app

NOW = datetime.now(timezone.utc)


def _make_client() -> TestClient:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    return TestClient(app)


def _register_web(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email, "password": "123456", "client_type": "web",
            "device_name": "pytest", "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_ledger_web(client: TestClient, token: str, ledger_id: str) -> None:
    res = client.post(
        "/api/v1/write/ledgers",
        headers=_auth(token),
        json={
            "ledger_id": ledger_id, "ledger_name": ledger_id,
            "currency": "CNY", "month_start_day": 1,
        },
    )
    assert res.status_code == 200, res.text


def _create_account_web(client: TestClient, token: str, ledger_id: str, name: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts",
        headers=_auth(token),
        json={"base_change_id": 0, "name": name},
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _list_txns(client: TestClient, token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=_auth(token)
    )
    assert res.status_code == 200, res.text
    return res.json()


def _find_flow(txns: list[dict], tx_sync_id: str | None) -> dict | None:
    if not tx_sync_id:
        return None
    return next((t for t in txns if t["id"] == tx_sync_id), None)


def _create_deposit(client: TestClient, token: str, ledger_id: str, **extra) -> dict:
    payload = {
        "ledger_id": ledger_id,
        "name": "三年期大额存单",
        "principal": 10000.0,
        "happened_at": NOW.isoformat(),
        "term_months": 36,
        "annual_rate": 2.75,
    }
    payload.update(extra)
    res = client.post("/api/v1/deposits", headers=_auth(token), json=payload)
    assert res.status_code == 201, res.text
    return res.json()


# --------------------------------------------------------------------------


def test_deposit_crud_interest_and_maturity() -> None:
    client = _make_client()
    user = _register_web(client, "dep1@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "DP1")

    # 不传账户 → 创建成功,无流水;利息按公式 10000 × 2.75% × 3 = 825。
    dep = _create_deposit(client, token, "DP1")
    assert dep["status"] == "holding"
    assert dep["expected_interest"] == 825.0
    assert dep["total_at_maturity"] == 10825.0
    assert dep["maturity_at"] is not None
    # 到期日 = 存入时间 + 36 个月,秒级精度(时间部分不变)。
    start = datetime.fromisoformat(dep["started_at"])
    maturity = datetime.fromisoformat(dep["maturity_at"])
    assert maturity.year == start.year + 3
    assert maturity.hour == start.hour and maturity.second == start.second
    # 未选账户 → 不生成划转流水。
    assert dep["flow_tx_sync_id"] is None

    # 手动覆盖利息 → expected_interest 跟随。
    res = client.patch(
        f"/api/v1/deposits/{dep['id']}", headers=_auth(token),
        json={"interest_override": 900.0, "note": "银行实际利息"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["expected_interest"] == 900.0
    assert res.json()["total_at_maturity"] == 10900.0

    # 月末收敛:2024-01-31 + 1M = 2024-02-29。
    dep2 = _create_deposit(
        client, token, "DP1", name="月度测试", principal=100.0,
        term_months=1, annual_rate=0.0,
        happened_at="2024-01-31T10:30:45+00:00",
    )
    maturity2 = datetime.fromisoformat(dep2["maturity_at"])
    assert (maturity2.year, maturity2.month, maturity2.day) == (2024, 2, 29)
    assert dep2["expected_interest"] == 0.0  # 无利率 → 0

    # 列表 + 详情 + 删除。
    lst = client.get("/api/v1/deposits", headers=_auth(token), params={"ledger_id": "DP1"})
    assert lst.status_code == 200
    assert len(lst.json()) == 2
    detail = client.get(f"/api/v1/deposits/{dep['id']}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["interest_override"] == 900.0
    res = client.delete(f"/api/v1/deposits/{dep['id']}", headers=_auth(token))
    assert res.status_code == 204


def test_deposit_flow_generation_and_withdraw() -> None:
    client = _make_client()
    user = _register_web(client, "dep2@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "DP2")
    acc_id = _create_account_web(client, token, "DP2", "工商银行")

    dep = _create_deposit(
        client, token, "DP2", account_id=acc_id, account_name="工商银行",
    )
    # 存入划转:transfer 活期 → 虚拟定期存款账户。
    flow = _find_flow(_list_txns(client, token, "DP2"), dep["flow_tx_sync_id"])
    assert flow is not None
    assert flow["tx_type"] == "transfer"
    assert flow["amount"] == 10000.0
    assert flow["from_account_id"] == acc_id
    assert flow["to_account_id"] == "virtual:deposit"
    assert "存入定期" in (flow["note"] or "")

    # summary:虚拟账户总额 = 本金。
    summary = client.get(
        "/api/v1/deposits/summary", headers=_auth(token), params={"ledger_id": "DP2"}
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["count"] == 1
    assert body["total_principal"] == 10000.0
    assert body["total_interest_expected"] == 825.0

    # 支取:本金转回 transfer + 利息 income(计入统计)。
    at = (NOW + timedelta(days=1)).isoformat()
    res = client.post(
        f"/api/v1/deposits/{dep['id']}/withdraw", headers=_auth(token),
        json={"withdrawn_at": at},
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "withdrawn"
    assert out["withdrawn_interest"] == 825.0
    txns = _list_txns(client, token, "DP2")
    ret = _find_flow(txns, out["principal_return_tx_sync_id"])
    assert ret is not None
    assert ret["tx_type"] == "transfer"
    assert ret["amount"] == 10000.0
    assert ret["from_account_id"] == "virtual:deposit"
    assert ret["to_account_id"] == acc_id
    intr = _find_flow(txns, out["interest_tx_sync_id"])
    assert intr is not None
    assert intr["tx_type"] == "income"
    assert intr["amount"] == 825.0
    assert intr["exclude_from_stats"] is False
    assert "存款利息" in (intr["note"] or "")

    # 重复支取 → 409。
    res = client.post(
        f"/api/v1/deposits/{dep['id']}/withdraw", headers=_auth(token),
        json={"withdrawn_at": at},
    )
    assert res.status_code == 409

    # 支取后 summary 归零。
    summary = client.get(
        "/api/v1/deposits/summary", headers=_auth(token), params={"ledger_id": "DP2"}
    )
    assert summary.json()["count"] == 0


def test_deposit_renew_creates_new_term() -> None:
    client = _make_client()
    user = _register_web(client, "dep3@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "DP3")
    acc_id = _create_account_web(client, token, "DP3", "建设银行")

    dep = _create_deposit(
        client, token, "DP3", account_id=acc_id, account_name="建设银行",
        term_months=12, annual_rate=2.0,
    )
    res = client.post(
        f"/api/v1/deposits/{dep['id']}/renew", headers=_auth(token),
        json={"new_rate": 1.9, "new_term_months": 12},
    )
    assert res.status_code == 200, res.text
    new_dep = res.json()
    assert new_dep["parent_deposit_id"] == dep["id"]
    assert new_dep["annual_rate"] == 1.9
    assert new_dep["principal"] == 10200.0  # 本金 + 利息(10000×2%×1)
    assert new_dep["maturity_at"] is not None

    # 旧存单:renewed,并回链新存单。
    old = client.get(f"/api/v1/deposits/{dep['id']}", headers=_auth(token)).json()
    assert old["status"] == "renewed"
    assert old["renewed_to_deposit_id"] == new_dep["id"]

    # 账面:利息 income + 本金转回 + 新存单划转,三笔都在。
    txns = _list_txns(client, token, "DP3")
    assert _find_flow(txns, old["interest_tx_sync_id"]) is not None
    assert _find_flow(txns, old["principal_return_tx_sync_id"]) is not None
    new_flow = _find_flow(txns, new_dep["flow_tx_sync_id"])
    assert new_flow is not None
    assert new_flow["amount"] == 10200.0
    assert new_flow["from_account_id"] == acc_id

    # 新存单独立流转:支取后状态闭环。
    res = client.post(
        f"/api/v1/deposits/{new_dep['id']}/withdraw", headers=_auth(token),
        json={"withdrawn_at": NOW.isoformat()},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "withdrawn"

    # summary 只算持有中的新存单口径已归零。
    summary = client.get(
        "/api/v1/deposits/summary", headers=_auth(token), params={"ledger_id": "DP3"}
    )
    assert summary.json()["count"] == 0


def test_deposit_periodic_interest_settlement() -> None:
    client = _make_client()
    user = _register_web(client, "dep4@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "DP4")
    acc_id = _create_account_web(client, token, "DP4", "农业银行")

    dep = _create_deposit(
        client, token, "DP4", account_id=acc_id, account_name="农业银行",
        interest_settlement="monthly", settlement_day=21,
    )
    # 结息 50 → income 流水 + 记录。
    res = client.post(
        f"/api/v1/deposits/{dep['id']}/settle-interest", headers=_auth(token),
        json={"amount": 50.0, "settled_at": NOW.isoformat(), "note": "第一季度结息"},
    )
    assert res.status_code == 201, res.text
    pay = res.json()
    assert pay["tx_sync_id"]
    flow = _find_flow(_list_txns(client, token, "DP4"), pay["tx_sync_id"])
    assert flow is not None
    assert flow["tx_type"] == "income"
    assert flow["amount"] == 50.0

    # 结息列表 + settled_interest 汇总。
    lst = client.get(
        f"/api/v1/deposits/{dep['id']}/interest-payments", headers=_auth(token)
    )
    assert lst.status_code == 200
    assert len(lst.json()) == 1
    detail = client.get(f"/api/v1/deposits/{dep['id']}", headers=_auth(token)).json()
    assert detail["settled_interest"] == 50.0

    # 支取时利息默认 = 预期 - 已结息。
    res = client.post(
        f"/api/v1/deposits/{dep['id']}/withdraw", headers=_auth(token),
        json={"withdrawn_at": NOW.isoformat()},
    )
    assert res.status_code == 200
    assert res.json()["withdrawn_interest"] == 775.0  # 825 - 50

    # 删除结息记录 → 流水联动删除。用第二张存单验证。
    dep2 = _create_deposit(
        client, token, "DP4", account_id=acc_id, account_name="农业银行", name="第二张",
    )
    pay2 = client.post(
        f"/api/v1/deposits/{dep2['id']}/settle-interest", headers=_auth(token),
        json={"amount": 10.0, "settled_at": NOW.isoformat()},
    ).json()
    res = client.delete(
        f"/api/v1/deposits/interest-payments/{pay2['id']}", headers=_auth(token)
    )
    assert res.status_code == 204
    assert _find_flow(_list_txns(client, token, "DP4"), pay2["tx_sync_id"]) is None
    detail2 = client.get(f"/api/v1/deposits/{dep2['id']}", headers=_auth(token)).json()
    assert detail2["settled_interest"] == 0.0


def test_deposit_edit_recomputes_flow_and_maturity() -> None:
    client = _make_client()
    user = _register_web(client, "dep5@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "DP5")
    acc_id = _create_account_web(client, token, "DP5", "中国银行")

    dep = _create_deposit(
        client, token, "DP5", account_id=acc_id, account_name="中国银行",
    )
    old_flow = dep["flow_tx_sync_id"]
    # 改本金 → 流水原地更新(同一 syncId),金额跟新值对齐。
    res = client.patch(
        f"/api/v1/deposits/{dep['id']}", headers=_auth(token), json={"principal": 12000.0}
    )
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["flow_tx_sync_id"] == old_flow
    flow = _find_flow(_list_txns(client, token, "DP5"), old_flow)
    assert flow["amount"] == 12000.0

    # 删除存单 → 划转流水联动删除。
    res = client.delete(f"/api/v1/deposits/{dep['id']}", headers=_auth(token))
    assert res.status_code == 204
    assert _find_flow(_list_txns(client, token, "DP5"), old_flow) is None


def test_deposit_reminders_and_matured_status() -> None:
    client = _make_client()
    user = _register_web(client, "dep6@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "DP6")

    # 3 天后到期 → near_due。
    dep3 = _create_deposit(
        client, token, "DP6", name="三天后到期", term_months=1,
        annual_rate=1.0, happened_at=(NOW - timedelta(days=27)).isoformat(),
    )
    assert dep3["status"] == "holding"
    rem = client.get(
        "/api/v1/deposits/reminders", headers=_auth(token), params={"ledger_id": "DP6"}
    )
    assert rem.status_code == 200
    body = rem.json()
    ids_near = [item["deposit"]["id"] for item in body["near_due"]]
    ids_over = [item["deposit"]["id"] for item in body["overdue"]]
    assert dep3["id"] in ids_near

    # 已到期(1 个月前存入,1 个月期)→ matured + overdue。
    dep_old = _create_deposit(
        client, token, "DP6", name="已到期", term_months=1,
        annual_rate=1.0, happened_at=(NOW - timedelta(days=40)).isoformat(),
    )
    detail = client.get(f"/api/v1/deposits/{dep_old['id']}", headers=_auth(token)).json()
    assert detail["status"] == "matured"
    assert detail["days_to_maturity"] < 0
    rem = client.get(
        "/api/v1/deposits/reminders", headers=_auth(token), params={"ledger_id": "DP6"}
    ).json()
    ids_over = [item["deposit"]["id"] for item in rem["overdue"]]
    assert dep_old["id"] in ids_over

    # 远期存单(5 年)不出现在提醒里。
    dep_far = _create_deposit(
        client, token, "DP6", name="远期", term_months=60,
        happened_at=NOW.isoformat(),
    )
    rem = client.get(
        "/api/v1/deposits/reminders", headers=_auth(token), params={"ledger_id": "DP6"}
    ).json()
    all_ids = [i["deposit"]["id"] for i in rem["near_due"] + rem["overdue"]]
    assert dep_far["id"] not in all_ids

    # status 过滤参数。
    lst = client.get(
        "/api/v1/deposits", headers=_auth(token),
        params={"ledger_id": "DP6", "status": "matured"},
    ).json()
    assert all(o["status"] == "matured" for o in lst) and len(lst) == 1
