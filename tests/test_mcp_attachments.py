"""MCP 小票附件：真实写 router + 私有测试存储；实服 transport 另由 QA 场景验证。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src import _mcp_internal_client
from src.config import get_settings
from src.database import Base, get_db
from src.deps import require_any_scopes
from src.main import app
from src.mcp import server
from src.mcp.tools import read_tools, write_tools
from src.models import AttachmentFile, User
from src.routers import attachments as attachment_router
from src.routers.write import _shared as write_shared
from src.security import SCOPE_APP_WRITE, SCOPE_WEB_WRITE


@pytest.fixture
def ledger(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    scope_dep = require_any_scopes(SCOPE_WEB_WRITE, SCOPE_APP_WRITE)
    app.dependency_overrides[write_shared._WRITE_SCOPE_DEP] = scope_dep
    app.dependency_overrides[attachment_router._WRITE_SCOPE_DEP] = scope_dep
    for module in (write_tools, read_tools, server):
        monkeypatch.setattr(module, "SessionLocal", sessions)
    monkeypatch.setattr(get_settings(), "attachment_storage_dir", str(tmp_path / "attachments"))
    client = TestClient(app)

    def register(email):
        response = client.post("/api/v1/auth/register", json={
            "email": email, "password": "private-unit-test", "client_type": "web",
            "device_name": "mcp-attachment-test", "platform": "web",
        })
        assert response.status_code == 200
        auth = response.json()
        headers = {"Authorization": f"Bearer {auth['access_token']}", "X-Device-ID": auth["device_id"]}
        response = client.post("/api/v1/write/ledgers", json={"ledger_name": "Receipts", "currency": "CNY"}, headers=headers)
        assert response.status_code == 200
        with sessions() as db:
            user = db.scalar(select(User).where(User.email == email))
            db.expunge(user)
        return user, response.json()["entity_id"], headers

    user, ledger_id, headers = register("receipts@example.com")
    try:
        yield SimpleNamespace(user=user, ledger_id=ledger_id, headers=headers,
                              client=client, sessions=sessions, register=register)
    finally:
        app.dependency_overrides.clear()
        client.close()
        engine.dispose()


def run(coro):
    async def invoke():
        try:
            return await coro
        finally:
            await _mcp_internal_client.close_internal_client()
    return asyncio.run(invoke())


def upload(ctx, content=b"synthetic receipt", **kwargs):
    return run(write_tools.upload_attachment(
        ctx.user, ledger_id=ctx.ledger_id, file_name="receipt.png",
        content_base64=base64.b64encode(content).decode(), **kwargs,
    ))


def create(ctx, ids=None):
    return run(write_tools.create_transaction(ctx.user, ledger_id=ctx.ledger_id, amount=12.5, attachments=ids))


def read(ctx, sid):
    return read_tools.get_transaction(ctx.user, sync_id=sid)


def test_upload_uses_existing_dedup_and_real_file(ledger):
    first, second = upload(ledger), upload(ledger)
    assert first["file_id"] == second["file_id"]
    assert first["mime_type"] == "image/png"
    with ledger.sessions() as db:
        rows = db.scalars(select(AttachmentFile)).all()
        assert len(rows) == 1
        assert Path(rows[0].storage_path).read_bytes() == b"synthetic receipt"
        assert rows[0].sha256 == hashlib.sha256(b"synthetic receipt").hexdigest()
    assert read_tools.list_transactions(ledger.user)["total"] == 0


@pytest.mark.parametrize("content,error", [
    ("", "empty"), ("not base64", "valid standard Base64"),
    ("data:image/png;base64,aGVsbG8=", "valid standard Base64"),
    ("中文", "valid standard Base64"),
])
def test_invalid_upload_does_not_write(ledger, content, error):
    with pytest.raises(ValueError, match=error):
        run(write_tools.upload_attachment(ledger.user, ledger_id=ledger.ledger_id,
                                         file_name="receipt.png", content_base64=content))
    with ledger.sessions() as db:
        assert db.scalars(select(AttachmentFile)).all() == []


@pytest.mark.parametrize("size", [4, 5])
def test_upload_rejects_encoded_and_decoded_limits(ledger, monkeypatch, size):
    monkeypatch.setattr(get_settings(), "attachment_max_upload_bytes", 3 if size == 4 else 4)
    with pytest.raises(ValueError, match="too large"):
        upload(ledger, b"x" * size)


def test_create_multiple_same_name_files_has_unique_identity_and_order(ledger):
    a, b = upload(ledger, b"a"), upload(ledger, b"b")
    result = create(ledger, [b["file_id"], a["file_id"]])
    refs = read(ledger, result["sync_id"])["attachments"]
    assert refs == result["attachments"]
    assert [x["cloudFileId"] for x in refs] == [b["file_id"], a["file_id"]]
    assert [x["sortOrder"] for x in refs] == [0, 1]
    assert len({x["fileName"] for x in refs}) == 2
    for ref in refs:
        assert ref["originalName"] == "receipt.png"
        assert ref["fileSize"] == 1
        assert ref["cloudSha256"] in [a["sha256"], b["sha256"]]


@pytest.mark.parametrize("ids", [["missing"], [""], [" "], ["https://example.com/receipt.png"]])
def test_invalid_references_never_create_transaction(ledger, ids):
    with pytest.raises(ValueError):
        create(ledger, ids)
    assert read_tools.list_transactions(ledger.user)["total"] == 0


def test_duplicate_references_rejected_before_create(ledger):
    file_id = upload(ledger)["file_id"]
    with pytest.raises(ValueError, match="Duplicate"):
        create(ledger, [file_id, file_id])
    assert read_tools.list_transactions(ledger.user)["total"] == 0


@pytest.mark.parametrize("foreign_user", [False, True])
def test_foreign_ledger_references_rejected_without_leaking_existence(ledger, foreign_user):
    if foreign_user:
        owner, lid, _ = ledger.register("other@example.com")
    else:
        response = ledger.client.post("/api/v1/write/ledgers", json={"ledger_name": "Other", "currency": "CNY"}, headers=ledger.headers)
        owner, lid = ledger.user, response.json()["entity_id"]
    other = SimpleNamespace(user=owner, ledger_id=lid)
    file_id = upload(other)["file_id"]
    with pytest.raises(ValueError, match="not found in the target ledger"):
        create(ledger, [file_id])


def test_category_icon_cannot_be_attached(ledger):
    file_id = upload(ledger)["file_id"]
    with ledger.sessions() as db:
        db.get(AttachmentFile, file_id).attachment_kind = "category_icon"
        db.commit()
    with pytest.raises(ValueError, match="not found in the target ledger"):
        create(ledger, [file_id])


def test_update_preserves_reorders_appends_replaces_and_clears(ledger):
    a, b, c = [upload(ledger, value)["file_id"] for value in [b"a", b"b", b"c"]]
    sid = create(ledger, [a, b])["sync_id"]
    for patch in [{"note": "changed"}, {"attachments": None, "amount": 14}]:
        run(write_tools.update_transaction(ledger.user, sync_id=sid, **patch))
        assert [x["cloudFileId"] for x in read(ledger, sid)["attachments"]] == [a, b]
    for ids in [[b, a], [b, a, c], [c], []]:
        result = run(write_tools.update_transaction(ledger.user, sync_id=sid, attachments=ids))
        refs = read(ledger, sid)["attachments"]
        assert [x["cloudFileId"] for x in refs] == ids
        assert [x["sortOrder"] for x in refs] == list(range(len(ids)))
        assert result["attachments"] == refs
    assert read_tools.list_transactions(ledger.user)["total"] == 1


def test_failed_update_keeps_existing_files_and_fields(ledger):
    file_id = upload(ledger)["file_id"]
    sid = create(ledger, [file_id])["sync_id"]
    before = read(ledger, sid)
    with pytest.raises(ValueError):
        run(write_tools.update_transaction(ledger.user, sync_id=sid, amount=999, attachments=["unknown"]))
    after = read(ledger, sid)
    assert after["amount"] == before["amount"]
    assert after["attachments"] == before["attachments"]


def test_mcp_schema_has_upload_and_ordered_optional_ids():
    tools = server.mcp._tool_manager._tools
    assert {"file_name", "content_base64"} <= set(tools["upload_attachment"].parameters["required"])
    for name in ["create_transaction", "update_transaction"]:
        prop = tools[name].parameters["properties"]["attachments"]
        assert {"type": "array", "items": {"type": "string"}} in prop["anyOf"]
        assert "attachments" not in tools[name].parameters.get("required", [])


def test_read_only_scope_cannot_upload(ledger):
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(scope={
        "bc_mcp_user": ledger.user, "bc_mcp_scopes": {"mcp:read"},
    })))
    with pytest.raises(Exception, match="mcp:write"):
        run(server.upload_attachment(ctx, file_name="receipt.png", content_base64="aGk="))
    with ledger.sessions() as db:
        assert db.scalars(select(AttachmentFile)).all() == []


def test_attachment_call_summary_does_not_store_filename_or_bytes():
    summary = server._summarize_args({
        "file_name": "private receipt location.png", "content_base64": "PRIVATE_BINARY",
        "ledger_id": str(uuid4()), "attachments": [str(uuid4())],
    })
    assert "PRIVATE" not in summary and "private" not in summary
    assert "content_base64" not in summary and "file_name" not in summary
    assert "attachments=[1]" in summary


def test_no_attachments_keeps_old_create_behavior(ledger):
    result = create(ledger)
    assert "attachments" not in result
    assert read(ledger, result["sync_id"])["attachments"] == []
