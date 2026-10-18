from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from test_account_hidden_sync import _make_client, _login
from src.main import app
from src.models import Ledger, User, UserAccountProjection, ReadTxProjection
from src import projection


def setup_loan():
    client, sessions = _make_client()
    email = f"loan-{uuid4()}@qa.example.com"
    token = _login(client, email)
    headers = {"Authorization": f"Bearer {token}", "X-BeeCount-Capabilities": "loan-flow-v1"}
    app_headers = headers
    web_token = _login(client, email, device_id="web-read", client_type="web")
    headers = {"Authorization": f"Bearer {web_token}", "X-BeeCount-Capabilities": "loan-flow-v1"}
    now = datetime.now(timezone.utc)
    def push(kind, sid, payload, *, ledger="loan-main", action="upsert", capable=True):
        return client.post("/api/v1/sync/push", headers=app_headers if capable else {"Authorization": f"Bearer {token}"},
            json={"device_id": "d1", "changes": [{"ledger_id": ledger, "entity_type": kind,
            "entity_sync_id": sid, "action": action, "updated_at": datetime.now(timezone.utc).isoformat(), "payload": payload}]})
    assert push("ledger", "loan-main", {"syncId": "loan-main", "name": "Loan QA", "currency": "CNY"}).status_code == 200
    assert push("account", "bank", {"syncId": "bank", "name": "QA bank", "type": "bank_card",
        "currency": "CNY", "initialBalance": 20000}).status_code == 200
    assert push("account", "loan", {"syncId": "loan", "name": "QA loan", "type": "loan",
        "currency": "CNY", "initialBalance": -50000, "loanBalanceVersion": 1,
        "loanBalanceSince": (now-timedelta(days=1)).isoformat()}).status_code == 200
    def repayment(amount, *, sid="repay", ledger="loan-main", kind="transfer", happened=None, capable=True):
        return push("transaction", sid, {"syncId": sid, "type": kind, "amount": amount,
            "accountId": "bank" if kind == "transfer" else "loan",
            "fromAccountId": "bank" if kind == "transfer" else "",
            "toAccountId": "loan" if kind == "transfer" else "",
            "happenedAt": (happened or now).isoformat()}, ledger=ledger, capable=capable)
    return client, sessions, email, headers, push, repayment


def account_balance(client, headers):
    response = client.get("/api/v1/read/workspace/accounts", headers=headers)
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()}


def test_principal_round_trip_global_balance_and_no_income_expense():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        assert repay(1000).status_code == 200
        rows = account_balance(client, headers)
        assert rows["bank"]["balance"] == 19000
        assert rows["loan"]["balance"] == -49000
        assert rows["bank"]["expense_total"] == rows["loan"]["income_total"] == 0
        assert repay(800).status_code == 200
        assert account_balance(client, headers)["loan"]["balance"] == -49200
        assert push("transaction", "repay", {}, action="delete").status_code == 200
        assert account_balance(client, headers)["loan"]["balance"] == -50000
        full = client.get("/api/v1/sync/full", params={"ledger_id": "loan-main"}, headers={"Authorization": f"Bearer {_login(client, email)}"})
        assert full.status_code == 200
        assert "loanBalanceVersion" in str(full.json())
    finally:
        app.dependency_overrides.clear()


def test_old_client_cannot_overwrite_upgraded_loan_or_write_repayments():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        response = push("account", "loan", {"name": "old overwrite"}, capable=False)
        assert response.status_code == 426, response.text
        assert repay(1000, capable=False).status_code == 426
        rows = account_balance(client, headers)
        assert rows["loan"]["name"] == "QA loan"
        assert rows["loan"]["balance"] == -50000
        assert rows["bank"]["balance"] == 20000
        # Ordinary accounts remain writable from the old client.
        assert push("account", "bank", {"name": "ordinary rename"}, capable=False).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_amount_kind_baseline_and_multi_ledger_guards():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        assert repay(50001).status_code == 400
        assert repay(1, kind="expense").status_code == 400
        assert repay(1, happened=datetime.now(timezone.utc)-timedelta(days=2)).status_code == 409
        assert repay(40000).status_code == 200
        # Same user-global debt, different ledger: remaining is only 10000.
        denied = repay(20000, sid="other-ledger", ledger="loan-second")
        assert denied.status_code == 400, denied.text
        assert account_balance(client, headers)["loan"]["balance"] == -10000
    finally:
        app.dependency_overrides.clear()


def test_legacy_snapshot_with_history_is_preserved_until_explicit_reconciliation():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        assert push("account", "legacy", {"syncId": "legacy", "name": "QA legacy", "type": "loan",
            "currency": "CNY", "initialBalance": -50000}).status_code == 200
        assert push("transaction", "seed-ledger", {"syncId": "seed-ledger", "type": "expense",
            "amount": 1, "accountId": "bank", "happenedAt": datetime.now(timezone.utc).isoformat()}).status_code == 200
        with sessions() as db:
            user = db.scalar(select(User).where(User.email == email))
            ledger = db.scalar(select(Ledger).where(Ledger.user_id == user.id, Ledger.external_id == "loan-main"))
            projection.upsert_tx(db, ledger_id=ledger.id, user_id=user.id, source_change_id=0,
                payload={"syncId": "legacy-history", "type": "transfer", "amount": 1000,
                    "accountId": "bank", "fromAccountId": "bank", "toAccountId": "legacy",
                    "happenedAt": (datetime.now(timezone.utc)-timedelta(days=7)).isoformat()})
            db.commit()
        assert account_balance(client, headers)["legacy"]["balance"] == -50000
        web = _login(client, email, device_id="web-loan", client_type="web")
        web_headers = {"Authorization": f"Bearer {web}", "X-BeeCount-Capabilities": "loan-flow-v1",
                       "Idempotency-Key": f"loan-review-{uuid4()}"}
        body = {"base_change_id": 0, "loan_confirmed_debt": 50000}
        for _ in range(2):
            response = client.patch("/api/v1/write/ledgers/loan-main/accounts/legacy",
                headers=web_headers, json=body)
            assert response.status_code == 200, response.text
        row = account_balance(client, headers)["legacy"]
        assert row["balance"] == -50000
        assert row["initial_balance"] == -50000
        assert row["loan_balance_version"] == 1
    finally:
        app.dependency_overrides.clear()


def test_mobile_account_partial_update_keeps_loan_metadata_and_snapshot():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        before = account_balance(client, headers)["loan"]
        assert push("account", "loan", {"name": "Renamed loan"}).status_code == 200
        row = account_balance(client, headers)["loan"]
        assert row["loan_balance_version"] == 1
        assert row["loan_balance_since"] == before["loan_balance_since"]
        assert row["initial_balance"] == -50000
        full = client.get("/api/v1/sync/full", params={"ledger_id": "loan-main"}, headers=headers).json()
        import json
        content = json.loads(full["snapshot"]["payload"]["content"])
        account = next(a for a in content["accounts"] if a["syncId"] == "loan")
        assert account["loanBalanceVersion"] == 1
        assert account["loanBalanceSince"] is not None
        assert push("account", "loan", {"loanBalanceVersion": 0}).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_name_only_transfer_cannot_bypass_loan_guard_and_notes_remain_editable():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        response = push("transaction", "alias", {"syncId": "alias", "type": "transfer", "amount": 50001,
            "fromAccountName": "QA bank", "toAccountName": "QA loan", "happenedAt": datetime.now(timezone.utc).isoformat()})
        assert response.status_code == 400
        assert repay(1000).status_code == 200
        assert push("transaction", "repay", {"note": "本金还款备注"}).status_code == 200
        assert account_balance(client, headers)["loan"]["balance"] == -49000
    finally:
        app.dependency_overrides.clear()


def test_mcp_read_reports_current_principal_instead_of_only_baseline(monkeypatch):
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        assert repay(1000).status_code == 200
        from src.mcp.tools import read_tools
        monkeypatch.setattr(read_tools, "SessionLocal", sessions)
        with sessions() as db:
            user = db.scalar(select(User).where(User.email == email))
            rows = read_tools.list_accounts(user, account_type="loan")
        assert len(rows) == 1
        assert rows[0]["initial_balance"] == -50000
        assert rows[0]["balance"] == -49000
        assert rows[0]["loan_balance_version"] == 1
    finally:
        app.dependency_overrides.clear()


def test_sync_can_restore_pre_baseline_history_without_moving_new_principal():
    client, sessions, email, headers, push, repay = setup_loan()
    try:
        payload = {"syncId": "historical", "type": "transfer", "amount": 1000,
            "accountId": "bank", "fromAccountId": "bank", "toAccountId": "loan",
            "happenedAt": (datetime.now(timezone.utc)-timedelta(days=7)).isoformat(), "loanHistoryImport": True}
        assert push("transaction", "historical", payload).status_code == 200
        assert account_balance(client, headers)["loan"]["balance"] == -50000
        assert account_balance(client, headers)["bank"]["balance"] == 19000
        assert push("transaction", "historical", {"amount": 900}).status_code == 409
        assert push("transaction", "historical", {}, action="delete").status_code == 409
    finally:
        app.dependency_overrides.clear()
