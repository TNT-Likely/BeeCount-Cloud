"""Contacts & Loans (web-first) REST + status-derivation tests.

Covers:contact CRUD + 409-on-linked-delete,loan create / status derivation
(unpaid/partial/overdue/settled),repayment add/delete recompute, virtual
accounts (应收款/应付款),reminders (near-due/overdue),contact summary.
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


def _register(client: TestClient, email: str) -> dict:
    res = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "123456",
            "client_type": "app",
            "device_name": "pytest",
            "platform": "app",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()


def _seed_ledger(client: TestClient, token: str, device_id: str, ledger_id: str) -> None:
    now = NOW.isoformat()
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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_contact(client: TestClient, token: str, name: str, **extra) -> dict:
    res = client.post(
        "/api/v1/contacts",
        headers=_auth(token),
        json={"name": name, **extra},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_loan(client: TestClient, token: str, ledger_id: str, **extra) -> dict:
    res = client.post(
        "/api/v1/loans",
        headers=_auth(token),
        json={"ledger_id": ledger_id, "direction": "lend", "amount": 100.0,
              "happened_at": NOW.isoformat(), **extra},
    )
    assert res.status_code == 201, res.text
    return res.json()


# --------------------------------------------------------------------------


def test_contact_crud_and_suggest() -> None:
    client = _make_client()
    user = _register(client, "cl1@example.com")
    token = user["access_token"]

    c = _create_contact(client, token, "Alice", contact_tag="friend", phone="123")
    assert c["name"] == "Alice" and c["contact_tag"] == "friend"

    # list + search
    res = client.get("/api/v1/contacts", headers=_auth(token), params={"search": "ali"})
    assert res.status_code == 200 and len(res.json()) == 1
    res = client.get("/api/v1/contacts", headers=_auth(token), params={"search": "zzz"})
    assert res.json() == []

    # patch
    res = client.patch(
        f"/api/v1/contacts/{c['id']}", headers=_auth(token), json={"note": "buddy"}
    )
    assert res.status_code == 200 and res.json()["note"] == "buddy"

    # suggest returns a list (may be empty in fresh db)
    res = client.get("/api/v1/contacts/suggest", headers=_auth(token))
    assert res.status_code == 200 and isinstance(res.json(), list)


def test_contact_archive_and_disable() -> None:
    """归档/停用:列表筛选、状态动作端点与新借款拦截(0066)。"""
    client = _make_client()
    user = _register(client, "cl_status@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "LSTAT")
    a = _create_contact(client, token, "ArchivedA")
    b = _create_contact(client, token, "DisabledB")

    def rows(state: str) -> list[dict]:
        res = client.get("/api/v1/contacts", headers=_auth(token), params={"state": state})
        assert res.status_code == 200, res.text
        return res.json()

    def loan_payload(contact_id: str) -> dict:
        return {
            "ledger_id": "LSTAT",
            "contact_id": contact_id,
            "direction": "lend",
            "amount": 100.0,
            "happened_at": NOW.isoformat(),
        }

    assert len(rows("active")) == 2 and rows("archived") == []

    # 归档:移出默认列表,进入 archived;不可产生新借款
    res = client.post(f"/api/v1/contacts/{a['id']}/archive", headers=_auth(token))
    assert res.status_code == 200 and res.json()["is_archived"] is True
    assert [x["id"] for x in rows("active")] == [b["id"]]
    assert [x["id"] for x in rows("archived")] == [a["id"]]
    res = client.post("/api/v1/loans", headers=_auth(token), json=loan_payload(a["id"]))
    assert res.status_code == 409, res.text

    # 恢复归档:回到默认列表
    res = client.post(f"/api/v1/contacts/{a['id']}/unarchive", headers=_auth(token))
    assert res.status_code == 200 and res.json()["is_archived"] is False
    assert len(rows("active")) == 2

    # 停用:仍在默认列表,进入 disabled;不可新增借款,也不进排行
    res = client.post(f"/api/v1/contacts/{b['id']}/disable", headers=_auth(token))
    assert res.status_code == 200 and res.json()["is_active"] is False
    assert len(rows("active")) == 2
    assert [x["id"] for x in rows("disabled")] == [b["id"]]
    res = client.post("/api/v1/loans", headers=_auth(token), json=loan_payload(b["id"]))
    assert res.status_code == 409, res.text
    loan_a = _create_loan(client, token, "LSTAT", contact_id=a["id"])
    res = client.patch(
        f"/api/v1/loans/{loan_a['id']}", headers=_auth(token), json={"contact_id": b["id"]}
    )
    assert res.status_code == 409, res.text
    ranks = client.get("/api/v1/contacts/loan-ranks", headers=_auth(token)).json()
    assert all(r["contact_id"] != b["id"] for r in ranks)

    # 重新启用:恢复新借款能力
    res = client.post(f"/api/v1/contacts/{b['id']}/enable", headers=_auth(token))
    assert res.status_code == 200 and res.json()["is_active"] is True
    loan_b = _create_loan(client, token, "LSTAT", contact_id=b["id"])
    assert loan_b["contact_name"] == "DisabledB"
    assert len(rows("all")) == 2


def test_loan_status_derivation_and_repayment() -> None:
    client = _make_client()
    user = _register(client, "cl2@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "L1")
    contact = _create_contact(client, token, "Bob")

    # 借出 100,未还 → 未还清(unpaid)
    loan = _create_loan(client, token, "L1", contact_id=contact["id"])
    assert loan["status"] == "unpaid" and loan["total_repaid"] == 0.0
    assert loan["contact_name"] == "Bob"

    # 还 40 → 部分还款(partial)
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments",
        headers=_auth(token),
        json={"amount": 40.0, "repaid_at": NOW.isoformat()},
    )
    assert res.status_code == 201, res.text
    res = client.get(f"/api/v1/loans/{loan['id']}", headers=_auth(token))
    assert res.json()["status"] == "partial" and res.json()["total_repaid"] == 40.0

    # 再还 60 → 已结清(settled)
    client.post(
        f"/api/v1/loans/{loan['id']}/repayments",
        headers=_auth(token),
        json={"amount": 60.0, "repaid_at": NOW.isoformat()},
    )
    res = client.get(f"/api/v1/loans/{loan['id']}", headers=_auth(token))
    assert res.json()["status"] == "settled"

    # 删一笔还款 → 回到部分还款
    reps = client.get(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token)
    ).json()
    client.delete(
        f"/api/v1/loans/{loan['id']}/repayments/{reps[0]['id']}", headers=_auth(token)
    )
    res = client.get(f"/api/v1/loans/{loan['id']}", headers=_auth(token))
    assert res.json()["status"] == "partial"


def test_loan_overdue_and_virtual_accounts() -> None:
    client = _make_client()
    user = _register(client, "cl3@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "L2")

    # 借出 200,约定还款日已过去 → 逾期(overdue)
    past = (NOW - timedelta(days=5)).isoformat()
    loan = _create_loan(client, token, "L2", amount=200.0, due_at=past)
    assert loan["status"] == "overdue"

    # 借入 50,无到期 → 未还清
    loan2 = _create_loan(client, token, "L2", direction="borrow", amount=50.0)
    assert loan2["status"] == "unpaid"

    # 虚拟账户:应收=200(借出未还),应付=50(借入未还)
    res = client.get(
        "/api/v1/loans/virtual-accounts", headers=_auth(token), params={"ledger_id": "L2"}
    )
    assert res.status_code == 200, res.text
    va = res.json()
    assert abs(va["receivable"] - 200.0) < 0.001
    assert abs(va["payable"] - 50.0) < 0.001

    # 提醒:overdue 应出现在 overdue 列表
    res = client.get("/api/v1/loans/reminders", headers=_auth(token), params={"ledger_id": "L2"})
    assert res.status_code == 200
    rem = res.json()
    assert len(rem["overdue"]) == 1
    assert rem["overdue"][0]["days_to_due"] < 0


def test_loan_near_due_reminder() -> None:
    client = _make_client()
    user = _register(client, "cl4@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "L3")
    soon = (NOW + timedelta(days=2)).isoformat()
    _create_loan(client, token, "L3", amount=80.0, due_at=soon)
    res = client.get("/api/v1/loans/reminders", headers=_auth(token), params={"ledger_id": "L3"})
    rem = res.json()
    assert len(rem["near_due"]) == 1 and len(rem["overdue"]) == 0


def test_contact_summary_and_delete_guard() -> None:
    client = _make_client()
    user = _register(client, "cl5@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "L4")
    contact = _create_contact(client, token, "Carol")
    _create_loan(client, token, "L4", contact_id=contact["id"], amount=120.0)

    res = client.get(f"/api/v1/contacts/{contact['id']}/summary", headers=_auth(token))
    assert res.status_code == 200, res.text
    s = res.json()
    assert s["loan_count"] == 1
    assert s["lend_total"] == 120.0
    assert abs(s["receivable"] - 120.0) < 0.001
    assert s["payable"] == 0.0

    # 有关联借款,删除应 409
    res = client.delete(f"/api/v1/contacts/{contact['id']}", headers=_auth(token))
    assert res.status_code == 409


def test_contact_loan_ranks() -> None:
    """0031:联系人借款排行一次返回全量联系人,跨账本、与 summary 同口径。"""
    client = _make_client()
    user = _register(client, "clranks@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "LR1")
    _seed_ledger(client, token, user["device_id"], "LR2")
    alice = _create_contact(client, token, "Alice", contact_tag="friend")
    bob = _create_contact(client, token, "Bob")
    _create_contact(client, token, "NoLoans")  # 无借款:不应出现在排行里

    # Alice:两个账本各有借出,其中一笔部分还款 → 应收 60 + 30 = 90
    a1 = _create_loan(
        client, token, "LR1", contact_id=alice["id"], amount=100.0
    )
    client.post(
        f"/api/v1/loans/{a1['id']}/repayments",
        headers=_auth(token),
        json={"amount": 40.0, "repaid_at": NOW.isoformat()},
    )
    _create_loan(client, token, "LR2", contact_id=alice["id"], amount=30.0)
    # Bob:借入 50(未还)+ 借出 20(已结清) → borrow 50 / lend 20 / payable 50
    b1 = _create_loan(
        client, token, "LR1", contact_id=bob["id"], direction="borrow", amount=50.0
    )
    b2 = _create_loan(
        client, token, "LR1", contact_id=bob["id"], direction="lend", amount=20.0
    )
    client.post(
        f"/api/v1/loans/{b2['id']}/repayments",
        headers=_auth(token),
        json={"amount": 20.0, "repaid_at": NOW.isoformat()},
    )

    res = client.get("/api/v1/contacts/loan-ranks", headers=_auth(token))
    assert res.status_code == 200, res.text
    rows = {row["contact_id"]: row for row in res.json()}
    assert set(rows) == {alice["id"], bob["id"]}
    a = rows[alice["id"]]
    assert a["name"] == "Alice" and a["contact_tag"] == "friend"
    assert a["loan_count"] == 2
    assert abs(a["lend_total"] - 130.0) < 0.001
    assert abs(a["borrow_total"]) < 0.001
    assert abs(a["receivable"] - 90.0) < 0.001
    assert abs(a["payable"]) < 0.001
    b = rows[bob["id"]]
    assert b["loan_count"] == 2
    assert abs(b["lend_total"] - 20.0) < 0.001
    assert abs(b["borrow_total"] - 50.0) < 0.001
    assert abs(b["receivable"]) < 0.001
    assert abs(b["payable"] - 50.0) < 0.001

    # 与单联系人 summary 完全一致(同一口径)
    summary = client.get(
        f"/api/v1/contacts/{alice['id']}/summary", headers=_auth(token)
    ).json()
    for key in ("loan_count", "lend_total", "borrow_total", "receivable", "payable"):
        assert abs(a[key] - summary[key]) < 0.001

    # 可选按账本过滤:LR2 只有 Alice 的 30
    res = client.get(
        "/api/v1/contacts/loan-ranks",
        headers=_auth(token),
        params={"ledger_id": "LR2"},
    )
    assert res.status_code == 200, res.text
    assert len(res.json()) == 1
    assert res.json()[0]["contact_id"] == alice["id"]
    assert abs(res.json()[0]["lend_total"] - 30.0) < 0.001

    # 用户隔离:另一个账号看不到任何排行
    other = _register(client, "clranks2@example.com")
    res = client.get(
        "/api/v1/contacts/loan-ranks", headers=_auth(other["access_token"])
    )
    assert res.status_code == 200 and res.json() == []


def test_loan_list_filters() -> None:
    client = _make_client()
    user = _register(client, "cl6@example.com")
    token = user["access_token"]
    _seed_ledger(client, token, user["device_id"], "L5")
    _create_loan(client, token, "L5", amount=10.0)
    _create_loan(client, token, "L5", direction="borrow", amount=20.0)

    res = client.get("/api/v1/loans", headers=_auth(token), params={"ledger_id": "L5"})
    assert res.status_code == 200 and len(res.json()) == 2
    res = client.get(
        "/api/v1/loans",
        headers=_auth(token),
        params={"ledger_id": "L5", "direction": "borrow"},
    )
    assert len(res.json()) == 1 and res.json()[0]["direction"] == "borrow"


def test_loan_requires_ledger_id() -> None:
    client = _make_client()
    user = _register(client, "cl7@example.com")
    token = user["access_token"]
    res = client.get("/api/v1/loans", headers=_auth(token))
    assert res.status_code == 422


# --------------------------------------------------------------------------
# 借款流水(0027):账户选择 + 生成对应流水 + 编辑/删除联动
# --------------------------------------------------------------------------

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


def test_loan_flow_generation_and_sync() -> None:
    client = _make_client()
    user = _register_web(client, "loanflow@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LFL")
    acc_id = _create_account_web(client, token, "LFL", "招商银行")

    # 借出 100,选账户 → 生成 expense 流水(排除统计)
    loan = _create_loan(
        client, token, "LFL", direction="lend", amount=100.0,
        account_id=acc_id, account_name="招商银行",
    )
    assert loan["account_id"] == acc_id
    assert loan["account_name"] == "招商银行"
    assert loan["tx_sync_id"]
    f = _find_flow(_list_txns(client, token, "LFL"), loan["tx_sync_id"])
    assert f is not None
    assert f["tx_type"] == "expense"
    assert f["amount"] == 100.0
    assert f["account_id"] == acc_id
    assert f["account_name"] == "招商银行"
    assert f["exclude_from_stats"] is True
    assert f["exclude_from_budget"] is True
    assert "借出" in (f["note"] or "")

    # 借入 50 → 生成 income 流水
    loan2 = _create_loan(
        client, token, "LFL", direction="borrow", amount=50.0,
        account_id=acc_id, account_name="招商银行",
    )
    f2 = _find_flow(_list_txns(client, token, "LFL"), loan2["tx_sync_id"])
    assert f2["tx_type"] == "income"
    assert f2["amount"] == 50.0
    assert "借入" in (f2["note"] or "")

    # 还款 40(借出还款=收入入账)
    rp = client.post(
        f"/api/v1/loans/{loan['id']}/repayments",
        headers=_auth(token),
        json={"amount": 40.0, "repaid_at": NOW.isoformat()},
    )
    assert rp.status_code == 201, rp.text
    assert rp.json()["tx_sync_id"]
    rf = _find_flow(_list_txns(client, token, "LFL"), rp.json()["tx_sync_id"])
    assert rf["tx_type"] == "income"
    assert rf["amount"] == 40.0
    assert "收回借款" in (rf["note"] or "")

    # 编辑借款金额 100→80 → 本金流水金额原地联动更新(保留 tx_sync_id)
    old_sync = loan["tx_sync_id"]
    res = client.patch(
        f"/api/v1/loans/{loan['id']}", headers=_auth(token), json={"amount": 80.0}
    )
    assert res.status_code == 200, res.text
    assert res.json()["tx_sync_id"] == old_sync  # 联动更新,不重建
    f = _find_flow(_list_txns(client, token, "LFL"), old_sync)
    assert f is not None and f["amount"] == 80.0

    # 删除借款 → 本金流水 + 还款流水一并删除
    res = client.delete(f"/api/v1/loans/{loan['id']}", headers=_auth(token))
    assert res.status_code == 204, res.text
    txns = _list_txns(client, token, "LFL")
    assert _find_flow(txns, old_sync) is None
    assert _find_flow(txns, rp.json()["tx_sync_id"]) is None


def test_loan_without_account_has_no_flow() -> None:
    client = _make_client()
    user = _register_web(client, "loanacct@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LAC")
    # 不传账户 → 成功创建借款,但不生成流水(tx_sync_id 为 None)。
    res = client.post(
        "/api/v1/loans",
        headers=_auth(token),
        json={"ledger_id": "LAC", "direction": "lend", "amount": 100.0,
              "happened_at": NOW.isoformat()},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["account_id"] is None
    assert body["tx_sync_id"] is None
    # 交易列表不应出现任何借款流水。
    txns = _list_txns(client, token, "LAC")
    assert txns == []


def _patch_tx_amount(client: TestClient, token: str, ledger_id: str, tx_id: str, amount: float) -> dict:
    res = client.patch(
        f"/api/v1/write/ledgers/{ledger_id}/transactions/{tx_id}",
        headers=_auth(token),
        json={"base_change_id": 0, "amount": amount},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _get_loan(client: TestClient, token: str, loan_id: str) -> dict:
    res = client.get(f"/api/v1/loans/{loan_id}", headers=_auth(token))
    assert res.status_code == 200, res.text
    return res.json()


def test_loan_flow_amount_edit_syncs_loan_and_repayment() -> None:
    """交易侧直接编辑借款流水金额 → 反向同步借款/还款金额与状态。"""
    client = _make_client()
    user = _register_web(client, "loansync@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LSY")
    acc_id = _create_account_web(client, token, "LSY", "现金")

    # 借出 100 → 流水;交易侧把流水金额改成 66 → 借款金额同步为 66
    loan = _create_loan(
        client, token, "LSY", direction="lend", amount=100.0,
        account_id=acc_id, account_name="现金",
    )
    _patch_tx_amount(client, token, "LSY", loan["tx_sync_id"], 66.0)
    assert _get_loan(client, token, loan["id"])["amount"] == 66.0

    # 还款 30 → 收回借款流水;交易侧改成 20 → 还款金额 20,借款状态重算 partial
    rp = client.post(
        f"/api/v1/loans/{loan['id']}/repayments",
        headers=_auth(token),
        json={"amount": 30.0, "repaid_at": NOW.isoformat()},
    )
    assert rp.status_code == 201, rp.text
    rp_tx = rp.json()["tx_sync_id"]
    _patch_tx_amount(client, token, "LSY", rp_tx, 20.0)
    detail = _get_loan(client, token, loan["id"])
    assert detail["amount"] == 66.0
    repayments = client.get(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token)
    ).json()
    assert repayments[0]["amount"] == 20.0
    # 已还 20 < 66 → partial
    assert detail["status"] == "partial"

    # 与借款无关的普通流水:改金额不产生副作用(借款不变)
    f = _find_flow(_list_txns(client, token, "LSY"), loan["tx_sync_id"])
    assert f is not None and f["amount"] == 66.0  # 流水侧保持用户输入


def test_repayment_account_is_selectable() -> None:
    """还款账户可独立选择:不再被强制继承借款账户;编辑借款账户也不覆盖显式选择。"""
    client = _make_client()
    user = _register_web(client, "loanrpacct@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LRA")
    acc_a = _create_account_web(client, token, "LRA", "现金")
    acc_b = _create_account_web(client, token, "LRA", "银行卡")
    acc_c = _create_account_web(client, token, "LRA", "支付宝")

    loan = _create_loan(
        client, token, "LRA", direction="lend", amount=100.0,
        account_id=acc_a, account_name="现金",
    )
    url = f"/api/v1/loans/{loan['id']}/repayments"

    # 显式选另一个账户 → 还款记录和流水都用这个账户。
    rp = client.post(
        url, headers=_auth(token),
        json={"amount": 40.0, "repaid_at": NOW.isoformat(),
              "account_id": acc_b, "account_name": "银行卡"},
    )
    assert rp.status_code == 201, rp.text
    explicit = rp.json()
    assert explicit["account_id"] == acc_b
    assert explicit["account_name"] == "银行卡"
    assert explicit["tx_sync_id"]
    rf = _find_flow(_list_txns(client, token, "LRA"), explicit["tx_sync_id"])
    assert rf is not None
    assert rf["account_id"] == acc_b
    assert rf["tx_type"] == "income" and rf["amount"] == 40.0

    # 不传账户(旧客户端)→ 仍沿用借款账户。
    rp2 = client.post(
        url, headers=_auth(token),
        json={"amount": 10.0, "repaid_at": NOW.isoformat()},
    )
    assert rp2.status_code == 201, rp2.text
    inherited = rp2.json()
    assert inherited["account_id"] == acc_a

    # 改借款账户 → 显式选择的还款保留 acc_b,沿用型的跟随新账户 acc_c。
    res = client.patch(
        f"/api/v1/loans/{loan['id']}", headers=_auth(token),
        json={"account_id": acc_c, "account_name": "支付宝"},
    )
    assert res.status_code == 200, res.text
    rows = client.get(url, headers=_auth(token)).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[explicit["id"]]["account_id"] == acc_b
    assert by_id[inherited["id"]]["account_id"] == acc_c

    # 借款本身没有账户时,也能单独为一笔还款指定账户并生成流水。
    no_acct_loan = _create_loan(client, token, "LRA", direction="borrow", amount=50.0)
    assert no_acct_loan["account_id"] is None
    rp3 = client.post(
        f"/api/v1/loans/{no_acct_loan['id']}/repayments", headers=_auth(token),
        json={"amount": 20.0, "repaid_at": NOW.isoformat(),
              "account_id": acc_b, "account_name": "银行卡"},
    )
    assert rp3.status_code == 201, rp3.text
    assert rp3.json()["account_id"] == acc_b
    assert rp3.json()["tx_sync_id"]
    rf3 = _find_flow(_list_txns(client, token, "LRA"), rp3.json()["tx_sync_id"])
    assert rf3 is not None and rf3["tx_type"] == "expense"


def test_settled_loan_can_append_principal_and_repay_again() -> None:
    """已结清借款仍可追加本金:状态按新本金重算,本金流水联动,并可继续还款。"""
    client = _make_client()
    user = _register_web(client, "loanappend@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LAP")
    acc = _create_account_web(client, token, "LAP", "现金")

    loan = _create_loan(
        client, token, "LAP", direction="lend", amount=100.0,
        account_id=acc, account_name="现金",
    )
    url = f"/api/v1/loans/{loan['id']}/repayments"
    res = client.post(
        url, headers=_auth(token),
        json={"amount": 100.0, "repaid_at": NOW.isoformat()},
    )
    assert res.status_code == 201, res.text
    assert _get_loan(client, token, loan["id"])["status"] == "settled"

    # 追加 50 → 本金 150,已还 100 → 重新变成 partial,剩余 50。
    res = client.patch(
        f"/api/v1/loans/{loan['id']}", headers=_auth(token),
        json={"amount": 150.0, "account_id": acc, "account_name": "现金",
              "note": "追加借款"},
    )
    assert res.status_code == 200, res.text
    detail = _get_loan(client, token, loan["id"])
    assert detail["amount"] == 150.0
    assert detail["total_repaid"] == 100.0
    assert detail["status"] == "partial"
    assert detail["tx_sync_id"] == loan["tx_sync_id"]  # 原地更新,不重建流水
    f = _find_flow(_list_txns(client, token, "LAP"), detail["tx_sync_id"])
    assert f is not None and f["amount"] == 150.0

    # 后端不拦截已结清/部分状态下的继续还款(超还也允许)。
    again = client.post(
        url, headers=_auth(token),
        json={"amount": 60.0, "repaid_at": NOW.isoformat(), "account_id": acc},
    )
    assert again.status_code == 201, again.text
    assert _get_loan(client, token, loan["id"])["status"] == "settled"


def test_loan_and_repayment_attachments_flow_through() -> None:
    """借款凭证/还款凭证:API 往返 + 生成流水的 attachments 联动(增删改)。"""
    client = _make_client()
    user = _register_web(client, "loanattach@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LAT")
    acc = _create_account_web(client, token, "LAT", "现金")
    loan_url = "/api/v1/loans"

    loan = _create_loan(
        client, token, "LAT", direction="lend", amount=100.0,
        account_id=acc, account_name="现金",
        voucher_file_ids=["file-1", "file-2"],
    )
    assert loan["voucher_file_ids"] == ["file-1", "file-2"]
    flow = _find_flow(_list_txns(client, token, "LAT"), loan["tx_sync_id"])
    assert flow is not None
    assert [a["cloudFileId"] for a in flow["attachments"]] == ["file-1", "file-2"]

    # 追加借款时改凭证:本金流水原地更新,删掉 file-1 加进 file-3。
    res = client.patch(
        f"{loan_url}/{loan['id']}", headers=_auth(token),
        json={"voucher_file_ids": ["file-2", "file-3"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["voucher_file_ids"] == ["file-2", "file-3"]
    flow = _find_flow(_list_txns(client, token, "LAT"), loan["tx_sync_id"])
    assert [a["cloudFileId"] for a in flow["attachments"]] == ["file-2", "file-3"]

    # 清空凭证 → 流水附件也要被清空(不能残留旧图)。
    res = client.patch(
        f"{loan_url}/{loan['id']}", headers=_auth(token),
        json={"voucher_file_ids": []},
    )
    assert res.status_code == 200, res.text
    assert res.json()["voucher_file_ids"] == []
    flow = _find_flow(_list_txns(client, token, "LAT"), loan["tx_sync_id"])
    assert not flow["attachments"]

    # 还款带凭证:创建返回 + 列表返回 + 还款流水 attachments 三处一致。
    rp = client.post(
        f"{loan_url}/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 40.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金",
              "attachment_file_ids": ["file-r1"]},
    )
    assert rp.status_code == 201, rp.text
    assert rp.json()["attachment_file_ids"] == ["file-r1"]
    listed = client.get(
        f"{loan_url}/{loan['id']}/repayments", headers=_auth(token)
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["attachment_file_ids"] == ["file-r1"]
    repay_flow = _find_flow(_list_txns(client, token, "LAT"), rp.json()["tx_sync_id"])
    assert repay_flow is not None
    assert [a["cloudFileId"] for a in repay_flow["attachments"]] == ["file-r1"]

    # 旧客户端不传附件字段时按空数组返回,不报错。
    rp2 = client.post(
        f"{loan_url}/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 10.0, "repaid_at": NOW.isoformat()},
    )
    assert rp2.status_code == 201, rp2.text
    assert rp2.json()["attachment_file_ids"] == []


def test_repayment_total_auto_splits_interest() -> None:
    """借 500 还 510:只填还款总额,系统拆出本金 500 + 利息 10。

    验收点(用户场景):
      - 借款状态 = settled(已还款),本金被全额冲抵;
      - Repayment.amount=500(本金),interest_amount=10;
      - 0041:利息**不单独成流水**(与实际银行流水一致),而是并入同一笔流水的
        金额 → 流水金额 = 510,备注注明「含利息 10 元」;
      - 还 510 不产生「超还 10」的错觉(本金维度刚好结清)。
    """
    client = _make_client()
    user = _register_web(client, "loansplit@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LSPL")
    acc = _create_account_web(client, token, "LSPL", "现金")

    loan = _create_loan(
        client, token, "LSPL", direction="lend", amount=500.0,
        account_id=acc, account_name="现金",
    )
    # 只填总额 510,不显式传利息 → 自动拆分。
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 510.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    rp = res.json()
    assert rp["amount"] == 500.0
    assert rp["interest_amount"] == 10.0

    # 借款状态:本金结清。
    detail = _get_loan(client, token, loan["id"])
    assert detail["status"] == "settled"
    assert detail["total_repaid"] == 500.0

    # 交易流水:只有**一条**还款流水,金额 = 本金 + 利息 = 510,备注注明含利息。
    txns = _list_txns(client, token, "LSPL")
    repay_flow = _find_flow(txns, rp["tx_sync_id"])
    assert repay_flow is not None and repay_flow["amount"] == 510.0
    assert repay_flow["category_name"] == "收回借款"
    assert repay_flow["exclude_from_stats"] is True
    assert repay_flow["exclude_from_budget"] is True
    assert "含利息 10 元" in (repay_flow["note"] or "")
    # 利息不再单独成流水:没有「借款利息」分类的流水,也没有独立的利息流水引用。
    assert not [t for t in txns if t["category_name"] == "借款利息"]
    assert rp["interest_tx_sync_id"] is None

    # 删除还款 → 该笔流水一并删除,状态回到未还。
    client.delete(
        f"/api/v1/loans/{loan['id']}/repayments/{rp['id']}", headers=_auth(token)
    )
    detail = _get_loan(client, token, loan["id"])
    assert detail["status"] == "unpaid" and detail["total_repaid"] == 0.0
    txns = _list_txns(client, token, "LSPL")
    assert _find_flow(txns, rp["tx_sync_id"]) is None
    assert not [t for t in txns if t["category_name"] == "借款利息"]


def test_repayment_explicit_interest_takes_precedence() -> None:
    """显式传 interest_amount 时以用户指定为准(本金 = 总额 - 利息)。"""
    client = _make_client()
    user = _register_web(client, "loanexpint@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LEXP")
    acc = _create_account_web(client, token, "LEXP", "现金")
    loan = _create_loan(
        client, token, "LEXP", direction="lend", amount=100.0,
        account_id=acc, account_name="现金",
    )
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 103.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金",
              "interest_amount": 3.0},
    )
    assert res.status_code == 201, res.text
    rp = res.json()
    assert rp["amount"] == 100.0 and rp["interest_amount"] == 3.0
    assert _get_loan(client, token, loan["id"])["status"] == "settled"


def test_merged_repayment_creates_single_flow_for_multiple_loans() -> None:
    """一次实际收付款合并结清多笔借款(0039)。

    用户场景:张三 2025-01-01 借出 50+50+50,实际收到**一笔** 170 元
    (150 合并本金 + 20 利息)。验收点:
      - 交易流水里只有**一条**(170 = 150 本金 + 20 利息),不是 3 条各 50;
      - 0041:利息不单独成流水,而是并入这条流水的金额,备注注明「含利息 20 元」;
      - 3 笔借款全部结清,状态都是 settled;
      - 合并流水带 merged_from_sync_ids(被合并的 3 笔借款本金流水),
        交易详情据此展示「查看原单」。
    """
    client = _make_client()
    user = _register_web(client, "loanmerge@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LMRG")
    acc = _create_account_web(client, token, "LMRG", "现金")
    contact = _create_contact(client, token, "张三")

    # 借出 3 笔各 50
    loans = []
    for _ in range(3):
        loan = _create_loan(
            client, token, "LMRG", direction="lend", amount=50.0,
            contact_id=contact["id"], account_id=acc, account_name="现金",
        )
        loans.append(loan)
    assert len(loans) == 3

    # 一次收付款:总额 170(本金 150 + 利息 20)
    res = client.post(
        "/api/v1/loans/repayments/merged", headers=_auth(token),
        json={"ledger_id": "LMRG", "contact_id": contact["id"], "direction": "lend",
              "amount": 170.0, "interest_amount": 20.0,
              "repaid_at": NOW.isoformat(), "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    merged = res.json()
    assert merged["amount"] == 150.0
    assert merged["interest_amount"] == 20.0
    assert len(merged["allocations"]) == 3
    assert merged["tx_sync_id"]
    # 0041:利息不再单独成流水,没有独立的利息流水引用。
    assert merged["interest_tx_sync_id"] is None
    # 被合并的 3 笔借款本金流水都在「查看原单」列表里
    assert len(merged["merged_from_sync_ids"]) == 3
    for l in loans:
        assert l["tx_sync_id"] in merged["merged_from_sync_ids"]

    # 3 笔借款全部结清
    for l in loans:
        detail = _get_loan(client, token, l["id"])
        assert detail["status"] == "settled", (l["id"], detail["status"])
        assert detail["total_repaid"] == 50.0

    # 交易流水:只有一条(170 = 150 本金 + 20 利息),利息不单独成流水。
    txns = _list_txns(client, token, "LMRG")
    repay_flows = [t for t in txns if t["category_name"] == "收回借款"]
    interest_flows = [t for t in txns if t["category_name"] == "借款利息"]
    assert len(repay_flows) == 1, f"本金流水应只有 1 条,实际 {len(repay_flows)}"
    assert repay_flows[0]["amount"] == 170.0
    assert "含利息 20 元" in (repay_flows[0]["note"] or "")
    assert len(interest_flows) == 0

    # 合并流水在读接口带 merged_from_sync_ids(供「查看原单」)
    res = client.get("/api/v1/read/workspace/transactions?ledger_id=LMRG&limit=200", headers=_auth(token))
    assert res.status_code == 200, res.text
    rows = res.json()["items"]
    merged_row = next(r for r in rows if r["id"] == merged["tx_sync_id"])
    assert sorted(merged_row["merged_from_sync_ids"]) == sorted(merged["merged_from_sync_ids"])
    assert len(merged_row["merged_from_sync_ids"]) == 3

    # 删除合并组内的代表行 → 整笔实际收付款被删除(所有分摊行 + 两条流水),
    # 三笔借款都回到未还。
    reps = client.get(
        f"/api/v1/loans/{loans[0]['id']}/repayments", headers=_auth(token)
    ).json()
    assert len(reps) == 1
    assert reps[0]["group_size"] == 3
    # group_amount = 实际流水金额(本金 150 + 利息 20 = 170)。
    assert reps[0]["group_amount"] == 170.0
    res = client.delete(
        f"/api/v1/loans/{loans[0]['id']}/repayments/{reps[0]['id']}", headers=_auth(token)
    )
    assert res.status_code == 204, res.text
    for l in loans:
        assert _get_loan(client, token, l["id"])["status"] == "unpaid", l["id"]
    # 合并本金流水 + 利息流水都应被删除
    txns = _list_txns(client, token, "LMRG")
    assert not [t for t in txns if t["category_name"] == "收回借款"]
    assert not [t for t in txns if t["category_name"] == "借款利息"]


def test_merged_repayment_auto_splits_interest() -> None:
    """合并还款不显式给利息时,按剩余本金自动拆分(借 50×3 还 170 → 本金150+利息20)。"""
    client = _make_client()
    user = _register_web(client, "loanmergeauto@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LMRA")
    acc = _create_account_web(client, token, "LMRA", "现金")
    contact = _create_contact(client, token, "唐敏")
    for _ in range(3):
        _create_loan(
            client, token, "LMRA", direction="lend", amount=50.0,
            contact_id=contact["id"], account_id=acc, account_name="现金",
        )
    res = client.post(
        "/api/v1/loans/repayments/merged", headers=_auth(token),
        json={"ledger_id": "LMRA", "contact_id": contact["id"], "direction": "lend",
              "amount": 170.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    merged = res.json()
    assert merged["amount"] == 150.0
    assert merged["interest_amount"] == 20.0
    txns = _list_txns(client, token, "LMRA")
    repay_flows = [t for t in txns if t["category_name"] == "收回借款"]
    assert len(repay_flows) == 1
    # 0041:利息并入这一条流水(150 本金 + 20 利息 = 170),不单独成流水。
    assert repay_flows[0]["amount"] == 170.0
    assert "含利息 20 元" in (repay_flows[0]["note"] or "")
    assert len([t for t in txns if t["category_name"] == "借款利息"]) == 0




def test_repayment_principal_never_exceeds_outstanding() -> None:
    """本金记账不得超过未结清额(0041)。

    场景:借款 500 已还 510(本金 500 + 利息 10,已结清),随后再记一笔还款 170
    并显式声明利息 20。此时该借款已无未结清本金,本金应记 0、利息记 170,
    流水金额 = 170(与实际收付一致),不得出现「超还」。
    """
    client = _make_client()
    user = _register_web(client, "loancap@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LCAP")
    acc = _create_account_web(client, token, "LCAP", "现金")
    contact = _create_contact(client, token, "王五")

    loan = _create_loan(
        client, token, "LCAP", direction="lend", amount=500.0,
        contact_id=contact["id"], account_id=acc, account_name="现金",
    )
    # 第一笔:借 500 还 510 → 结清。
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 510.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    assert _get_loan(client, token, loan["id"])["status"] == "settled"

    # 第二笔:该借款已无未结清本金 → 本金记 0,金额全部算利息。
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 170.0, "interest_amount": 20.0, "repaid_at": NOW.isoformat(),
              "account_id": acc, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    rp = res.json()
    # 本金被压到 0,超出部分归入利息 → 流水金额仍等于实际收付的 170。
    assert rp["amount"] == 0.0
    assert rp["interest_amount"] == 170.0
    txns = _list_txns(client, token, "LCAP")
    flow = _find_flow(txns, rp["tx_sync_id"])
    assert flow is not None and flow["amount"] == 170.0
    assert "含利息 170 元" in (flow["note"] or "")
    # 借款状态:本金没被超还,仍是已结清(总已还本金 = 500)。
    detail = _get_loan(client, token, loan["id"])
    assert detail["total_repaid"] == 500.0
    assert detail["status"] == "settled"


def test_flow_edit_syncs_interest_and_merged_group() -> None:
    """编辑流水金额 → 同步含利息的还款与合并还款组(0042)。

    场景:
      - 单笔含利息的还款:流水金额 = 本金 + 利息。改流水金额时只调整**本金**
        (本金 = 新金额 - 利息),利息不变,避免把利息冲掉;
      - 合并还款组:一条流水对应多笔借款。改流水金额时按比例调整组内各笔
        分摊行,保证「各笔已还本金之和 = 流水金额 - 利息」。
    """
    client = _make_client()
    user = _register_web(client, "loansync2@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LSY2")
    acc_id = _create_account_web(client, token, "LSY2", "现金")
    contact = _create_contact(client, token, "孙七")

    # --- 1) 单笔含利息的还款 ---
    loan = _create_loan(
        client, token, "LSY2", direction="lend", amount=500.0,
        contact_id=contact["id"], account_id=acc_id, account_name="现金",
    )
    res = client.post(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token),
        json={"amount": 510.0, "repaid_at": NOW.isoformat(),
              "account_id": acc_id, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    rp = res.json()
    assert rp["amount"] == 500.0 and rp["interest_amount"] == 10.0
    # 把流水改成 520 → 本金 = 520 - 10(利息不变) = 510
    _patch_tx_amount(client, token, "LSY2", rp["tx_sync_id"], 520.0)
    repayments = client.get(
        f"/api/v1/loans/{loan['id']}/repayments", headers=_auth(token)
    ).json()
    assert repayments[0]["amount"] == 510.0, repayments[0]
    assert repayments[0]["interest_amount"] == 10.0, repayments[0]

    # --- 2) 合并还款组:按比例调整组内分摊行 ---
    loans = []
    for _ in range(3):
        loans.append(
            _create_loan(
                client, token, "LSY2", direction="lend", amount=50.0,
                contact_id=contact["id"], account_id=acc_id, account_name="现金",
            )
        )
    res = client.post(
        "/api/v1/loans/repayments/merged", headers=_auth(token),
        json={"ledger_id": "LSY2", "contact_id": contact["id"], "direction": "lend",
              "amount": 170.0, "interest_amount": 20.0, "repaid_at": NOW.isoformat(),
              "account_id": acc_id, "account_name": "现金"},
    )
    assert res.status_code == 201, res.text
    merged = res.json()
    assert merged["amount"] == 150.0
    # 把合并流水改成 220 → 本金合计 = 220 - 20(利息) = 200,按 50:50:50 分摊
    _patch_tx_amount(client, token, "LSY2", merged["tx_sync_id"], 220.0)
    total_principal = 0.0
    for l in loans:
        reps = client.get(
            f"/api/v1/loans/{l['id']}/repayments", headers=_auth(token)
        ).json()
        for r in reps:
            if r.get("group_id"):
                total_principal += r["amount"]
    assert abs(total_principal - 200.0) < 0.005, total_principal
    # 组信息也同步:group_amount = 实际流水金额
    reps = client.get(
        f"/api/v1/loans/{loans[0]['id']}/repayments", headers=_auth(token)
    ).json()
    assert abs(reps[0]["group_amount"] - 220.0) < 0.005, reps[0]


def test_flow_edit_syncs_loan_principal_with_discount() -> None:
    """编辑带优惠的借出流水 → 借款本金 = 流水金额 + 优惠额(0042)。

    场景(成孝林 2025-02-17):银行只扣款 299.85,但 0.15 是优惠,对方**实际到账 300**。
    借款本金应按**实际到账**记 300,还款也是 300。
    若同步时忽略优惠,本金会被记成 299.85,出现「还款 300 比本金还多」的账实不符。
    """
    client = _make_client()
    user = _register_web(client, "loandisc@example.com")
    token = user["access_token"]
    _create_ledger_web(client, token, "LDS")
    acc_id = _create_account_web(client, token, "LDS", "网商银行")
    contact = _create_contact(client, token, "成孝林")

    loan = _create_loan(
        client, token, "LDS", direction="lend", amount=299.85,
        contact_id=contact["id"], account_id=acc_id, account_name="网商银行",
    )
    tx_id = loan["tx_sync_id"]
    assert tx_id

    # 给借出流水补上优惠 0.15(银行实付 299.85 / 实际到账 300)。
    res = client.patch(
        f"/api/v1/write/ledgers/LDS/transactions/{tx_id}",
        headers=_auth(token),
        json={"base_change_id": 0, "discount_amount": 0.15},
    )
    assert res.status_code == 200, res.text
    # 流水金额没变,但本金要按实际到账 300 记。
    assert _get_loan(client, token, loan["id"])["amount"] == 300.0

    # 再改流水金额 300 → 290:本金 = 290 + 0.15(优惠) = 290.15
    _patch_tx_amount(client, token, "LDS", tx_id, 290.0)
    assert _get_loan(client, token, loan["id"])["amount"] == 290.15

    # 清掉优惠 → 本金 = 流水金额本身
    res = client.patch(
        f"/api/v1/write/ledgers/LDS/transactions/{tx_id}",
        headers=_auth(token),
        json={"base_change_id": 0, "discount_amount": 0.0},
    )
    assert res.status_code == 200, res.text
    assert _get_loan(client, token, loan["id"])["amount"] == 290.0
