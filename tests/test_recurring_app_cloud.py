from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from test_mcp_attachments import ledger as ledger
from test_mcp_attachments import run

from src.mcp import rule_tools as rules
from src.mcp.tools import read_tools
from src.models import MCPStateRecord, ReadTxProjection, SyncChange


def route(ctx):
    return f"/api/v1/ledgers/{ctx.ledger_id}/recurring-rules"


def submit(ctx, payload, headers=None, status=200):
    response = ctx.client.post(route(ctx), json=payload, headers=headers or ctx.headers)
    assert response.status_code == status, response.text
    return response.json()


def sample(**kwargs):
    return {
        "action": "create",
        "client_id": str(uuid4()),
        "data": {
            "name": "QA rent",
            "transaction": {"amount": 32, "note": "rent"},
            "frequency": "monthly",
            "starts_at": "2030-01-01",
            "day_of_month": 5,
            "time_zone": "UTC",
        },
        "confirm": True,
        **kwargs,
    }


def app_session(ctx, name="phone"):
    res = ctx.client.post(
        "/api/v1/auth/login",
        json={
            "email": "receipts@example.com",
            "password": "private-unit-test",
            "client_type": "app",
            "device_name": name,
        },
    )
    assert res.status_code == 200, res.text
    auth = res.json()
    return {"Authorization": f"Bearer {auth['access_token']}", "X-Device-ID": auth["device_id"]}


def test_app_web_and_mcp_share_a_rule_and_check_revisions(ledger):
    mobile = app_session(ledger)
    draft = sample()
    assert submit(ledger, {**draft, "confirm": False}, mobile)["next_occurrences"][0].startswith(
        "2030-01-05"
    )
    assert ledger.client.get(route(ledger), headers=mobile).json()["items"] == []
    first = submit(ledger, draft, mobile)
    assert submit(ledger, draft, mobile)["id"] == first["id"]
    submit(ledger, {**draft, "data": {**draft["data"], "name": "different"}}, mobile, 409)
    (row,) = ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]
    assert row["id"] == first["id"] and row["can_edit"]
    update = {
        "action": "update",
        "record_id": row["id"],
        "expected_revision": row["revision"],
        "data": {**draft["data"], "transaction": {"amount": 38}},
        "confirm": True,
    }
    edited = submit(ledger, update)
    submit(ledger, update, mobile, 409)
    assert (
        ledger.client.get(route(ledger), headers=mobile).json()["items"][0]["data"]["rule"][
            "transaction"
        ]["amount"]
        == 38
    )
    paused = run(
        rules.manage_recurring_rule(
            ledger.user,
            "pause",
            ledger.ledger_id,
            record_id=row["id"],
            expected_revision=edited["revision"],
            confirm=True,
        )
    )
    assert paused["status"] == "paused"
    removed = submit(
        ledger,
        {
            "action": "delete",
            "record_id": row["id"],
            "expected_revision": paused["revision"],
            "confirm": True,
        },
        mobile,
    )
    assert removed["status"] == "deleted"
    assert submit(ledger, draft, mobile)["status"] == "deleted"
    assert ledger.client.get(route(ledger), headers=ledger.headers).json()["items"] == []
    _, _, unrelated = ledger.register("unrelated@example.com")
    assert ledger.client.get(route(ledger), headers=unrelated).status_code == 404


def test_cloud_occurrence_retries_emit_one_sync_transaction_and_move_the_next_month(ledger):
    draft = sample()
    row = submit(ledger, draft)
    now = datetime(2030, 1, 10, tzinfo=timezone.utc)
    result = run(rules.execute_due_rules(now=now))[0]["operation"]
    assert result["created_count"] == 1
    assert run(rules.execute_due_rules(now=now)) == []
    (current,) = ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]
    new = submit(
        ledger,
        {
            "action": "update",
            "record_id": row["id"],
            "expected_revision": current["revision"],
            "data": {**draft["data"], "day_of_month": 15},
            "confirm": True,
        },
    )
    assert new["next_occurrences"][0].startswith("2030-02-15")
    assert run(rules.execute_due_rules(now=datetime(2030, 1, 31, tzinfo=timezone.utc))) == []
    with ledger.sessions() as db:
        (tx,) = db.scalars(select(ReadTxProjection)).all()
        changes = db.scalars(
            select(SyncChange).where(
                SyncChange.entity_type == "transaction", SyncChange.entity_sync_id == tx.sync_id
            )
        ).all()
        assert len(changes) == 1 and tx.sync_id == result["sync_ids"][0]


def test_list_distinguishes_a_handoff_cursor_from_an_actual_local_posting_date(ledger):
    draft = sample()
    draft["data"]["time_zone"] = "Pacific/Kiritimati"
    submit(ledger, {**draft, "migrate": True, "consumed_through": "2030-01-05"})

    def current():
        (item,) = ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]
        return item

    assert current()["last_generated_local_date"] is None
    assert current()["next_local"].startswith("2030-02-05")
    run(rules.execute_due_rules(now=datetime(2030, 2, 4, 11, tzinfo=timezone.utc)))
    assert current()["data"]["last_due_at"].startswith("2030-02-04")
    assert current()["last_generated_local_date"] == "2030-02-05"


def test_two_phone_handoffs_reconcile_the_same_period_and_resume_after_lost_receipt(ledger):
    draft = sample()
    first_id, second_id = str(uuid4()), str(uuid4())
    handoff = {
        **draft,
        "migrate": True,
        "consumed_through": "2030-01-05",
        "local_transactions": [{"date": "2030-01-05", "sync_id": first_id}],
    }
    preview = submit(ledger, {**handoff, "confirm": False})
    first = submit(ledger, {**handoff, "reviewed_mapping": preview["reviewed_mapping"]})
    assert first["next_occurrences"][0].startswith("2030-02-05")
    assert first["required_uploads"] == [first_id]
    (listed,) = ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]
    assert listed["last_generated_local_date"] == "2030-01-05"
    assert submit(ledger, handoff)["local_transactions"] == {first_id: first_id}
    other = {
        **handoff,
        "client_id": str(uuid4()),
        "local_transactions": [{"date": "2030-01-05", "sync_id": second_id}],
    }
    preview = submit(ledger, {**other, "confirm": False})
    submit(ledger, {**other, "reviewed_mapping": preview["reviewed_mapping"]}, status=409)
    link = {
        **other,
        "action": "update",
        "record_id": first["id"],
        "expected_revision": first["revision"],
    }
    preview = submit(ledger, {**link, "confirm": False})
    assert preview["local_transactions"] == {second_id: first_id}
    result = submit(ledger, {**link, "reviewed_mapping": preview["reviewed_mapping"]})
    assert submit(ledger, {**other, "confirm": False})["id"] == result["id"]
    assert result["required_uploads"] == []
    assert run(rules.execute_due_rules(now=datetime(2030, 1, 31, tzinfo=timezone.utc))) == []
    assert len(ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]) == 1


def test_legacy_push_stays_pending_and_modern_manual_transactions_still_sync(ledger):
    mobile = app_session(ledger)
    row = submit(ledger, sample())
    due = run(rules.execute_due_rules(now=datetime(2030, 1, 7, tzinfo=timezone.utc)))[0]
    (canonical,) = due["operation"]["sync_ids"]
    sid = str(uuid4())
    change = {
        "ledger_id": ledger.ledger_id,
        "entity_type": "transaction",
        "entity_sync_id": sid,
        "action": "upsert",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "syncId": sid,
            "type": "expense",
            "amount": 32,
            "happenedAt": "2030-01-05T00:00:00Z",
            "localRecurringOrigin": True,
        },
    }

    def push(headers, changes):
        return ledger.client.post(
            "/api/v1/sync/push",
            headers=headers,
            json={"device_id": mobile["X-Device-ID"], "changes": changes},
        )

    assert push(mobile, [change]).status_code == 426
    modern = {**mobile, "X-BeeCount-Recurring": "cloud-v1"}
    assert push(modern, [change]).status_code == 409
    (current,) = ledger.client.get(route(ledger), headers=mobile).json()["items"]
    link = {
        **sample(),
        "action": "update",
        "record_id": row["id"],
        "expected_revision": current["revision"],
        "migrate": True,
        "local_transactions": [{"date": "2030-01-05", "sync_id": sid}],
        "consumed_through": "2030-01-05",
    }
    preview = submit(ledger, {**link, "confirm": False}, mobile)
    assert preview["local_transactions"] == {sid: canonical}
    submit(ledger, {**link, "reviewed_mapping": preview["reviewed_mapping"]}, mobile)
    assert push(modern, [change]).status_code == 409
    assert read_tools.list_transactions(ledger.user)["total"] == 1
    manual = {**change, "payload": {**change["payload"], "localRecurringOrigin": False}}
    response = push(modern, [manual])
    assert response.status_code == 200, response.text
    assert read_tools.list_transactions(ledger.user)["total"] == 2


def test_worker_overlap_and_crash_after_post_recover_one_receipt(ledger, monkeypatch):
    import asyncio
    from datetime import timedelta

    from src.mcp.tools import write_tools

    row = submit(ledger, sample())
    now = datetime(2030, 1, 7, tzinfo=timezone.utc)
    real = write_tools.create_transactions

    async def overlap():
        started, release = asyncio.Event(), asyncio.Event()

        async def post(*args, **kwargs):
            started.set()
            await release.wait()
            await real(*args, **kwargs)
            raise RuntimeError("simulated worker exit after durable transaction")

        monkeypatch.setattr(write_tools, "create_transactions", post)
        first = asyncio.create_task(rules.execute_due_rules(now=now))
        await started.wait()
        try:
            assert await rules.execute_due_rules(now=now) == []
        finally:
            release.set()
        assert (await first)[0]["status"] == "failed"

    run(overlap())
    assert read_tools.list_transactions(ledger.user)["total"] == 1
    with ledger.sessions() as db:
        record = db.get(MCPStateRecord, row["id"])
        assert record.status == "executing"
        record.updated_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        db.commit()
    monkeypatch.setattr(write_tools, "create_transactions", real)
    assert run(rules.execute_due_rules(now=now))[0]["operation"]["status"] == "created"
    assert run(rules.execute_due_rules(now=now)) == []
    assert read_tools.list_transactions(ledger.user)["total"] == 1
    (current,) = ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]
    assert current["data"]["executed_count"] == 1


def test_automatic_adoption_uses_shared_transaction_identity_and_preserves_cloud_edits(ledger):
    first_tx = str(uuid4())
    first_phone = {
        **sample(),
        "automatic": True,
        "migrate": True,
        "consumed_through": "2030-01-05",
        "local_transactions": [{"date": "2030-01-05", "sync_id": first_tx}],
    }

    def confirmed(change):
        preview = submit(ledger, {**change, "confirm": False})
        return submit(
            ledger, {**change, "confirm": True, "reviewed_mapping": preview["reviewed_mapping"]}
        )

    created = confirmed(first_phone)
    original = first_phone["data"]
    edited = {
        **original,
        "name": "Updated rent",
        "transaction": {**original["transaction"], "amount": 98},
    }
    current = submit(
        ledger,
        {
            "action": "update",
            "record_id": created["id"],
            "expected_revision": created["revision"],
            "data": edited,
            "confirm": True,
        },
    )
    other_phone = {**first_phone, "client_id": str(uuid4())}
    matched = submit(ledger, {**other_phone, "confirm": False})["matched_rule"]
    assert matched["id"] == current["id"] and matched["data"]["rule"]["transaction"]["amount"] == 98
    linked = confirmed(
        {
            **other_phone,
            "action": "update",
            "record_id": matched["id"],
            "expected_revision": matched["revision"],
            "data": matched["data"]["rule"],
        }
    )
    assert linked["id"] == created["id"] and linked["local_transactions"] == {first_tx: first_tx}
    assert submit(ledger, {**other_phone, "confirm": False})["handoff_confirmed"]

    # A separate install with the original fields, but no shared transaction ID,
    # needs a human decision even after a web edit changed both amount and name.
    ambiguous = {**first_phone, "client_id": str(uuid4()), "local_transactions": []}
    assert "SIMILAR_RULE" in submit(ledger, ambiguous, status=409)["detail"]
    assert len(ledger.client.get(route(ledger), headers=ledger.headers).json()["items"]) == 1
    separate = confirmed({**ambiguous, "distinct": True, "automatic": False})
    assert separate["id"] != linked["id"]


def test_replayed_receipt_does_not_reupload_a_deleted_occurrence(ledger):
    sid = str(uuid4())
    request = {
        **sample(),
        "migrate": True,
        "automatic": True,
        "consumed_through": "2030-01-05",
        "local_transactions": [{"date": "2030-01-05", "sync_id": sid}],
    }
    preview = submit(ledger, {**request, "confirm": False})
    first = submit(ledger, {**request, "reviewed_mapping": preview["reviewed_mapping"]})
    assert first["required_uploads"] == [sid]
    mobile = app_session(ledger)

    def push(action):
        return ledger.client.post(
            "/api/v1/sync/push",
            headers={**mobile, "X-BeeCount-Recurring": "cloud-v1"},
            json={
                "device_id": mobile["X-Device-ID"],
                "changes": [
                    {
                        "ledger_id": ledger.ledger_id,
                        "entity_type": "transaction",
                        "entity_sync_id": sid,
                        "action": action,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "payload": {
                            "syncId": sid,
                            "type": "expense",
                            "amount": 10,
                            "happenedAt": "2030-01-05T00:00:00Z",
                            "localRecurringOrigin": True,
                        },
                    }
                ],
            },
        )

    assert push("upsert").status_code == 200
    assert submit(ledger, request)["required_uploads"] == []
    assert push("delete").status_code == 200
    replay = submit(ledger, request)
    assert replay["required_uploads"] == [] and replay["deleted_transactions"] == [sid]
    assert push("upsert").status_code == 409


def test_migration_preserves_several_postings_from_one_local_history(ledger):
    postings = [
        {"sync_id": str(uuid4()), "date": day}
        for day in ("2030-01-05", "2030-01-12", "2030-01-12")
    ]
    change = sample(
        migrate=True,
        automatic=True,
        consumed_through="2030-01-12",
        local_transactions=postings,
    )
    expected = {tx["sync_id"]: tx["sync_id"] for tx in postings}
    preview = submit(ledger, {**change, "confirm": False})
    assert preview["local_transactions"] == expected
    first = submit(ledger, {**change, "reviewed_mapping": preview["reviewed_mapping"]})
    assert first["local_transactions"] == expected
    assert set(first["required_uploads"]) == set(expected)
    assert submit(ledger, change)["local_transactions"] == expected
    assert first["next_occurrences"][0].startswith("2030-02-05")
    ambiguous = {
        **change,
        "client_id": str(uuid4()),
        "action": "update",
        "record_id": first["id"],
        "expected_revision": first["revision"],
        "local_transactions": [{"sync_id": str(uuid4()), "date": "2030-01-12"}],
        "confirm": False,
    }
    assert "HISTORY_CONFLICT" in submit(ledger, ambiguous, status=409)["detail"]


def test_old_app_can_sync_with_existing_mcp_rules_and_delete_transactions(ledger):
    mobile = app_session(ledger)
    rule = sample()["data"]
    run(
        rules.manage_recurring_rule(
            ledger.user, "create", ledger.ledger_id, rules.RecurringRule(**rule), confirm=True
        )
    )
    sid = str(uuid4())

    def push(action):
        return ledger.client.post(
            "/api/v1/sync/push",
            headers=mobile,
            json={
                "device_id": mobile["X-Device-ID"],
                "changes": [
                    {
                        "ledger_id": ledger.ledger_id,
                        "entity_type": "transaction",
                        "entity_sync_id": sid,
                        "action": action,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "payload": {
                            "syncId": sid,
                            "type": "expense",
                            "amount": 12,
                            "happenedAt": "2030-01-05T00:00:00Z",
                        },
                    }
                ],
            },
        )

    assert push("upsert").status_code == 200
    modern = submit(ledger, sample())
    assert push("upsert").status_code == 426
    assert push("delete").status_code == 200
    submit(
        ledger,
        {"action": "delete", "record_id": modern["id"], "expected_revision": modern["revision"], "confirm": True},
    )
    assert push("upsert").status_code == 426
    assert read_tools.list_transactions(ledger.user)["total"] == 0


def test_restore_cannot_revive_deleted_cloud_occurrence_or_drop_rest_of_batch(ledger):
    created = submit(ledger, sample())
    posted = run(rules.execute_due_rules(now=datetime(2030, 1, 7, tzinfo=timezone.utc)))
    assert posted[0]["rule_id"] == created["id"]
    (occurrence,) = posted[0]["operation"]["sync_ids"]
    mobile = app_session(ledger)

    def tx_change(sid, action, *, restore=False):
        return {
            "ledger_id": ledger.ledger_id,
            "entity_type": "transaction",
            "entity_sync_id": sid,
            "action": action,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "payload": {
                "syncId": sid,
                "type": "expense",
                "amount": 32,
                "happenedAt": "2030-01-05T00:00:00Z",
                **({"restoredSnapshot": True} if restore else {}),
            },
        }

    def push(*changes):
        response = ledger.client.post(
            "/api/v1/sync/push",
            headers={**mobile, "X-BeeCount-Recurring": "cloud-v1"},
            json={"device_id": mobile["X-Device-ID"], "changes": changes},
        )
        assert response.status_code == 200, response.text
        return response.json()

    push(tx_change(occurrence, "delete"))
    manual = str(uuid4())
    result = push(tx_change(occurrence, "upsert", restore=True), tx_change(manual, "upsert", restore=True))
    assert result["accepted"] == 1 and result["rejected"] == 1
    assert result["conflict_samples"][0]["reason"] == "restore_deleted_transaction"
    assert read_tools.list_transactions(ledger.user)["total"] == 1
