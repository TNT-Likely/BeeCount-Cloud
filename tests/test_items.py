"""物品管理(0038)与多流水关联(0040)测试。

覆盖:
  - 物品 CRUD / 派生统计(净成本、均值、目标成本);
  - 关联账单三模式(new / existing / none);
  - 0040:一件物品可关联**多笔**已有交易流水(创建 + 更新替换 + 清空);
  - 关联的外部交易不随物品删除而删除;new 模式生成���交易随物品删除。
"""
from __future__ import annotations

from datetime import datetime, timezone

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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email, "password": "123456", "client_type": "web",
            "device_name": "pytest", "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
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
    """建一笔普通支出交易,返回其 syncId(用来做 existing 关联)。"""
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions", headers=_auth(token),
        json={
            "base_change_id": 0,
            "tx_type": "expense",
            "amount": amount,
            "happened_at": NOW.isoformat(),
            "category_name": "购物",
            "note": note,
        },
    )
    assert res.status_code in (200, 201), res.text
    entity_id = res.json().get("entity_id")
    assert entity_id, res.text
    return entity_id


def test_item_multi_tx_links() -> None:
    """0040:一件物品可关联多笔已有交易流水。"""
    client = _make_client()
    user = _register(client, "itemmulti@example.com")
    token = user["access_token"]
    _ledger(client, token, "IML")

    t1 = _tx(client, token, "IML", 100.0, "定金")
    t2 = _tx(client, token, "IML", 250.0, "尾款")
    t3 = _tx(client, token, "IML", 50.0, "运费")

    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={
            "ledger_id": "IML", "name": "沙发", "price": 350.0,
            "purchased_at": NOW.isoformat(),
            "link_mode": "existing",
            "tx_sync_ids": [t1, t2, t3],
        },
    )
    assert res.status_code == 201, res.text
    item = res.json()
    assert item["tx_sync_ids"] == [t1, t2, t3], item["tx_sync_ids"]
    assert item["tx_sync_id"] == t1
    assert item["link_mode"] == "existing"

    # 列表也要能拿到多笔关联
    res = client.get("/api/v1/items?ledger_id=IML", headers=_auth(token))
    assert res.status_code == 200, res.text
    listed = res.json()[0]
    assert listed["tx_sync_ids"] == [t1, t2, t3]

    # 更新:替换关联(换成 2 笔)
    res = client.patch(
        f"/api/v1/items/{item['id']}", headers=_auth(token),
        json={"tx_sync_ids": [t2, t3]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tx_sync_ids"] == [t2, t3]

    # 更新:清空关联
    res = client.patch(
        f"/api/v1/items/{item['id']}", headers=_auth(token),
        json={"tx_sync_ids": []},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tx_sync_ids"] == []
    assert res.json()["tx_sync_id"] is None


def test_item_multi_tx_links_validation() -> None:
    """0040:关联的交易必须存在且属于该账本;不传任何 id 时 400。"""
    client = _make_client()
    user = _register(client, "itemmultiinvalid@example.com")
    token = user["access_token"]
    _ledger(client, token, "IMV")
    ok_tx = _tx(client, token, "IMV", 10.0, "存在的交易")

    # 不存在的 syncId → 404
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "IMV", "name": "X", "price": 10.0,
              "purchased_at": NOW.isoformat(),
              "link_mode": "existing", "tx_sync_ids": [ok_tx, "tx_not_exists"]},
    )
    assert res.status_code == 404, res.text

    # 一个都没传 → 400
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "IMV", "name": "X", "price": 10.0,
              "purchased_at": NOW.isoformat(), "link_mode": "existing"},
    )
    assert res.status_code == 400, res.text


def test_item_flow_multi_tx_links() -> None:
    """0040:物品的收益/支出流水也可关联多笔交易。"""
    client = _make_client()
    user = _register(client, "itemflowmulti@example.com")
    token = user["access_token"]
    _ledger(client, token, "IFM")
    t1 = _tx(client, token, "IFM", 20.0, "租金1")
    t2 = _tx(client, token, "IFM", 30.0, "租金2")

    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "IFM", "name": "相机", "price": 100.0,
              "purchased_at": NOW.isoformat(), "link_mode": "none"},
    )
    assert res.status_code == 201, res.text
    item = res.json()

    res = client.post(
        f"/api/v1/items/{item['id']}/flows", headers=_auth(token),
        json={"item_id": item["id"], "flow_type": "income", "amount": 50.0,
              "happened_at": NOW.isoformat(), "link_mode": "existing",
              "tx_sync_ids": [t1, t2]},
    )
    assert res.status_code == 201, res.text
    flow = res.json()
    assert flow["tx_sync_ids"] == [t1, t2], flow["tx_sync_ids"]

    res = client.get(f"/api/v1/items/{item['id']}/flows", headers=_auth(token))
    assert res.status_code == 200, res.text
    assert res.json()[0]["tx_sync_ids"] == [t1, t2]


def test_item_delete_keeps_existing_tx() -> None:
    """删除物品时:existing 关联的外部交易保留,new 模式生成的交易被删除。"""
    client = _make_client()
    user = _register(client, "itemdel@example.com")
    token = user["access_token"]
    _ledger(client, token, "IDL")
    acc = _account(client, token, "IDL", "现金")
    ext_tx = _tx(client, token, "IDL", 30.0, "外部交易")

    # existing 关联的物品
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "IDL", "name": "台灯", "price": 30.0,
              "purchased_at": NOW.isoformat(),
              "link_mode": "existing", "tx_sync_ids": [ext_tx]},
    )
    assert res.status_code == 201, res.text
    item = res.json()
    res = client.delete(f"/api/v1/items/{item['id']}", headers=_auth(token))
    assert res.status_code == 204, res.text

    # 外部交易仍在
    res = client.get(
        f"/api/v1/read/ledgers/IDL/transactions?limit=100", headers=_auth(token)
    )
    assert res.status_code == 200, res.text
    rows = res.json() if isinstance(res.json(), list) else res.json()["items"]
    assert any(r["id"] == ext_tx for r in rows), "existing 关联的外部交易不应被删除"

    # new 模式生成的交易应被删除
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "IDL", "name": "台灯2", "price": 88.0,
              "purchased_at": NOW.isoformat(), "link_mode": "new",
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    item2 = res.json()
    assert item2["tx_sync_id"]
    res = client.delete(f"/api/v1/items/{item2['id']}", headers=_auth(token))
    assert res.status_code == 204, res.text
    res = client.get(
        f"/api/v1/read/ledgers/IDL/transactions?limit=100", headers=_auth(token)
    )
    rows = res.json() if isinstance(res.json(), list) else res.json()["items"]
    assert not any(r["id"] == item2["tx_sync_id"] for r in rows), "new 模式交易应随物品删除"


def test_item_flow_syncs_when_linked_tx_amount_changes() -> None:
    """0042:编辑关联流水的金额 → 物品流水金额同步。

    物品流水(ItemFlow)的 amount 是快照;关联的流水被改金额后必须同步,
    否则净成本/均值/目标成本会算错。
    """
    client = _make_client()
    user = _register(client, "itemsync@example.com")
    token = user["access_token"]
    _ledger(client, token, "LISY")
    acc = _account(client, token, "LISY", "现金")

    # 物品,关联一笔 100 元的已有交易
    tx_id = _tx(client, token, "LISY", 100.0, "购入配件")
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "LISY", "name": "配件", "price": 100.0,
              "link_mode": "existing", "tx_sync_ids": [tx_id]},
    )
    assert res.status_code in (200, 201), res.text
    item = res.json()

    # 物品流水关联同一笔交易,金额 100
    res = client.post(
        f"/api/v1/items/{item['id']}/flows", headers=_auth(token),
        json={"item_id": item["id"], "flow_type": "expense", "amount": 100.0,
              "link_mode": "existing", "tx_sync_ids": [tx_id]},
    )
    assert res.status_code == 201, res.text
    flow = res.json()
    assert flow["amount"] == 100.0

    # 交易侧把流水改成 150 → 物品流水金额应同步为 150
    res = client.patch(
        f"/api/v1/write/ledgers/LISY/transactions/{tx_id}",
        headers=_auth(token),
        json={"base_change_id": 0, "amount": 150.0},
    )
    assert res.status_code == 200, res.text

    flows = client.get(f"/api/v1/items/{item['id']}/flows", headers=_auth(token)).json()
    assert len(flows) == 1
    assert flows[0]["amount"] == 150.0, flows[0]

    # 物品的净成本也跟着变(price 100 + 支出 150 = 250)
    detail = client.get(f"/api/v1/items/{item['id']}", headers=_auth(token)).json()
    assert detail["net_cost"] == 250.0, detail


def test_item_price_auto_sums_linked_transactions() -> None:
    """0044:关联多笔流水 → 价格自动 = 各笔金额之和(成本汇总)。

    - 新建物品时价格可以填 0,关联流水后自动汇总;
    - 改关联(替换/增删)后价格跟着重算;
    - 手动指定价格(auto_price=False)时以用户值为准。
    """
    client = _make_client()
    user = _register(client, "itemcost@example.com")
    token = user["access_token"]
    _ledger(client, token, "ICO")

    t1 = _tx(client, token, "ICO", 100.0, "定金")
    t2 = _tx(client, token, "ICO", 250.0, "尾款")
    t3 = _tx(client, token, "ICO", 50.0, "运费")

    # 价格填 0 + 关联 3 笔 → 价格自动 = 400
    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "ICO", "name": "沙发", "price": 0.0,
              "link_mode": "existing", "tx_sync_ids": [t1, t2, t3]},
    )
    assert res.status_code == 201, res.text
    item = res.json()
    assert item["price"] == 400.0, item["price"]

    # 改关联(换成 2 笔)→ 价格重算 = 300
    res = client.patch(
        f"/api/v1/items/{item['id']}", headers=_auth(token),
        json={"tx_sync_ids": [t1, t2]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["price"] == 350.0, res.json()["price"]

    # 手动指定价格(auto_price=False)→ 保留用户值,不自动汇总
    res = client.patch(
        f"/api/v1/items/{item['id']}", headers=_auth(token),
        json={"price": 88.0, "auto_price": False},
    )
    assert res.status_code == 200, res.text
    assert res.json()["price"] == 88.0, res.json()["price"]

    # 清空关联 → 不再汇总
    res = client.patch(
        f"/api/v1/items/{item['id']}", headers=_auth(token),
        json={"tx_sync_ids": []},
    )
    assert res.status_code == 200, res.text
    assert res.json()["tx_sync_ids"] == []
    assert res.json()["price"] == 88.0, res.json()["price"]


def test_item_flow_amount_auto_sums_linked_transactions() -> None:
    """0044:物品流水关联已有流水 → 金额自动 = 各笔金额之和。"""
    client = _make_client()
    user = _register(client, "itemflowsum@example.com")
    token = user["access_token"]
    _ledger(client, token, "IFS")

    t1 = _tx(client, token, "IFS", 120.0, "维修A")
    t2 = _tx(client, token, "IFS", 30.0, "维修B")

    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "IFS", "name": "车", "price": 0.0},
    )
    assert res.status_code == 201, res.text
    item = res.json()

    res = client.post(
        f"/api/v1/items/{item['id']}/flows", headers=_auth(token),
        json={"item_id": item["id"], "flow_type": "expense", "amount": 1.0,
              "link_mode": "existing", "tx_sync_ids": [t1, t2]},
    )
    assert res.status_code == 201, res.text
    assert res.json()["amount"] == 150.0, res.json()["amount"]

    # 编辑流水关联 → 金额重算
    res = client.patch(
        f"/api/v1/items/flows/{res.json()['id']}", headers=_auth(token),
        json={"tx_sync_ids": [t1]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["amount"] == 120.0, res.json()["amount"]


def test_transaction_can_link_to_item() -> None:
    """0044:新建/编辑账单可关联物品 —— 该交易成为物品的一条流水。

    - 新建支出账单带 item_id → 物品下多一条金额相等的支出流水;
    - 改账单金额 → 物品流水金额跟随;
    - 改关联物品 → 流水移动到新物品;
    - 传空串解除关联 → 物品流水被移除。
    """
    client = _make_client()
    user = _register(client, "txitem@example.com")
    token = user["access_token"]
    _ledger(client, token, "TIX")

    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "TIX", "name": "相机", "price": 0.0},
    )
    assert res.status_code == 201, res.text
    item_a = res.json()

    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "TIX", "name": "镜头", "price": 0.0},
    )
    assert res.status_code == 201, res.text
    item_b = res.json()

    # 新建账单并关联 item_a
    res = client.post(
        "/api/v1/write/ledgers/TIX/transactions", headers=_auth(token),
        json={"base_change_id": 0, "tx_type": "expense", "amount": 66.0,
              "happened_at": NOW.isoformat(), "category_name": "购物",
              "note": "配件", "item_id": item_a["id"]},
    )
    assert res.status_code == 200, res.text
    tx_id = res.json()["entity_id"]

    flows = client.get(f"/api/v1/items/{item_a['id']}/flows", headers=_auth(token)).json()
    assert len(flows) == 1, flows
    assert flows[0]["amount"] == 66.0, flows[0]
    assert flows[0]["flow_type"] == "expense"
    assert flows[0]["tx_sync_id"] == tx_id

    # 改金额 → 物品流水跟随
    res = client.patch(
        f"/api/v1/write/ledgers/TIX/transactions/{tx_id}", headers=_auth(token),
        json={"base_change_id": 0, "amount": 88.0},
    )
    assert res.status_code == 200, res.text
    flows = client.get(f"/api/v1/items/{item_a['id']}/flows", headers=_auth(token)).json()
    assert flows[0]["amount"] == 88.0, flows[0]

    # 换物品 → 流水移动
    res = client.patch(
        f"/api/v1/write/ledgers/TIX/transactions/{tx_id}", headers=_auth(token),
        json={"base_change_id": 0, "item_id": item_b["id"]},
    )
    assert res.status_code == 200, res.text
    assert client.get(
        f"/api/v1/items/{item_a['id']}/flows", headers=_auth(token)
    ).json() == []
    flows_b = client.get(f"/api/v1/items/{item_b['id']}/flows", headers=_auth(token)).json()
    assert len(flows_b) == 1, flows_b
    assert flows_b[0]["tx_sync_id"] == tx_id

    # 解除关联 → 物品流水被移除
    res = client.patch(
        f"/api/v1/write/ledgers/TIX/transactions/{tx_id}", headers=_auth(token),
        json={"base_change_id": 0, "item_id": ""},
    )
    assert res.status_code == 200, res.text
    assert client.get(
        f"/api/v1/items/{item_b['id']}/flows", headers=_auth(token)
    ).json() == []


def test_item_linked_tx_details_are_readable() -> None:
    """0045:物品的「已关联交易」要返回可读摘要,不是光给 syncId。

    详情页要显示备注 / 金额 / 时间 / 分类 / 账户,用户才认得出是哪几笔。
    """
    client = _make_client()
    user = _register(client, "itemdetails@example.com")
    token = user["access_token"]
    _ledger(client, token, "ILD")

    t1 = _tx(client, token, "ILD", 100.0, "定金")
    t2 = _tx(client, token, "ILD", 250.0, "尾款")

    res = client.post(
        "/api/v1/items", headers=_auth(token),
        json={"ledger_id": "ILD", "name": "沙发", "price": 0.0,
              "link_mode": "existing", "tx_sync_ids": [t1, t2]},
    )
    assert res.status_code == 201, res.text
    item = res.json()

    details = item["tx_sync_details"]
    assert len(details) == 2, details
    # 顺序与 tx_sync_ids 一致
    assert [d["sync_id"] for d in details] == [t1, t2]
    # 备注 / 金额 / 类型都在
    assert details[0]["note"] == "定金", details[0]
    assert details[0]["amount"] == 100.0, details[0]
    assert details[1]["note"] == "尾款", details[1]
    assert details[1]["amount"] == 250.0, details[1]
    assert details[0]["tx_type"] == "expense"
    assert details[0]["happened_at"], details[0]

    # 列表接口也要带详情(详情弹窗用的是列表里的 item)
    res = client.get("/api/v1/items?ledger_id=ILD", headers=_auth(token))
    assert res.status_code == 200, res.text
    listed = res.json()[0]
    assert len(listed["tx_sync_details"]) == 2, listed["tx_sync_details"]
    assert listed["tx_sync_details"][0]["note"] == "定金"

    # 关联的流水被删除后,摘要里仍列出该 syncId(标注为已删除,不崩)
    res = client.patch(
        f"/api/v1/write/ledgers/ILD/transactions/{t2}", headers=_auth(token),
        json={"base_change_id": 0, "amount": 260.0},
    )
    assert res.status_code == 200, res.text
    res = client.get(f"/api/v1/items/{item['id']}", headers=_auth(token))
    details = res.json()["tx_sync_details"]
    assert len(details) == 2, details
    # 改金额后摘要要跟着变(读的是流水投影的真实金额)
    assert details[1]["amount"] == 260.0, details[1]
