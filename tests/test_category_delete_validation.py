"""Server-side guards on `delete_category`:
  - 拒绝删除有子分类的父分类(parent_name 引用)
  - 拒绝删除被任何 tx 引用的分类(categoryName + categoryKind)

跟 mobile/AccountsPage 的 delete-blocked 策略对齐 — 防止 orphan 数据污染。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app


def _make_client() -> TestClient:
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
    return TestClient(app)


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
    assert res.status_code == 200
    return int(res.json()["source_change_id"])


def _create_category(
    client: TestClient,
    token: str,
    ledger_id: str,
    *,
    name: str,
    kind: str = "expense",
    level: int = 1,
    parent_name: str | None = None,
) -> str:
    base = _latest_change_id(client, token, ledger_id)
    payload = {
        "base_change_id": base,
        "name": name,
        "kind": kind,
        "level": level,
        "icon": "category",
        "icon_type": "material",
    }
    if parent_name:
        payload["parent_name"] = parent_name
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/categories",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert res.status_code == 200, res.text
    # 拉列表反查刚建的 sync_id
    listing = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/categories",
        headers={"Authorization": f"Bearer {token}"},
    )
    cats = listing.json()
    matched = next((c for c in cats if c["name"] == name and c["kind"] == kind), None)
    assert matched, f"created category not found: {name}"
    return matched["id"]


def _create_tx(
    client: TestClient,
    token: str,
    ledger_id: str,
    *,
    amount: float,
    category_name: str,
    category_kind: str,
    happened_at: str,
) -> None:
    base = _latest_change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/transactions",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "base_change_id": base,
            "tx_type": category_kind,  # expense / income
            "amount": amount,
            "happened_at": happened_at,
            "category_name": category_name,
            "category_kind": category_kind,
        },
    )
    assert res.status_code == 200, res.text


def _delete_category(
    client: TestClient, token: str, ledger_id: str, category_id: str
) -> tuple[int, str]:
    base = _latest_change_id(client, token, ledger_id)
    res = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/{ledger_id}/categories/{category_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base},
    )
    return res.status_code, res.text


def test_delete_category_with_child_categories_is_rejected() -> None:
    """父分类下还有子分类 → 拒删,要求用户先删子分类"""
    client = _make_client()
    try:
        owner = _register(client, "cat-del-child@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_CAT_CHILD"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "cat-del-child@example.com")
        token = web["access_token"]

        # 父分类 + 一个子分类(parent_name 引用父)
        parent_id = _create_category(
            client, token, ledger_id, name="餐饮", kind="expense", level=1
        )
        _create_category(
            client,
            token,
            ledger_id,
            name="早餐",
            kind="expense",
            level=2,
            parent_name="餐饮",
        )

        # 删父分类应该 400/422,带"child categories" 错误
        status, text = _delete_category(client, token, ledger_id, parent_id)
        assert status >= 400, f"expected reject, got {status}: {text}"
        assert "child" in text.lower() or "subcateg" in text.lower() or "校验" in text, (
            f"expected child-category error, got: {text}"
        )
    finally:
        app.dependency_overrides.clear()


def test_delete_category_with_referencing_transactions_is_rejected() -> None:
    """分类被 tx 引用(categoryName + categoryKind)→ 拒删"""
    client = _make_client()
    try:
        owner = _register(client, "cat-del-tx@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_CAT_TX"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "cat-del-tx@example.com")
        token = web["access_token"]

        cat_id = _create_category(
            client, token, ledger_id, name="交通", kind="expense", level=1
        )
        # 创建一笔引用该分类的交易
        now_iso = datetime.now(timezone.utc).isoformat()
        _create_tx(
            client,
            token,
            ledger_id,
            amount=12.5,
            category_name="交通",
            category_kind="expense",
            happened_at=now_iso,
        )

        status, text = _delete_category(client, token, ledger_id, cat_id)
        assert status >= 400, f"expected reject, got {status}: {text}"
        assert "transaction" in text.lower() or "校验" in text or "tx" in text.lower(), (
            f"expected tx-reference error, got: {text}"
        )
    finally:
        app.dependency_overrides.clear()


def test_delete_unused_leaf_category_succeeds() -> None:
    """无子分类 + 无 tx 引用 → 允许删除(回归校验:正常路径不被误伤)"""
    client = _make_client()
    try:
        owner = _register(client, "cat-del-ok@example.com")
        app_token, device = owner["access_token"], owner["device_id"]
        ledger_id = "L_CAT_OK"
        _seed_ledger(client, app_token, device, ledger_id)

        web = _login_web(client, "cat-del-ok@example.com")
        token = web["access_token"]

        cat_id = _create_category(
            client, token, ledger_id, name="临时分类", kind="expense", level=1
        )

        status, _text = _delete_category(client, token, ledger_id, cat_id)
        assert status == 200, f"expected delete success, got {status}: {_text}"

        # 确认确实删了
        listing = client.get(
            f"/api/v1/read/ledgers/{ledger_id}/categories",
            headers={"Authorization": f"Bearer {token}"},
        )
        cats = listing.json()
        assert not any(c["name"] == "临时分类" for c in cats), "category should be gone"
    finally:
        app.dependency_overrides.clear()
