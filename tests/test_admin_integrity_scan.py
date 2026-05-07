"""Smoke + targeted tests for `/admin/integrity/scan`:
  - 全 clean 数据 → 0 issues
  - 数据库直接塞一笔 orphan tx(category_sync_id 指向已删的 cat)→ 命中
    orphan_tx_category
  - 未来时间 / 0 元交易也能命中
  - 普通用户调 endpoint → 403(admin only)

直接通过 SQL session 注入异常数据,绕过正常 write path 的拒绝(因为
delete_category 已加防御不让你 orphan,这里只是测扫描器)。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import (
    Ledger,
    ReadCategoryProjection,
    ReadTxProjection,
    User,
)


def _make_client_with_session():
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


def _grant_admin(TS, email: str) -> None:
    """单元测试里 alembic 没跑,需要手动给用户标 admin。直 SQL 即可。"""
    from src.models import User as _U
    with TS() as db:
        db.execute(update(_U).where(_U.email == email).values(is_admin=True))
        db.commit()


def test_integrity_scan_requires_admin() -> None:
    """普通用户调 /admin/integrity/scan → 403"""
    client, _TS = _make_client_with_session()
    try:
        normal = _register(client, "normal@example.com", client_type="web")
        token = normal["access_token"]

        res = client.get(
            "/api/v1/admin/integrity/scan",
            headers={"Authorization": f"Bearer {token}"},
        )
        # 应该 403/401 - 普通用户没权限
        assert res.status_code in (401, 403), f"expected denied, got {res.status_code}: {res.text}"
    finally:
        app.dependency_overrides.clear()


def test_integrity_scan_detects_orphan_tx_category_and_zero_amount_and_future() -> None:
    """直接 SQL 注入异常数据,扫描器应能命中 orphan_tx_category(name 为空)/
    zero_amount_tx / future_tx 三类。"""
    client, TS = _make_client_with_session()
    try:
        admin = _register(client, "admin-scan@example.com")
        admin_token = admin["access_token"]
        admin_device = admin["device_id"]
        ledger_id = "L_SCAN"
        _seed_ledger(client, admin_token, admin_device, ledger_id)

        # 单元测试里 alembic 没跑,手动 mark admin(否则 require_admin_user → 403)
        _grant_admin(TS, "admin-scan@example.com")

        # 切到 web token 跑 admin endpoint(scope 要求)
        web = _login_web(client, "admin-scan@example.com")
        web_token = web["access_token"]

        # 直接 SQL 注入 3 类异常
        with TS() as db:
            user_row = db.query(User).filter(User.email == "admin-scan@example.com").one()
            ledger_row = db.query(Ledger).filter(Ledger.user_id == user_row.id).first()
            assert ledger_row is not None
            now = datetime.now(timezone.utc)

            # 1. orphan tx — category_name 为 NULL,category_sync_id 悬空
            #    (现新规则:只 name 为空才算"用户感知的孤儿")
            db.add(
                ReadTxProjection(
                    ledger_id=ledger_row.id,
                    sync_id="tx_orphan_1",
                    user_id=user_row.id,
                    tx_type="expense",
                    amount=10.0,
                    happened_at=now,
                    category_sync_id="cat_does_not_exist",
                    category_name=None,
                    category_kind=None,
                    source_change_id=999,
                )
            )
            # 2. 0 元 tx
            db.add(
                ReadTxProjection(
                    ledger_id=ledger_row.id,
                    sync_id="tx_zero_1",
                    user_id=user_row.id,
                    tx_type="expense",
                    amount=0.0,
                    happened_at=now,
                    source_change_id=1000,
                )
            )
            # 3. 未来 tx
            db.add(
                ReadTxProjection(
                    ledger_id=ledger_row.id,
                    sync_id="tx_future_1",
                    user_id=user_row.id,
                    tx_type="expense",
                    amount=5.0,
                    happened_at=now + timedelta(days=30),
                    source_change_id=1001,
                )
            )
            db.commit()

        # 跑扫描
        res = client.get(
            "/api/v1/admin/integrity/scan",
            headers={"Authorization": f"Bearer {web_token}"},
        )
        assert res.status_code == 200, f"scan failed: {res.text}"
        data = res.json()
        assert data["ledgers_total"] >= 1
        assert data["issues_total"] >= 3

        types = {iss["issue_type"] for iss in data["issues"]}
        assert "orphan_tx_category" in types, f"missing orphan_tx_category in {types}"
        assert "zero_amount_tx" in types, f"missing zero_amount_tx in {types}"
        assert "future_tx" in types, f"missing future_tx in {types}"

        # 每个 issue 至少 1 条 sample,sync_id 命中我们注入的那条
        orphan = next(iss for iss in data["issues"] if iss["issue_type"] == "orphan_tx_category")
        assert orphan["count"] >= 1
        assert any(s["sync_id"] == "tx_orphan_1" for s in orphan["samples"])
    finally:
        app.dependency_overrides.clear()
