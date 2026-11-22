"""储蓄罐(web-first)REST 测试:计划 CRUD、存入记录修改/删除、流水联动。

Covers:
1. 计划创建 / 列表 / 更新(PATCH 改名称、目标金额、状态);
2. 存入记录创建(带扣款账户 → 生成支出流水);
3. **修改存入金额 → 关联流水金额同步改**(金额是实时派生的,
   只改 contribution.amount 会导致已存金额不跟随);
4. 修改备注 / 时间 / 分类 / 统计开关;
5. 删除存入记录 → 关联流水一并删除(不留孤儿交易);
6. 删除计划 → 其所有存入记录 + 流水一并删除;
7. 无关联流水的"仅记录"存入也能正常修改。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> str:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email, "password": "123456", "client_type": "web",
            "device_name": "pytest", "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _create_ledger(client: TestClient, token: str, ledger_id: str) -> None:
    res = client.post(
        "/api/v1/write/ledgers",
        headers=_auth(token),
        json={
            "ledger_id": ledger_id, "ledger_name": ledger_id,
            "currency": "CNY", "month_start_day": 1,
        },
    )
    assert res.status_code == 200, res.text


def _create_account(client: TestClient, token: str, ledger_id: str, name: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts",
        headers=_auth(token),
        json={"base_change_id": 0, "name": name},
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _create_plan(client: TestClient, token: str, ledger_id: str, **over) -> dict:
    body = {
        "ledger_id": ledger_id,
        "name": "旅行基金",
        "target_amount": 10000,
        "plan_type": "free",
    }
    body.update(over)
    res = client.post("/api/v1/saving-plans", headers=_auth(token), json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _contribute(client: TestClient, token: str, plan_id: str, **over) -> dict:
    body = {"amount": 500, "note": "第一笔"}
    body.update(over)
    res = client.post(
        f"/api/v1/saving-plans/{plan_id}/contribute", headers=_auth(token), json=body
    )
    assert res.status_code == 201, res.text
    return res.json()


def _tx_by_sync_id(client: TestClient, token: str, ledger_id: str, sync_id: str) -> dict | None:
    res = client.get(
        f"/api/v1/read/workspace/transactions",
        headers=_auth(token),
        params={"ledger_id": ledger_id, "tx_sync_id": sync_id, "limit": 1},
    )
    assert res.status_code == 200, res.text
    items = res.json().get("items", [])
    return items[0] if items else None


def test_saving_plan_crud_and_contribution_edit() -> None:
    client = _make_client()
    token = _register(client, "sp-edit@example.com")
    ledger_id = "ledger_sp_edit"
    _create_ledger(client, token, ledger_id)
    acc_id = _create_account(client, token, ledger_id, "工资卡")

    # --- 计划创建 / 列表 ---
    plan = _create_plan(client, token, ledger_id)
    plan_id = plan["id"]
    assert plan["target_amount"] == 10000
    assert plan["saved_amount"] == 0

    res = client.get(
        "/api/v1/saving-plans", headers=_auth(token), params={"ledger_id": ledger_id}
    )
    assert res.status_code == 200
    assert any(p["id"] == plan_id for p in res.json())

    # --- 计划更新(PATCH)---
    res = client.patch(
        f"/api/v1/saving-plans/{plan_id}",
        headers=_auth(token),
        json={"name": "旅行基金 2026", "target_amount": 20000, "note": "改个名"},
    )
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["name"] == "旅行基金 2026"
    assert updated["target_amount"] == 20000
    assert updated["note"] == "改个名"

    # --- 存入一笔(带扣款账户 → 生成支出流水)---
    contrib = _contribute(
        client, token, plan_id, amount=500, account_id=acc_id, account_name="工资卡"
    )
    assert contrib["amount"] == 500
    assert contrib["effective_amount"] == 500
    assert contrib["tx_sync_id"], "带扣款账户的存入应生成流水"
    tx_sync_id = contrib["tx_sync_id"]

    # 已存金额应随之更新
    res = client.get(f"/api/v1/saving-plans/{plan_id}", headers=_auth(token))
    assert res.json()["saved_amount"] == 500

    # --- 修改存入金额 → 关联流水金额同步改 ---
    res = client.patch(
        f"/api/v1/saving-plans/contributions/{contrib['id']}",
        headers=_auth(token),
        json={"amount": 800},
    )
    assert res.status_code == 200, res.text
    edited = res.json()
    assert edited["amount"] == 800
    # 关键断言:有效金额(从流水派生)也跟着变成 800
    assert edited["effective_amount"] == 800, (
        f"有效金额未跟随: {edited['effective_amount']} != 800"
    )
    # 已存金额同步
    res = client.get(f"/api/v1/saving-plans/{plan_id}", headers=_auth(token))
    assert res.json()["saved_amount"] == 800

    # 关联流水的金额也要同步改了
    tx = _tx_by_sync_id(client, token, ledger_id, tx_sync_id)
    assert tx is not None, "关联流水不应被删"
    assert tx["amount"] == 800, f"关联流水金额未同步: {tx['amount']}"

    # --- 修改备注 / 时间 / 分类 / 统计开关 ---
    res = client.patch(
        f"/api/v1/saving-plans/contributions/{contrib['id']}",
        headers=_auth(token),
        json={
            "note": "改成旅游基金",
            "category_name": "旅行",
            "exclude_from_stats": True,
            "exclude_from_budget": True,
            "happened_at": "2026-01-15T08:30:00Z",
        },
    )
    assert res.status_code == 200, res.text
    edited2 = res.json()
    assert edited2["note"] == "改成旅游基金"
    assert edited2["category_name"] == "旅行"
    assert edited2["exclude_from_stats"] is True
    assert edited2["exclude_from_budget"] is True
    assert edited2["happened_at"].startswith("2026-01-15T08:30")

    # --- 仅记录的存入(无关联流水)也能改 ---
    plan2 = _create_plan(client, token, ledger_id, name="纯记录")
    contrib2 = _contribute(client, token, plan2["id"], amount=100)
    assert contrib2["tx_sync_id"] is None
    res = client.patch(
        f"/api/v1/saving-plans/contributions/{contrib2['id']}",
        headers=_auth(token),
        json={"amount": 250},
    )
    assert res.status_code == 200, res.text
    assert res.json()["effective_amount"] == 250

    # --- 删除存入记录 → 关联流水一并删除 ---
    res = client.delete(
        f"/api/v1/saving-plans/contributions/{contrib['id']}", headers=_auth(token)
    )
    assert res.status_code == 204, res.text
    # 流水不应残留
    assert _tx_by_sync_id(client, token, ledger_id, tx_sync_id) is None, (
        "删除存入记录后关联流水仍残留(孤儿交易)"
    )
    # 已存金额回落
    res = client.get(f"/api/v1/saving-plans/{plan_id}", headers=_auth(token))
    assert res.json()["saved_amount"] == 0

    # --- 删除计划 → 其存入记录 + 流水一并删除 ---
    plan3 = _create_plan(client, token, ledger_id, name="待删")
    c3 = _contribute(client, token, plan3["id"], amount=300, account_id=acc_id)
    tx3 = c3["tx_sync_id"]
    assert tx3
    res = client.delete(f"/api/v1/saving-plans/{plan3['id']}", headers=_auth(token))
    assert res.status_code == 204, res.text
    assert _tx_by_sync_id(client, token, ledger_id, tx3) is None, (
        "删除计划后其流水仍残留(孤儿交易)"
    )
    res = client.get(
        "/api/v1/saving-plans", headers=_auth(token), params={"ledger_id": ledger_id}
    )
    assert all(p["id"] != plan3["id"] for p in res.json())


def test_saving_plan_contribution_edit_keeps_tx_fields() -> None:
    """只改金额时,流水上其它字段(备注/分类/账户)不能被抹掉。

    upsert_tx 会把 payload 的所有字段写回投影 —— 如果同步流水时只带了
    金额,其余字段就会变成 NULL。所以同步必须用**完整**的当前快照。
    """
    client = _make_client()
    token = _register(client, "sp-keep@example.com")
    ledger_id = "ledger_sp_keep"
    _create_ledger(client, token, ledger_id)
    acc_id = _create_account(client, token, ledger_id, "工资卡")

    plan = _create_plan(client, token, ledger_id, name="保留字段")
    contrib = _contribute(
        client,
        token,
        plan["id"],
        amount=500,
        account_id=acc_id,
        account_name="工资卡",
        note="原始备注",
        category_name="工资",
    )
    tx_sync_id = contrib["tx_sync_id"]
    assert tx_sync_id

    # 只改金额,不动其它字段
    res = client.patch(
        f"/api/v1/saving-plans/contributions/{contrib['id']}",
        headers=_auth(token),
        json={"amount": 600},
    )
    assert res.status_code == 200, res.text

    tx = _tx_by_sync_id(client, token, ledger_id, tx_sync_id)
    assert tx is not None
    assert tx["amount"] == 600
    # 备注 / 分类 / 账户都应保留
    assert tx.get("note") == "原始备注", f"流水备注被抹掉: {tx.get('note')}"
    assert tx.get("category_name") == "工资", f"流水分类被抹掉: {tx.get('category_name')}"
    assert tx.get("account_name") == "工资卡", f"流水账户被抹掉: {tx.get('account_name')}"


def test_saving_plan_edit_other_user_denied() -> None:
    """别人的存入记录不能改 / 删。"""
    client = _make_client()
    token_a = _register(client, "sp-a@example.com")
    token_b = _register(client, "sp-b@example.com")
    ledger_id = "ledger_sp_owner"
    _create_ledger(client, token_a, ledger_id)
    plan = _create_plan(client, token_a, ledger_id)
    contrib = _contribute(client, token_a, plan["id"], amount=100)

    res = client.patch(
        f"/api/v1/saving-plans/contributions/{contrib['id']}",
        headers=_auth(token_b),
        json={"amount": 999},
    )
    assert res.status_code == 404, res.text

    res = client.delete(
        f"/api/v1/saving-plans/contributions/{contrib['id']}", headers=_auth(token_b)
    )
    assert res.status_code == 404, res.text

    res = client.patch(
        f"/api/v1/saving-plans/{plan['id']}",
        headers=_auth(token_b),
        json={"name": "被篡改"},
    )
    assert res.status_code == 404, res.text


def test_contribute_with_two_accounts_creates_single_transfer() -> None:
    """同时选转出账户 + 转入账户 → 只记**一笔转账**,不是一笔支出 + 一笔收入(0057)。

    真实银行流水就是一笔转账;拆成两笔会让收支笔数虚增。
    """
    client = _make_client()
    token = _register(client, "sp-transfer@example.com")
    ledger_id = "ledger_sp_transfer"
    _create_ledger(client, token, ledger_id)
    src = _create_account(client, token, ledger_id, "工资卡")
    dst = _create_account(client, token, ledger_id, "储蓄卡")

    plan = _create_plan(client, token, ledger_id)
    contrib = _contribute(
        client, token, plan["id"],
        amount=800,
        account_id=src,
        receive_account_id=dst,
    )

    # 只有一笔关联流水,且是转账
    assert contrib["tx_sync_id"], contrib
    assert not contrib["receive_tx_sync_id"], contrib
    assert len(contrib["tx_sync_details"]) == 1, contrib
    tx = contrib["tx_sync_details"][0]
    assert tx["tx_type"] == "transfer", tx
    assert abs(float(tx["amount"]) - 800) < 0.005, tx
    # 账户信息:转出 = 扣款账户,转入 = 收款账户
    assert contrib["account_id"] == src, contrib
    assert contrib["receive_account_id"] == dst, contrib

    # 流水投影里确实只有这一笔转账(没有多出支出/收入两条)
    res = client.get(
        "/api/v1/read/workspace/transactions",
        headers=_auth(token),
        params={"ledger_id": ledger_id, "limit": 50},
    )
    items = res.json().get("items", [])
    mine = [t for t in items if t["id"] == contrib["tx_sync_id"]]
    assert len(mine) == 1, items
    assert mine[0]["tx_type"] == "transfer", mine[0]
    assert mine[0].get("from_account_name") == "工资卡", mine[0]
    assert mine[0].get("to_account_name") == "储蓄卡", mine[0]

    # 已存金额按这笔转账的金额计入一次(不重复计)
    res = client.get(
        "/api/v1/saving-plans", headers=_auth(token), params={"ledger_id": ledger_id}
    )
    saved = next(p for p in res.json() if p["id"] == plan["id"])["saved_amount"]
    assert abs(saved - 800) < 0.005, saved


def test_contribute_can_link_existing_transaction() -> None:
    """关联已有交易 → 不再生成新流水,储蓄罐只做归属(0057)。"""
    client = _make_client()
    token = _register(client, "sp-link@example.com")
    ledger_id = "ledger_sp_link"
    _create_ledger(client, token, ledger_id)
    acc = _create_account(client, token, ledger_id, "现金")

    # 先记一笔普通支出
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=_auth(token),
        json={
            "base_change_id": 0, "tx_type": "expense", "amount": 66.0,
            "happened_at": datetime.now(timezone.utc).isoformat(),
            "category_name": "餐饮", "account_id": acc, "account_name": "现金",
            "note": "已经记过的一笔",
        },
    )
    assert res.status_code in (200, 201), res.text
    existing_tx = res.json()["entity_id"]

    plan = _create_plan(client, token, ledger_id)
    contrib = _contribute(
        client, token, plan["id"], amount=66.0, tx_sync_id=existing_tx
    )
    assert contrib["tx_sync_id"] == existing_tx, contrib
    assert not contrib["receive_tx_sync_id"], contrib

    # 没有多出第二笔流水:账本里仍只有那一笔
    res = client.get(
        "/api/v1/read/workspace/transactions",
        headers=_auth(token),
        params={"ledger_id": ledger_id, "limit": 50},
    )
    items = res.json().get("items", [])
    assert len(items) == 1, items
    assert items[0]["id"] == existing_tx, items

    # 已存金额取自那笔流水
    res = client.get(
        "/api/v1/saving-plans", headers=_auth(token), params={"ledger_id": ledger_id}
    )
    saved = next(p for p in res.json() if p["id"] == plan["id"])["saved_amount"]
    assert abs(saved - 66.0) < 0.005, saved


def test_contribute_link_nonexistent_transaction_rejected() -> None:
    """关联不存在的流水 → 404,不静默生成新流水。"""
    client = _make_client()
    token = _register(client, "sp-linkbad@example.com")
    ledger_id = "ledger_sp_linkbad"
    _create_ledger(client, token, ledger_id)
    plan = _create_plan(client, token, ledger_id)
    res = client.post(
        f"/api/v1/saving-plans/{plan['id']}/contribute",
        headers=_auth(token),
        json={"amount": 100, "tx_sync_id": "tx_not_exist"},
    )
    assert res.status_code == 404, res.text
