"""流水关联反查(0043)测试。

验证:一条流水被业务实体引用后,能在 /read/transactions/{id}/links 查到关联项,
并带正确的跳转目标(target.page / target.xxx_id)。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

NOW = datetime.now(timezone.utc)


def _make_client() -> TestClient:
    from src.database import Base, get_db
    from src.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

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


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "123456", "client_type": "web",
              "device_name": "t", "platform": "web"},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()


def _ledger(client: TestClient, token: str, ledger_id: str) -> None:
    res = client.post(
        "/api/v1/write/ledgers", headers=_auth(token),
        json={"ledger_id": ledger_id, "ledger_name": ledger_id, "currency": "CNY",
              "month_start_day": 1},
    )
    assert res.status_code == 200, res.text


def _account(client: TestClient, token: str, ledger_id: str, name: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/accounts", headers=_auth(token),
        json={"base_change_id": 0, "name": name},
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _tx(client: TestClient, token: str, ledger_id: str, amount: float, note: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=_auth(token),
        json={"base_change_id": 0, "tx_type": "expense", "amount": amount,
              "happened_at": NOW.isoformat(), "category_name": "购物", "note": note},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["entity_id"]


def _links(client: TestClient, token: str, tx_id: str, ledger_id: str) -> dict:
    res = client.get(
        f"/api/v1/read/transactions/{tx_id}/links?ledger_id={ledger_id}",
        headers=_auth(token),
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_loan_flow_has_link_to_loan() -> None:
    """借出流水 → 借款详情;收回借款流水 → 借款详情。"""
    client = _make_client()
    user = _register(client, "linksloan@example.com")
    token = user["access_token"]
    _ledger(client, token, "LNK")
    acc = _account(client, token, "LNK", "现金")

    res = client.post(
        "/api/v1/loans", headers=_auth(token),
        json={"ledger_id": "LNK", "direction": "lend", "amount": 100.0,
              "happened_at": NOW.isoformat(), "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    loan = res.json()

    data = _links(client, token, loan["tx_sync_id"], "LNK")
    assert data["count"] >= 1, data
    kinds = {x["kind"] for x in data["links"]}
    assert "loan" in kinds, data
    link = next(x for x in data["links"] if x["kind"] == "loan")
    assert link["target"]["page"] == "loans"
    assert link["target"]["loan_id"] == loan["id"]
    assert abs(link["amount"] - 100.0) < 0.005


def test_repayment_flow_has_link_to_loan() -> None:
    """收回借款流水 → 借款详情。"""
    client = _make_client()
    user = _register(client, "linksrepay@example.com")
    token = user["access_token"]
    _ledger(client, token, "LNR")
    acc = _account(client, token, "LNR", "现金")

    res = client.post(
        "/api/v1/loans", headers=_auth(token),
        json={"ledger_id": "LNR", "direction": "lend", "amount": 100.0,
              "happened_at": NOW.isoformat(), "account_id": acc, "account_name": "现金"},
    )
    loan = res.json()
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 50.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    rp = res.json()

    data = _links(client, token, rp["tx_sync_id"], "LNR")
    assert data["count"] >= 1, data
    link = next(x for x in data["links"] if x["kind"] == "loan")
    assert link["target"]["loan_id"] == loan["id"]


def test_saving_flow_has_link_to_plan() -> None:
    """储蓄罐存入流水 → 储蓄罐详情。"""
    client = _make_client()
    user = _register(client, "linkssave@example.com")
    token = user["access_token"]
    _ledger(client, token, "LNS")
    acc = _account(client, token, "LNS", "现金")

    res = client.post(
        "/api/v1/saving-plans", headers=_auth(token),
        json={"ledger_id": "LNS", "name": "旅行基金", "target_amount": 10000.0,
              "currency_code": "CNY"},
    )
    assert res.status_code == 201, res.text
    plan = res.json()
    res = client.post(
        f"/api/v1/saving-plans/{plan['id']}/contribute", headers=_auth(token),
        json={"amount": 100.0, "happened_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    contrib = res.json()
    assert contrib["tx_sync_id"]

    data = _links(client, token, contrib["tx_sync_id"], "LNS")
    assert data["count"] >= 1, data
    link = next(x for x in data["links"] if x["kind"] == "saving_plan")
    assert link["target"]["page"] == "saving-plans"
    assert link["target"]["plan_id"] == plan["id"]


def test_item_and_reimbursement_flows_have_links() -> None:
    """物品关联流水 → 物品详情;报销项流水 → 报销详情。"""
    client = _make_client()
    user = _register(client, "linksitem@example.com")
    token = user["access_token"]
    _ledger(client, token, "LNI")
    acc = _account(client, token, "LNI", "现金")

    # 物品关联一笔已有交易
    tx_id = _tx(client, token, "LNI", 100.0, "购入配件")
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "LNI", "name": "配件", "price": 100.0,
              "link_mode": "existing", "tx_sync_ids": [tx_id]},
    )
    assert res.status_code in (200, 201), res.text
    item = res.json()

    data = _links(client, token, tx_id, "LNI")
    assert data["count"] >= 1, data
    link = next(x for x in data["links"] if x["kind"] == "item")
    assert link["target"]["page"] == "items"
    assert link["target"]["item_id"] == item["id"]

    # 报销项
    res = client.post(
        "/api/v1/companies", headers=_auth(token), json={"name": "测试公司"},
    )
    assert res.status_code == 201, res.text
    company_id = res.json()["id"]
    tx2 = _tx(client, token, "LNI", 80.0, "打车费")
    res = client.post(
        "/api/v1/reimbursements/items", headers=_auth(token),
        json={"ledger_id": "LNI", "tx_sync_id": tx2, "amount": 80.0,
              "happened_at": NOW.isoformat(), "account_id": acc, "account_name": "现金",
              "company_id": company_id},
    )
    assert res.status_code == 201, res.text
    rb_item = res.json()

    data2 = _links(client, token, tx2, "LNI")
    assert data2["count"] >= 1, data2
    link2 = next(x for x in data2["links"] if x["kind"] == "reimbursement")
    assert link2["target"]["page"] == "reimbursements"
    assert link2["target"]["reimbursement_item_id"] == rb_item["id"]


def test_unrelated_tx_has_no_links() -> None:
    """普通交易没有关联 → count = 0。"""
    client = _make_client()
    user = _register(client, "linksnone@example.com")
    token = user["access_token"]
    _ledger(client, token, "LNU")
    tx_id = _tx(client, token, "LNU", 50.0, "普通消费")
    data = _links(client, token, tx_id, "LNU")
    assert data["count"] == 0, data


def _member(client: TestClient, token: str, ledger_id: str, name: str) -> str:
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/members", headers=_auth(token),
        json={"base_change_id": 0, "name": name},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["entity_id"]


def test_member_flow_has_link_to_member() -> None:
    """带成员归属的收支流水 → 成员详情(0055)。

    覆盖三种口径:
      - 单成员关联(整笔金额算这个成员);
      - 多成员分摊(金额取该成员分摊到的那一份);
      - 未关联成员的流水不产生 member 入口。
    """
    client = _make_client()
    user = _register(client, "linksmember@example.com")
    token = user["access_token"]
    _ledger(client, token, "LNM")
    acc = _account(client, token, "LNM", "现金")
    member_a = _member(client, token, "LNM", "爸爸")
    member_b = _member(client, token, "LNM", "妈妈")

    # 1) 单成员关联:金额就是这一笔的全部
    res = client.post(
        f"/api/v1/write/ledgers/LNM/transactions", headers=_auth(token),
        json={"base_change_id": 0, "tx_type": "expense", "amount": 100.0,
              "happened_at": NOW.isoformat(), "category_name": "餐饮",
              "account_id": acc, "account_name": "现金",
              "member_splits": [{"member_id": member_a, "name": "爸爸", "amount": 100.0}]},
    )
    assert res.status_code in (200, 201), res.text
    tx_single = res.json()["entity_id"]

    data = _links(client, token, tx_single, "LNM")
    member_links = [x for x in data["links"] if x["kind"] == "member"]
    assert len(member_links) == 1, data
    link = member_links[0]
    assert link["target"]["page"] == "members"
    assert link["target"]["member_id"] == member_a
    assert "爸爸" in link["title"], link
    assert abs(link["amount"] - 100.0) < 0.005, link

    # 2) 多成员分摊:每个成员一个入口,金额是各自分摊到的那一份
    res = client.post(
        f"/api/v1/write/ledgers/LNM/transactions", headers=_auth(token),
        json={"base_change_id": 0, "tx_type": "expense", "amount": 90.0,
              "happened_at": NOW.isoformat(), "category_name": "餐饮",
              "account_id": acc, "account_name": "现金",
              "member_splits": [
                  {"member_id": member_a, "name": "爸爸", "amount": 60.0},
                  {"member_id": member_b, "name": "妈妈", "amount": 30.0},
              ]},
    )
    assert res.status_code in (200, 201), res.text
    tx_split = res.json()["entity_id"]

    data = _links(client, token, tx_split, "LNM")
    member_links = {x["target"]["member_id"]: x for x in data["links"] if x["kind"] == "member"}
    assert len(member_links) == 2, data
    assert abs(member_links[member_a]["amount"] - 60.0) < 0.005, member_links[member_a]
    assert abs(member_links[member_b]["amount"] - 30.0) < 0.005, member_links[member_b]
    assert "分摊" in member_links[member_a]["subtitle"], member_links[member_a]

    # 3) 未关联成员的流水不产生 member 入口
    tx_none = _tx(client, token, "LNM", 20.0, "无成员支出")
    data = _links(client, token, tx_none, "LNM")
    assert not [x for x in data["links"] if x["kind"] == "member"], data
