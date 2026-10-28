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
from src.models import (
    Ledger,
    ReadLoanProjection,
    ReadReimbursementProjection,
    ReadTxProjection,
)


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

    # 更新:登记一笔还款(0032)—— 本金 400 走所选银行卡 acc-x,自动累计已还金额。
    # 注意:已还金额不再由编辑接口手工改,改由「登记还款」端点维护。
    base = _change_id(client, tk, "LDG")
    rp = client.post(
        f"/api/v1/write/ledgers/LDG/loans/{row['id']}/repayments",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base,
            "repayment_id": "rp-1",
            "amount": 400,
            "interest_amount": 0,
            "repay_account_id": "acc-x",
            "repay_account_name": "招商银行",
            "happened_at": "2026-02-01",
            "note": "部分还款",
        },
    )
    assert rp.status_code == 200, rp.text
    loans = client.get(
        "/api/v1/read/ledgers/LDG/loans",
        headers={"Authorization": f"Bearer {tk}"},
    ).json()
    assert loans[0]["repaid_amount"] == 400
    assert abs(loans[0]["repay_progress"] - 0.4) < 1e-6
    assert loans[0]["status"] == "active"
    assert len(loans[0]["repayments"]) == 1
    assert loans[0]["repayments"][0]["id"] == "rp-1"

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
# 登记还款(0032)                                                               #
# --------------------------------------------------------------------------- #


def _get_loan(client: TestClient, token: str, ledger_id: str, loan_id: str):
    loans = client.get(
        f"/api/v1/read/ledgers/{ledger_id}/loans",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    for l in loans:
        if l["id"] == loan_id:
            return l
    return None


def _txs_by_prefix(TS, ledger_id: str, prefix: str) -> list[dict]:
    db = TS()
    try:
        # ledger_id 入参是 external_id(如 "LDG"),但 ReadTxProjection.ledger_id
        # 存的是内部 ledger.id(UUID)。先按原值查,查不到再解析成内部 id。
        rows = db.scalars(
            select(ReadTxProjection).where(
                ReadTxProjection.ledger_id == ledger_id,
                ReadTxProjection.sync_id.like(f"{prefix}%"),
            )
        ).all()
        if not rows:
            led = db.scalar(select(Ledger).where(Ledger.external_id == ledger_id))
            if led is not None:
                rows = db.scalars(
                    select(ReadTxProjection).where(
                        ReadTxProjection.ledger_id == led.id,
                        ReadTxProjection.sync_id.like(f"{prefix}%"),
                    )
                ).all()
        return [
            {
                "sync_id": r.sync_id,
                "type": r.tx_type,
                "amount": float(r.amount or 0.0),
                "account_sync_id": r.account_sync_id,
                "exclude_from_stats": bool(r.exclude_from_stats),
                "exclude_from_budget": bool(r.exclude_from_budget),
                "note": r.note,
            }
            for r in rows
        ]
    finally:
        db.close()


def test_loan_repayment_generates_linked_txs_and_respects_stats():
    """登记还款:本金走所选银行卡且不进收支,利息作正常支出计入统计,还清自动置
    repaid,撤销回退。"""
    client, TS, tk, _, _ = _setup("loan_rp@test.com", "LDG")

    created = _create_loan(
        client, tk, "LDG", direction="borrowed", counterparty="王五",
        principal=1000, started_at="2026-01-01", account_id="acc-1",
        account_name="招商银行", status="active",
    )
    loan_id = created["entity_id"]

    # 还款 1:本金 600 + 利息 50,走 acc-2
    base = _change_id(client, tk, "LDG")
    r1 = client.post(
        f"/api/v1/write/ledgers/LDG/loans/{loan_id}/repayments",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base, "repayment_id": "rp-1", "amount": 600,
            "interest_amount": 50, "repay_account_id": "acc-2",
            "repay_account_name": "工行", "happened_at": "2026-02-01",
            "note": "第1期",
        },
    )
    assert r1.status_code == 200, r1.text
    loan = _get_loan(client, tk, "LDG", loan_id)
    assert loan["repaid_amount"] == 600
    assert loan["status"] == "active"
    assert len(loan["repayments"]) == 1

    body_txs = _txs_by_prefix(TS, "LDG", f"{loan_id}::rp-body-")
    assert len(body_txs) == 1
    bt = body_txs[0]
    assert bt["type"] == "expense"  # 借入归还 = 支出(钱出 acc-2)
    assert bt["amount"] == 600
    assert bt["account_sync_id"] == "acc-2"
    assert bt["exclude_from_stats"] is True  # 本金不进收支统计
    assert bt["exclude_from_budget"] is True

    int_txs = _txs_by_prefix(TS, "LDG", f"{loan_id}::rp-interest-")
    assert len(int_txs) == 1
    it = int_txs[0]
    assert it["type"] == "expense"
    assert it["amount"] == 50
    assert it["account_sync_id"] == "acc-2"
    assert it["exclude_from_stats"] is False  # 利息正常支出
    assert it["exclude_from_budget"] is False

    # 借款本金流水仍在
    prin = _txs_by_prefix(TS, "LDG", f"{loan_id}::ln-principal")
    assert len(prin) == 1 and prin[0]["type"] == "income" and prin[0]["account_sync_id"] == "acc-1"

    # 还款 2:本金 400 还清,无利息
    base = _change_id(client, tk, "LDG")
    r2 = client.post(
        f"/api/v1/write/ledgers/LDG/loans/{loan_id}/repayments",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base, "repayment_id": "rp-2", "amount": 400,
            "interest_amount": 0, "repay_account_id": "acc-2",
            "repay_account_name": "工行", "happened_at": "2026-03-01",
        },
    )
    assert r2.status_code == 200, r2.text
    loan = _get_loan(client, tk, "LDG", loan_id)
    assert loan["repaid_amount"] == 1000
    assert loan["status"] == "repaid"
    assert len(_txs_by_prefix(TS, "LDG", f"{loan_id}::rp-body-rp-2")) == 1
    assert len(_txs_by_prefix(TS, "LDG", f"{loan_id}::rp-interest-rp-2")) == 0

    # 撤销还款 1:回退到 400 / active,rp-1 流水清理
    base = _change_id(client, tk, "LDG")
    d = client.request(
        "DELETE",
        f"/api/v1/write/ledgers/LDG/loans/{loan_id}/repayments/rp-1",
        headers={"Authorization": f"Bearer {tk}"},
        json={"base_change_id": base},
    )
    assert d.status_code == 200, d.text
    loan = _get_loan(client, tk, "LDG", loan_id)
    assert loan["repaid_amount"] == 400
    assert loan["status"] == "active"
    assert len(_txs_by_prefix(TS, "LDG", f"{loan_id}::rp-body-rp-1")) == 0
    assert len(_txs_by_prefix(TS, "LDG", f"{loan_id}::rp-interest-rp-1")) == 0
    assert len(_txs_by_prefix(TS, "LDG", f"{loan_id}::rp-body-rp-2")) == 1  # rp-2 仍在


def test_loan_repayment_validation_rejects_bad_input():
    client, TS, tk, _, _ = _setup("loan_rp_val@test.com", "LDG")
    created = _create_loan(
        client, tk, "LDG", direction="borrowed", counterparty="赵六",
        principal=1000, started_at="2026-01-01", account_id="acc-1",
        status="active",
    )
    loan_id = created["entity_id"]

    # 超额还款被拒
    base = _change_id(client, tk, "LDG")
    r = client.post(
        f"/api/v1/write/ledgers/LDG/loans/{loan_id}/repayments",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base, "repayment_id": "rp-x", "amount": 1500,
            "repay_account_id": "acc-2", "happened_at": "2026-02-01",
        },
    )
    assert r.status_code == 400, r.text

    # 正常还清
    base = _change_id(client, tk, "LDG")
    r = client.post(
        f"/api/v1/write/ledgers/LDG/loans/{loan_id}/repayments",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base, "repayment_id": "rp-1", "amount": 1000,
            "repay_account_id": "acc-2", "happened_at": "2026-02-01",
        },
    )
    assert r.status_code == 200, r.text

    # 已结清再还被拒
    base = _change_id(client, tk, "LDG")
    r = client.post(
        f"/api/v1/write/ledgers/LDG/loans/{loan_id}/repayments",
        headers={"Authorization": f"Bearer {tk}"},
        json={
            "base_change_id": base, "repayment_id": "rp-2", "amount": 1,
            "repay_account_id": "acc-2", "happened_at": "2026-03-01",
        },
    )
    assert r.status_code == 400, r.text


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
