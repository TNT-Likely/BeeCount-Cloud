"""0023 借款 / 报销 测试。

覆盖三条路径:
- web write/read 端点:CRUD、校验拒绝(principal/amount>0、counterparty/title 必填、
  direction/status 枚举)、派生字段(repay_progress / is_overdue)
- mobile /sync/push → projection(mobile payload camelCase + partial-update
  merge 契约,命名对齐 CLAUDE.md:test_mobile_push_<entity>_partial_update_keeps_*)
- 删除路径(web + mobile push)

鉴权约定(对齐 test_refunds.py):
- /sync/push 要求 SCOPE_APP_WRITE,故 seed 与 mobile push 一律用 owner(app 注册)令牌;
- /write 与 /read 端点用 web 登录令牌(SCOPE_WEB_WRITE / SCOPE_WEB_READ)。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, ReadLoanProjection, ReadReimbursementProjection


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


def _setup(email: str, ledger_id: str):
    """注册 owner(app scope,用于 seed / mobile push)+ web 登录(用于 write / read
    端点),返回 (client, TS, web_token, owner_token, device_id)。"""
    client, TS = _make_client()
    owner = _register(client, email)
    web = _login_web(client, email)
    web_token = web["access_token"]
    _seed_ledger(client, owner["access_token"], owner["device_id"], ledger_id)
    return client, TS, web_token, owner["access_token"], owner["device_id"]


def _change_id(client: TestClient, token: str, ledger_id: str) -> int:
    res = client.get(
        f"/api/v1/read/ledgers/{ledger_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    return int(res.json()["source_change_id"])


def _create_loan(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/loans",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _create_reimbursement(client: TestClient, token: str, ledger_id: str, **body) -> dict:
    base = _change_id(client, token, ledger_id)
    res = client.post(
        f"/api/v1/write/ledgers/{ledger_id}/reimbursements",
        headers={"Authorization": f"Bearer {token}"},
        json={"base_change_id": base, **body},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _push_one(client: TestClient, token: str, device_id: str, ledger_id: str, entity_type: str, payload: dict) -> dict:
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
    return res.json()


# --------------------------------------------------------------------------- #
# 借款                                                                         #
# --------------------------------------------------------------------------- #


def test_loan_crud_roundtrip_and_derived():
    client, _, tk, _, _ = _setup("loan_crud@test.com", "LDG")

    created = _create_loan(
        client,
        tk,
        "LDG",
        direction="lent",
        counterparty="张三",
        principal=1000,
        interest_rate=0.05,
        started_at="2024-01-01",
        due_at="2025-01-01",
        status="active",
    )
    # 用 read 列表校验 round-trip + 派生字段
    loans = client.get(
        "/api/v1/read/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert len(loans) == 1
    row = loans[0]
    assert row["direction"] == "lent"
    assert row["counterparty"] == "张三"
    assert row["principal"] == 1000
    assert row["interest_rate"] == 0.05
    # 2024-01-01 起借、2025-01-01 到期,测试日早已过期 → overdue
    assert row["is_overdue"] is True
    assert row["repay_progress"] == 0.0
    assert row["days_to_due"] is not None and row["days_to_due"] < 0

    # 更新:标记部分还款
    base = _change_id(client, tk, "LDG")
    upd = client.patch(
        f"/api/v1/write/ledgers/LDG/loans/{row['id']}",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base, "repaid_amount": 400, "status": "active"},
    )
    assert upd.status_code == 200, upd.text
    loans = client.get(
        "/api/v1/read/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert loans[0]["repaid_amount"] == 400
    assert abs(loans[0]["repay_progress"] - 0.4) < 1e-6

    # 删除
    base = _change_id(client, tk, "LDG")
    d = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/LDG/loans/{row['id']}",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base},
    )
    assert d.status_code == 200, d.text
    loans = client.get(
        "/api/v1/read/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert loans == []


def test_loan_validation_rejects_bad_input():
    client, _, tk, _, _ = _setup("loan_val@test.com", "LDG")
    base = _change_id(client, tk, "LDG")

    # principal 必须 > 0
    r = client.post(
        f"/api/v1/write/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base, "principal": 0, "counterparty": "x", "started_at": "2026-01-01"},
    )
    assert r.status_code == 422, r.text

    # counterparty 必填
    r = client.post(
        f"/api/v1/write/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base, "principal": 100, "started_at": "2026-01-01"},
    )
    assert r.status_code == 422, r.text

    # direction 枚举(Literal,非法值直接 422,不归一)
    r = client.post(
        f"/api/v1/write/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base,
            "direction": "weird",
            "principal": 100,
            "counterparty": "y",
            "started_at": "2026-01-01",
        },
    )
    assert r.status_code == 422, r.text

    # direction 缺省 borrowed
    created = client.post(
        f"/api/v1/write/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base,
            "principal": 100,
            "counterparty": "y",
            "started_at": "2026-01-01",
        },
    )
    assert created.status_code == 200, created.text
    loans = client.get(
        "/api/v1/read/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert loans[0]["direction"] == "borrowed"


def test_mobile_push_loan_partial_update_keeps_existing_fields():
    """mobile payload 是 camelCase 且只带部分字段;缺键必须从既有投影行补齐,
    不能被默认值覆盖(merge 契约,见 CLAUDE.md)。"""
    client, TS, _, owner_token, device_id = _setup("loan_push@test.com", "LDG")

    payload = {
        "syncId": "ln-1",
        "direction": "borrowed",
        "counterparty": "李四",
        "principal": 500,
        "currency": "CNY",
        "startedAt": "2026-03-01",
        "status": "active",
    }
    _push_one(client, owner_token, device_id, "LDG", "loan", payload)

    # 只带方向 + 状态(部分更新),其余字段必须保留
    _push_one(
        client,
        owner_token,
        device_id,
        "LDG",
        "loan",
        {"syncId": "ln-1", "direction": "lent", "status": "repaid"},
    )
    db = TS()
    try:
        row = db.scalar(
            select(ReadLoanProjection).where(ReadLoanProjection.sync_id == "ln-1")
        )
        assert row is not None
        assert row.direction == "lent"
        assert row.status == "repaid"
        # 未被部分更新触碰的字段保留
        assert row.counterparty == "李四"
        assert row.principal == 500
        assert row.started_at is not None
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 报销                                                                         #
# --------------------------------------------------------------------------- #


def test_reimbursement_crud_roundtrip():
    client, _, tk, _, _ = _setup("rb_crud@test.com", "LDG")

    created = _create_reimbursement(
        client,
        tk,
        "LDG",
        title="出差机票",
        payer="我",
        reimburser="公司",
        amount=2000,
        happened_at="2026-05-01",
        status="pending",
        related_tx_id="tx-air",
    )
    assert created["new_change_id"] > 0
    rbs = client.get(
        "/api/v1/read/ledgers/LDG/reimbursements",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert len(rbs) == 1
    row = rbs[0]
    assert row["title"] == "出差机票"
    assert row["payer"] == "我"
    assert row["reimburser"] == "公司"
    assert row["amount"] == 2000
    assert row["related_tx_id"] == "tx-air"
    assert row["status"] == "pending"

    # 状态推进到 reimbursed
    base = _change_id(client, tk, "LDG")
    upd = client.patch(
        f"/api/v1/write/ledgers/LDG/reimbursements/{row['id']}",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base, "status": "reimbursed"},
    )
    assert upd.status_code == 200, upd.text
    rbs = client.get(
        "/api/v1/read/ledgers/LDG/reimbursements?status=reimbursed",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert len(rbs) == 1 and rbs[0]["status"] == "reimbursed"

    # 删除
    base = _change_id(client, tk, "LDG")
    d = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/LDG/reimbursements/{row['id']}",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base},
    )
    assert d.status_code == 200, d.text
    rbs = client.get(
        "/api/v1/read/ledgers/LDG/reimbursements",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert rbs == []


def test_reimbursement_validation_rejects_bad_input():
    client, _, tk, _, _ = _setup("rb_val@test.com", "LDG")
    base = _change_id(client, tk, "LDG")

    # title 必填
    r = client.post(
        f"/api/v1/write/ledgers/LDG/reimbursements",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base, "amount": 100, "happened_at": "2026-01-01"},
    )
    assert r.status_code == 422, r.text

    # amount 必须 > 0
    r = client.post(
        f"/api/v1/write/ledgers/LDG/reimbursements",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base,
            "title": "x",
            "amount": 0,
            "happened_at": "2026-01-01",
        },
    )
    assert r.status_code == 422, r.text


def test_mobile_push_reimbursement_partial_update_keeps_existing_fields():
    client, TS, _, owner_token, device_id = _setup("rb_push@test.com", "LDG")

    _push_one(
        client,
        owner_token,
        device_id,
        "LDG",
        "reimbursement",
        {
            "syncId": "rb-1",
            "title": "团建",
            "amount": 800,
            "currency": "CNY",
            "happenedAt": "2026-04-01",
            "status": "pending",
        },
    )
    # 只带状态(部分更新)
    _push_one(
        client,
        owner_token,
        device_id,
        "LDG",
        "reimbursement",
        {"syncId": "rb-1", "status": "reimbursed"},
    )
    db = TS()
    try:
        row = db.scalar(
            select(ReadReimbursementProjection).where(
                ReadReimbursementProjection.sync_id == "rb-1"
            )
        )
        assert row is not None
        assert row.status == "reimbursed"
        assert row.title == "团建"
        assert row.amount == 800
        assert row.happened_at is not None
    finally:
        db.close()
