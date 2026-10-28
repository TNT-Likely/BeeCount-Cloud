"""「支出统计不含已退款的支出金额」—— 统计类读端点的退款净额口径。

覆盖六类支出口径:
- /read/summary + /read/ledgers(账本卡片):expense_total 扣退款
- /read/workspace/analytics(首页 / 图表分析 / 月度收支走势 / 分类排行 /
  anomaly 归因):summary.expense_total / series.expense / category_ranks 扣退款
- /read/workspace/accounts(账户页):卡维度,只扣退回本卡的部分
- /read/workspace/tags(标签维度):扣退款
- /read/ledgers/{id}/budgets/usage(预算已用):扣退款
- /read/ledgers/{id}/member-share-stats + member-stats(成员维度):按份额比例扣

口径分工(与 0022 流水删除线修复一致):
- 全局/账本维度(analytics / summary / tags / 预算 / 成员):扣**全部**有效退款
  (refunded_total)—— 钱已退回用户,这笔支出就没真实发生;
- 卡维度(accounts):只扣**退回本卡**的部分(refunded_to_account)—— 跨卡退款
  钱到了别的卡,本卡支出仍然成立。

收入 / 余额不受影响:退款关联 income 交易本就带 excludeFromStats(不进收支
统计);余额是流水口径(含退款 income),净额后「收入 - 支出 = 余额」自洽。
cancelled 退款不扣;周期外的原单退款不影响该周期统计。
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


def _web_tx(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "happened_at": datetime.now(timezone.utc).isoformat(),
            **body,
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _push_entity(
    client: TestClient,
    token: str,
    device_id: str,
    ledger_id: str,
    entity_type: str,
    payload: dict,
) -> None:
    """sync/push 单实体 upsert(account / tag / member / transaction 通用)。"""
    now = datetime.now(timezone.utc).isoformat()
    res = client.post(
        "/api/v1/sync/push",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "device_id": device_id,
            "changes": [
                {
                    "ledger_id": ledger_id,
                    "entity_type": entity_type,
                    "entity_sync_id": payload.get("syncId"),
                    "action": "upsert",
                    "payload": payload,
                    "updated_at": now,
                }
            ],
        },
    )
    assert res.status_code == 200, res.text


def _create_refund(client: TestClient, token: str, ledger_id: str, **body):
    base = _latest_change_id(client, token, ledger_id)
    return client.post(
        f"/api/v1/write/ledgers/{ledger_id}/refunds",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "happened_at": datetime.now(timezone.utc).date().isoformat(),
            **body,
        },
    )


def _get(client: TestClient, token: str, path: str) -> dict | list:
    res = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    return res.json()


def _setup(email: str, ledger_id: str):
    """注册 owner + web 登录 + 建账本,返回 (client, TS, token, app_token, device)。"""
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, web["access_token"], owner["access_token"], owner["device_id"]


def _setup_two_accounts(email: str, ledger_id: str):
    client, TS, token, app_token, device = _setup(email, ledger_id)
    for sync_id, name in (("acc-1", "微信"), ("acc-2", "支付宝")):
        _push_entity(
            client, app_token, device, ledger_id, "account",
            {"syncId": sync_id, "name": name, "type": "card",
             "currency": "CNY", "initialBalance": 0},
        )
    return client, TS, token


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 账本卡片 / summary                                                            #
# --------------------------------------------------------------------------- #


def test_summary_and_ledger_list_expense_nets_refund() -> None:
    """/summary 与 /ledgers 卡片的 expense_total 扣已退款;收入/余额不动。"""
    client, TS, token = _setup("st1@example.com", "L_ST1")[:3]
    try:
        tx = _web_tx(
            client, token, "L_ST1", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        tx_id = tx["entity_id"]
        # 部分退款 200(统一退到微信之外的一张卡也无所谓 —— 全局维度扣全部)
        res = _create_refund(
            client, token, "L_ST1", original_tx_id=tx_id, amount=200.0,
            status="received",
        )
        assert res.status_code == 200, res.text

        summary = _get(client, token, "/api/v1/read/summary?ledger_id=L_ST1")
        assert summary["expense_total"] == pytest.approx(300.0)
        # 退款关联 income 带 excludeFromStats → 收入统计不含它
        assert summary["income_total"] == pytest.approx(0.0)
        # 余额是流水口径。本笔退款没填去向账户 → 不生成关联 income,
        # 余额仍只记支出 -500(有去向账户时余额会含退款 inflow,见
        # test_analytics_nets_cross_card_refund_in_global_dimension)。
        assert summary["balance"] == pytest.approx(-500.0)

        ledgers = _get(client, token, "/api/v1/read/ledgers")
        row = [l for l in ledgers if l["ledger_id"] == "L_ST1"][0]
        assert row["expense_total"] == pytest.approx(300.0)
        assert row["income_total"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_summary_cancelled_refund_not_netted() -> None:
    """cancelled 退款不进统计净额。"""
    client, TS, token = _setup("st2@example.com", "L_ST2")[:3]
    try:
        tx = _web_tx(
            client, token, "L_ST2", tx_type="expense", amount=100.0,
            category_name="数码",
        )
        res = _create_refund(
            client, token, "L_ST2", original_tx_id=tx["entity_id"],
            amount=100.0, status="cancelled",
        )
        assert res.status_code == 200, res.text
        summary = _get(client, token, "/api/v1/read/summary?ledger_id=L_ST2")
        assert summary["expense_total"] == pytest.approx(100.0)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# analytics(首页 / 图表分析 / 月度收支走势 / 分类排行)                            #
# --------------------------------------------------------------------------- #


def test_analytics_expense_nets_refund() -> None:
    """analytics:summary/series/分类排行都扣已退款,收入与笔数不变。"""
    client, TS, token = _setup("an1@example.com", "L_AN1")[:3]
    try:
        # 本周期两笔支出:100(退 40)+ 50(不退)
        tx_a = _web_tx(
            client, token, "L_AN1", tx_type="expense", amount=100.0,
            category_name="数码",
        )
        _web_tx(client, token, "L_AN1", tx_type="expense", amount=50.0,
                category_name="餐饮")
        res = _create_refund(
            client, token, "L_AN1", original_tx_id=tx_a["entity_id"],
            amount=40.0, status="received",
        )
        assert res.status_code == 200, res.text

        data = _get(
            client, token,
            "/api/v1/read/workspace/analytics?scope=month&metric=expense",
        )
        summary = data["summary"]
        # 100 - 40 + 50 = 110
        assert summary["expense_total"] == pytest.approx(110.0)
        assert summary["income_total"] == pytest.approx(0.0)
        # 笔数记真实交易(2 笔),不受退款影响
        assert summary["transaction_count"] == 2
        # 净额后自洽
        assert summary["balance"] == pytest.approx(-110.0)

        series = data["series"]
        assert len(series) == 1
        assert series[0]["bucket"] == _today_utc()
        assert series[0]["expense"] == pytest.approx(110.0)

        ranks = {r["category_name"]: r for r in data["category_ranks"]}
        assert ranks["数码"]["total"] == pytest.approx(60.0)
        assert ranks["数码"]["tx_count"] == 1
        assert ranks["餐饮"]["total"] == pytest.approx(50.0)
    finally:
        app.dependency_overrides.clear()


def test_analytics_full_refund_drops_category_rank() -> None:
    """全额退款 → 该分类支出净额为 0,跌出分类排行(total > 0 才上榜)。"""
    client, TS, token = _setup("an2@example.com", "L_AN2")[:3]
    try:
        tx = _web_tx(
            client, token, "L_AN2", tx_type="expense", amount=100.0,
            category_name="数码",
        )
        res = _create_refund(
            client, token, "L_AN2", original_tx_id=tx["entity_id"],
            amount=100.0, status="received",
        )
        assert res.status_code == 200, res.text

        data = _get(
            client, token,
            "/api/v1/read/workspace/analytics?scope=month&metric=expense",
        )
        assert data["summary"]["expense_total"] == pytest.approx(0.0)
        assert data["category_ranks"] == []
        assert data["series"][0]["expense"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_analytics_nets_cross_card_refund_in_global_dimension() -> None:
    """全局维度扣全部退款:跨卡退款(退到别的卡)analytics 支出仍净额为 0。

    与卡维度(accounts)分工:那边只扣退回本卡的部分。
    """
    client, TS, token = _setup_two_accounts("an3@example.com", "L_AN3")
    try:
        tx = _web_tx(
            client, token, "L_AN3", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        res = _create_refund(
            client, token, "L_AN3", original_tx_id=tx["entity_id"],
            amount=500.0, refund_mode="unified", account_id="acc-2",
            account_name="支付宝", status="received",
        )
        assert res.status_code == 200, res.text

        data = _get(
            client, token,
            "/api/v1/read/workspace/analytics?scope=month&metric=expense",
        )
        # 钱退回用户(到支付宝),这笔支出全局统计里不成立
        assert data["summary"]["expense_total"] == pytest.approx(0.0)
        assert data["series"][0]["expense"] == pytest.approx(0.0)
        # 余额含退款 income(+500)- 支出(-500)= 0,自洽
        assert data["summary"]["balance"] == pytest.approx(0.0)
    finally:
        app.dependency_overrides.clear()


def test_analytics_refund_outside_period_not_netted() -> None:
    """周期外原单的退款不影响本周期统计(净额只对周期内的支出原单生效)。"""
    client, TS, token = _setup("an4@example.com", "L_AN4")[:3]
    try:
        now = datetime.now(timezone.utc)
        prev_month = (now.replace(day=1) - timedelta(days=1))
        prev_label = prev_month.strftime("%Y-%m")
        prev_happened = prev_month.replace(
            hour=12, minute=0, second=0, microsecond=0).isoformat()

        base = _latest_change_id(client, token, "L_AN4")
        res = client.post(
            "/api/v1/write/ledgers/L_AN4/transactions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "base_change_id": base,
                "tx_type": "expense",
                "amount": 100.0,
                "category_name": "数码",
                "happened_at": prev_happened,
            },
        )
        assert res.status_code == 200, res.text
        prev_tx_id = res.json()["entity_id"]

        res = _create_refund(
            client, token, "L_AN4", original_tx_id=prev_tx_id,
            amount=100.0, status="received",
        )
        assert res.status_code == 200, res.text

        # 查上个自然月:那笔支出被净额为 0
        prev_data = _get(
            client, token,
            f"/api/v1/read/workspace/analytics?scope=month&metric=expense&period={prev_label}",
        )
        assert prev_data["summary"]["expense_total"] == pytest.approx(0.0)
        assert prev_data["range"]["period"] == prev_label

        # 查本月:不受上月退款影响(本月无支出)
        cur_label = now.strftime("%Y-%m")
        cur_data = _get(
            client, token,
            f"/api/v1/read/workspace/analytics?scope=month&metric=expense&period={cur_label}",
        )
        assert cur_data["summary"]["expense_total"] == pytest.approx(0.0)
        assert cur_data["summary"]["transaction_count"] == 0
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 账户页:卡维度,只扣退回本卡的部分                                               #
# --------------------------------------------------------------------------- #


def test_accounts_expense_nets_only_same_card() -> None:
    """跨卡退款不退本卡 → 本卡支出仍成立;退回本卡的部分才扣。"""
    client, TS, token = _setup_two_accounts("ac1@example.com", "L_AC1")
    try:
        tx = _web_tx(
            client, token, "L_AC1", tx_type="expense", amount=500.0,
            category_name="数码", account_name="微信",
        )
        tx_id = tx["entity_id"]

        def expense_by_card() -> dict[str, float]:
            rows = _get(
                client, token, "/api/v1/read/workspace/accounts?ledger_id=L_AC1")
            return {r["name"]: float(r["expense_total"]) for r in rows}

        assert expense_by_card() == {"微信": 500.0, "支付宝": 0.0}

        # 跨卡:200 退到支付宝 → 微信支出不变
        res = _create_refund(
            client, token, "L_AC1", original_tx_id=tx_id, amount=200.0,
            refund_mode="unified", account_id="acc-2", account_name="支付宝",
            status="received",
        )
        assert res.status_code == 200, res.text
        assert expense_by_card() == {"微信": 500.0, "支付宝": 0.0}

        # 再退 100 回微信 → 微信支出净额 400
        res = _create_refund(
            client, token, "L_AC1", original_tx_id=tx_id, amount=100.0,
            refund_mode="unified", account_id="acc-1", account_name="微信",
            status="received",
        )
        assert res.status_code == 200, res.text
        assert expense_by_card() == {"微信": 400.0, "支付宝": 0.0}
    finally:
        app.dependency_overrides.clear()


def test_accounts_expense_nets_split_legs() -> None:
    """合并付款 legs:退款按腿退回各卡,各卡支出各扣各的。"""
    client, TS, token = _setup_two_accounts("ac2@example.com", "L_AC2")
    try:
        tx = _web_tx(
            client, token, "L_AC2", tx_type="expense", amount=500.0,
            category_name="数码",
            payment_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 300.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
            ],
        )
        tx_id = tx["entity_id"]

        # 分别退款:微信回 100、支付宝回 200
        res = _create_refund(
            client, token, "L_AC2", original_tx_id=tx_id, amount=300.0,
            refund_mode="split",
            refund_legs=[
                {"accountId": "acc-1", "accountName": "微信", "amount": 100.0},
                {"accountId": "acc-2", "accountName": "支付宝", "amount": 200.0},
            ],
            status="received",
        )
        assert res.status_code == 200, res.text

        rows = _get(
            client, token, "/api/v1/read/workspace/accounts?ledger_id=L_AC2")
        expense = {r["name"]: float(r["expense_total"]) for r in rows}
        assert expense == {"微信": 200.0, "支付宝": 0.0}
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 标签维度                                                                      #
# --------------------------------------------------------------------------- #


def test_tags_expense_nets_refund() -> None:
    """标签 expense_total 扣已退款;笔数不变。"""
    client, TS, token, app_token, device = _setup("tg1@example.com", "L_TG1")
    try:
        _push_entity(
            client, app_token, device, "L_TG1", "tag",
            {"syncId": "tag-1", "name": "旅行"},
        )
        _push_entity(
            client, app_token, device, "L_TG1", "transaction",
            {
                "syncId": "tx-tag-1",
                "type": "expense",
                "amount": 200.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "categoryName": "数码",
                "tags": ["旅行"],
            },
        )
        res = _create_refund(
            client, token, "L_TG1", original_tx_id="tx-tag-1",
            amount=50.0, status="received",
        )
        assert res.status_code == 200, res.text

        tags = _get(client, token, "/api/v1/read/workspace/tags?ledger_id=L_TG1")
        row = [t for t in tags if t["name"] == "旅行"][0]
        assert row["expense_total"] == pytest.approx(150.0)
        assert row["tx_count"] == 1
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 预算已用                                                                      #
# --------------------------------------------------------------------------- #


def test_budget_usage_nets_refund() -> None:
    """预算 used 扣已退款(退款关联 income 本就 excludeFromBudget,不重复计)。"""
    client, TS, token, app_token, device = _setup("bu1@example.com", "L_BU1")
    try:
        _push_entity(
            client, app_token, device, "L_BU1", "transaction",
            {
                "syncId": "tx-bu-1",
                "type": "expense",
                "amount": 100.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "categoryName": "数码",
            },
        )
        res = _create_refund(
            client, token, "L_BU1", original_tx_id="tx-bu-1",
            amount=40.0, status="received",
        )
        assert res.status_code == 200, res.text

        base = _latest_change_id(client, token, "L_BU1")
        res = client.post(
            "/api/v1/write/ledgers/L_BU1/budgets",
            headers={"Authorization": f"Bearer {token}"},
            json={"base_change_id": base, "type": "total", "amount": 5000,
                  "start_day": 1},
        )
        assert res.status_code == 200, res.text

        usage = _get(
            client, token, "/api/v1/read/ledgers/L_BU1/budgets/usage")
        items = usage["items"]
        assert len(items) == 1
        assert items[0]["used"] == pytest.approx(60.0)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 成员维度                                                                      #
# --------------------------------------------------------------------------- #


def test_member_share_stats_nets_refund() -> None:
    """成员分摊统计:退款按各成员份额比例等比例扣。"""
    client, TS, token, app_token, device = _setup("mb1@example.com", "L_MB1")
    try:
        for sync_id, name in (("m-1", "爸爸"), ("m-2", "妈妈")):
            _push_entity(
                client, app_token, device, "L_MB1", "member",
                {"syncId": sync_id, "name": name},
            )
        _push_entity(
            client, app_token, device, "L_MB1", "transaction",
            {
                "syncId": "tx-mb-1",
                "type": "expense",
                "amount": 300.0,
                "happenedAt": datetime.now(timezone.utc).isoformat(),
                "categoryName": "数码",
                "memberShares": [
                    {"memberId": "m-1", "memberName": "爸爸", "amount": 100.0},
                    {"memberId": "m-2", "memberName": "妈妈", "amount": 200.0},
                ],
            },
        )
        # 退 150(一半)→ 爸爸 100-50 / 妈妈 200-100
        res = _create_refund(
            client, token, "L_MB1", original_tx_id="tx-mb-1",
            amount=150.0, status="received",
        )
        assert res.status_code == 200, res.text

        data = _get(
            client, token,
            "/api/v1/ledgers/L_MB1/member-share-stats?scope=month")
        by_member = {i["id"]: i for i in data["items"]}
        assert by_member["m-1"]["expense_total"] == pytest.approx(50.0)
        assert by_member["m-2"]["expense_total"] == pytest.approx(100.0)
        # 笔数仍各 1(记真实交易)
        assert by_member["m-1"]["tx_count"] == 1

        # 记账人维度(member-stats):owner 记的这笔支出也净额 150
        member_stats = _get(
            client, token,
            "/api/v1/ledgers/L_MB1/member-stats?scope=month")
        hit = [i for i in member_stats["items"] if i["expense_total"] > 0]
        assert len(hit) == 1
        assert hit[0]["expense_total"] == pytest.approx(150.0)
        assert hit[0]["tx_count"] == 1
    finally:
        app.dependency_overrides.clear()
