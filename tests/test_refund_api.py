"""退款备注联动（0025）API 集成测试。

验证端到端:
  - 通过 web 写接口创建退款时,退款备注 = 原交易备注(后端派生,无后缀)。
  - 原交易(被退)的备注被修改时,关联退款备注自动同步为新备注(无后缀)
    (依赖 routers/write/transactions.update_tx 对"改备注的原单"回退完整路径)。
  - 原交易本身可正常编辑(不会被关联退款卡住)。
  - 退款流水分类统一为「退款」,不沿用原消费单分类。

注:测试套件 conftest 把 ALLOW_APP_RW_SCOPES 设为 false,故写接口要求
SCOPE_WEB_WRITE。这里用 client_type="web" 注册拿到 web 作用域 token,并直接
通过写接口建账本,避免 sync/push(app 作用域)与写接口(web 作用域)的冲突。
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


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "web",
            "device_name": "pytest",
            "platform": "web",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_ledger(client: TestClient, token: str, ledger_id: str) -> None:
    res = client.post(
        "/api/v1/write/ledgers",
        headers=_auth(token),
        json={
            "ledger_id": ledger_id,
            "ledger_name": ledger_id,
            "currency": "CNY",
            "month_start_day": 1,
        },
    )
    assert res.status_code == 200, res.text


def _create_tx(client: TestClient, token: str, ledger_id: str, **body) -> str:
    body.setdefault("base_change_id", 0)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers=_auth(token),
        json=body,
    )
    assert res.status_code == 200, res.text
    return res.json()["entity_id"]


def _list_txns(client: TestClient, token: str, ledger_id: str) -> list[dict]:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/transactions", headers=_auth(token)
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_refund_note_derived_and_original_edit_synced() -> None:
    client = _make_client()
    user = _register(client, "rfapi@example.com")
    token = user["access_token"]
    ledger_id = "RFAPI1"
    _create_ledger(client, token, ledger_id)

    # 原支出(备注 "午餐")
    orig_id = _create_tx(
        client, token, ledger_id,
        tx_type="expense", amount=100.0, happened_at=NOW.isoformat(),
        note="午餐", account_name="支付宝", category_name="餐饮美食",
        category_kind="expense",
    )
    # 退款(不传 note)
    rf_id = _create_tx(
        client, token, ledger_id,
        tx_type="refund", amount=30.0, happened_at=NOW.isoformat(),
        account_name="Cash", refund_of_sync_id=orig_id,
    )

    txns = _list_txns(client, token, ledger_id)
    rf = next(t for t in txns if t["id"] == rf_id)
    orig = next(t for t in txns if t["id"] == orig_id)
    assert rf["tx_type"] == "refund"
    assert rf["note"] == "午餐", rf["note"]
    assert orig["note"] == "午餐"
    # 退款分类统一为「退款」,不沿用原消费单分类(餐饮美食)
    assert rf["category_name"] == "退款", rf["category_name"]
    assert orig["category_name"] == "餐饮美食"

    # 原交易可正常编辑:改备注为 "晚餐"
    res = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/transactions/{orig_id}",
        headers=_auth(token),
        json={"note": "晚餐", "base_change_id": 0},
    )
    assert res.status_code == 200, res.text

    txns = _list_txns(client, token, ledger_id)
    rf = next(t for t in txns if t["id"] == rf_id)
    orig = next(t for t in txns if t["id"] == orig_id)
    assert orig["note"] == "晚餐"
    # 退款备注自动同步
    assert rf["note"] == "晚餐", rf["note"]

    # 再改一次:清空原单备注 → 退款备注也为空
    res = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/transactions/{orig_id}",
        headers=_auth(token),
        json={"note": "", "base_change_id": 0},
    )
    assert res.status_code == 200, res.text
    txns = _list_txns(client, token, ledger_id)
    rf = next(t for t in txns if t["id"] == rf_id)
    assert rf["note"] in ("", None), rf["note"]
