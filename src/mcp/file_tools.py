"""Original-byte attachment access, draft-only recognition and persistent file imports/exports."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.types import CallToolResult, ImageContent, ResourceLink, TextContent
from pydantic import Field
from sqlalchemy import select

from ..config import get_settings
from ..models import AttachmentFile, ReadTxProjection, User
from .capability_tools import Fields, require_ledger
from .query_context import date_boundary, revision
from .schemas import BatchTransactionItem
from .tools import read_tools as rt
from .tools import write_tools as wt
from .workflow_tools import ImportCandidate, SourceInput, preview_import


class ImportMapping(Fields):
    tx_type: str | None = None
    amount: str | None = None
    happened_at: str | None = None
    category_name: str | None = None
    subcategory_name: str | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    note: str | None = None
    currency: str | None = None
    tags: list[str] = Field(default_factory=list)
    datetime_format: str | None = None
    strip_currency_symbols: bool = True
    expense_is_negative: bool = False
    tz_offset_minutes: int | None = Field(default=None, ge=-720, le=840)


def attachment_bytes(user, file_id):
    with rt.SessionLocal() as db:
        row = db.get(AttachmentFile, file_id)
        if not row or not row.ledger_id or row.attachment_kind != "transaction":
            raise ValueError("Attachment not found")
        from ..models import Ledger

        ledger = db.get(Ledger, row.ledger_id)
        if not ledger:
            raise ValueError("Attachment not found")
        require_ledger(db, user, ledger.external_id)
        root = Path(get_settings().attachment_storage_dir).expanduser().resolve()
        path = Path(row.storage_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Attachment storage unavailable")
        if path.stat().st_size > get_settings().attachment_max_upload_bytes:
            raise ValueError("Attachment exceeds configured size limit")
        raw = path.read_bytes()
        if len(raw) != row.size_bytes or hashlib.sha256(raw).hexdigest() != row.sha256:
            raise ValueError("Attachment integrity check failed")
        metadata = dict(
            file_id=row.id,
            ledger_id=ledger.external_id,
            file_name=row.file_name,
            mime_type=row.mime_type or "application/octet-stream",
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            resource_uri="beecount://attachments/" + row.id,
        )
        return metadata, raw


def read_attachment(user: User, file_id: str) -> CallToolResult:
    """Read an original receipt image or small text attachment after checking current ledger membership. Large/binary files return an authenticated MCP resource link; bytes are never resized or transcoded."""
    metadata, raw = attachment_bytes(user, file_id)
    content = [TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False))]
    if (
        metadata["mime_type"] in {"image/png", "image/jpeg", "image/webp", "image/gif"}
        and len(raw) <= 8 * 1024 * 1024
    ):
        content.append(
            ImageContent(
                type="image", data=base64.b64encode(raw).decode(), mimeType=metadata["mime_type"]
            )
        )
    elif len(raw) <= 65536 and (
        metadata["mime_type"].startswith("text/") or metadata["mime_type"] == "application/json"
    ):
        content.append(TextContent(type="text", text=raw.decode("utf-8-sig", errors="replace")))
    else:
        content.append(
            ResourceLink(
                type="resource_link",
                uri=metadata["resource_uri"],
                name=metadata["file_name"] or file_id,
                mimeType=metadata["mime_type"],
            )
        )
    return CallToolResult(content=content)


def list_attachments(
    user: User, ledger_id: str, q: str | None = None, limit: int = 40, offset: int = 0
) -> dict[str, Any]:
    """Find receipt files and their linked transactions, with filename/note search and pagination."""
    from ..routers.read.attachment_gallery import list_gallery_attachments

    with rt.SessionLocal() as db:
        require_ledger(db, user, ledger_id)
        return list_gallery_attachments(
            ledger_id=ledger_id,
            q=q,
            category_sync_id=None,
            date_from=None,
            date_to=None,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
            _scopes=set(),
            current_user=user,
            db=db,
        ).model_dump(mode="json")


async def parse_receipt(user: User, file_id: str, locale: str = "zh") -> dict[str, Any]:
    """Use the user's configured BeeCount vision provider to recognize an uploaded receipt into drafts. No transaction is created. Keep file_id as the attachment when creating approved drafts."""
    metadata, raw = attachment_bytes(user, file_id)
    result = await wt._self_call(
        "POST",
        f"{get_settings().api_prefix}/ai/parse-tx-image",
        user,
        data={"ledger_id": metadata["ledger_id"], "locale": locale},
        files={"image": (metadata["file_name"], raw, metadata["mime_type"])},
    )
    return {
        **result,
        "file_id": file_id,
        "ledger_id": metadata["ledger_id"],
        "writes_performed": False,
        "attachments": [file_id],
    }


async def preview_text_transactions(
    user: User, ledger_id: str, text: str, locale: str = "zh"
) -> dict[str, Any]:
    """Ask the user's configured BeeCount text provider for drafts without recording transactions. Review drafts, then call create_transaction(s) with a stable idempotency key."""
    with rt.SessionLocal() as db:
        require_ledger(db, user, ledger_id)
    result = await wt._self_call(
        "POST",
        f"{get_settings().api_prefix}/ai/parse-tx-text",
        user,
        json=dict(ledger_id=ledger_id, text=text, locale=locale),
    )
    return {**result, "writes_performed": False}


async def preview_import_file(
    user: User,
    file_id: str,
    mapping: ImportMapping | None = None,
    source_system: str = "file",
    source_id_column: str | None = None,
    time_zone: str | None = None,
    offset: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Parse an uploaded CSV/TSV/XLSX with the production parser, return mappings/errors and save a durable import preview. Each plan covers at most 200 source rows; use next_offset for large files. No transaction is written."""
    from ..services.import_data.parser import parse_csv_text, parse_excel_bytes
    from ..services.import_data.schema import ImportFieldMapping
    from ..services.import_data.transformer import apply_mapping

    metadata, raw = attachment_bytes(user, file_id)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("Import files must be at most 16 MiB")
    if (metadata["file_name"] or "").lower().endswith(".xlsx") or raw[:4] == b"PK\x03\x04":
        import zipfile

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Expanded workbook exceeds 128 MiB")
        parsed = parse_excel_bytes(payload=raw)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gbk")
        parsed = parse_csv_text(raw_text=text)
    if len(parsed.rows) > 50000:
        raise ValueError("Import files may contain at most 50000 rows")
    selected = ImportFieldMapping(**mapping.model_dump()) if mapping else parsed.suggested_mapping
    mapping_out = asdict(selected)
    selected_rows = parsed.rows[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
    txs, errors, warnings = apply_mapping(rows=selected_rows, mapping=selected)
    out = dict(
        file_id=file_id,
        file_sha256=metadata["sha256"],
        headers=parsed.headers,
        suggested_mapping=mapping_out,
        total_rows=len(parsed.rows),
        offset=max(0, offset),
        next_offset=offset + len(selected_rows)
        if offset + len(selected_rows) < len(parsed.rows)
        else None,
        errors=[asdict(error) for error in errors],
        warnings=[asdict(warning) for warning in warnings],
    )
    if errors or not txs:
        return {**out, "status": "mapping_or_data_errors", "plan": None}
    rows_by_number = {row.row_number: row for row in selected_rows}
    candidates = []
    for tx in txs:
        happened = tx.happened_at
        if selected.tz_offset_minutes is None:
            # The legacy parser labels naive values as UTC. Recover the raw wall clock,
            # preserve real explicit offsets, then use the Cloud ingestion timezone contract.
            from ..services.import_data.transformer import _DATETIME_CANDIDATES

            source_time = (
                rows_by_number[tx.source_row_number]
                .cells.get(selected.happened_at or "", "")
                .strip()
            )
            parsed_time = None
            if selected.datetime_format:
                parsed_time = datetime.strptime(source_time, selected.datetime_format)
            else:
                try:
                    parsed_time = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
                except ValueError:
                    for fmt in _DATETIME_CANDIDATES:
                        try:
                            parsed_time = datetime.strptime(source_time, fmt)
                            break
                        except ValueError:
                            continue
            if parsed_time is not None and parsed_time.tzinfo is None:
                from .datetime_utils import parse_transaction_datetime

                happened = parse_transaction_datetime(parsed_time.isoformat(), time_zone=time_zone)
        cells = rows_by_number[tx.source_row_number].cells
        source_id = (
            cells.get(source_id_column, "").strip()
            if source_id_column
            else f"{metadata['sha256']}:{tx.source_row_number}"
        )
        if not source_id:
            out["errors"].append(dict(row_number=tx.source_row_number, code="EMPTY_SOURCE_ID"))
            continue
        transaction = BatchTransactionItem(
            amount=float(tx.amount),
            tx_type=tx.tx_type,
            happened_at=happened.isoformat(),
            note=tx.note,
            category=tx.category_name,
            account=tx.account_name,
            from_account=tx.from_account_name,
            to_account=tx.to_account_name,
            currency=tx.currency_code,
            tags=tx.tag_names,
        )
        candidates.append(
            ImportCandidate(
                transaction=transaction,
                sources=[
                    SourceInput(
                        source_system=source_system,
                        source_id=source_id,
                        file_sha256=metadata["sha256"],
                        row_number=tx.source_row_number,
                    )
                ],
            )
        )
    if out["errors"]:
        return {**out, "status": "mapping_or_data_errors", "plan": None}
    plan = await preview_import(
        user, ledger_id=metadata["ledger_id"], candidates=candidates, time_zone=time_zone
    )
    return {**out, "status": plan["status"], "plan": plan}


def export_transactions(
    user: User,
    ledger_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    tx_type: str | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    tag_id: str | None = None,
    time_zone: str | None = None,
) -> dict[str, Any]:
    """Create a complete CSV snapshot (up to 50000 rows) with IDs, original/settlement currencies and flags. Fetch the saved CSV using its authenticated MCP resource URI."""
    with rt.SessionLocal() as db:
        ledger = require_ledger(db, user, ledger_id)
        baseline = revision(db, [ledger.id], ledger.user_id)
        query = select(ReadTxProjection).where(ReadTxProjection.ledger_id == ledger.id)
        if date_from:
            query = query.where(
                ReadTxProjection.happened_at >= date_boundary(date_from, time_zone=time_zone)
            )
        if date_to:
            query = query.where(
                ReadTxProjection.happened_at < date_boundary(date_to, end=True, time_zone=time_zone)
            )
        if tx_type:
            if tx_type not in {"income", "expense", "transfer"}:
                raise ValueError("Invalid tx_type")
            query = query.where(ReadTxProjection.tx_type == tx_type)
        if category_id:
            query = query.where(ReadTxProjection.category_sync_id == category_id)
        if account_id:
            from sqlalchemy import or_

            query = query.where(
                or_(
                    ReadTxProjection.account_sync_id == account_id,
                    ReadTxProjection.from_account_sync_id == account_id,
                    ReadTxProjection.to_account_sync_id == account_id,
                )
            )
        if tag_id:
            # Exact JSON membership is checked without SQL wildcard interpolation.
            rows = [
                row
                for row in db.scalars(query).all()
                if tag_id in json.loads(row.tag_sync_ids_json or "[]")
            ]
        else:
            rows = db.scalars(
                query.order_by(ReadTxProjection.happened_at, ReadTxProjection.sync_id).limit(50001)
            ).all()
        if len(rows) > 50000:
            raise ValueError("Export exceeds 50000 rows; narrow the date range")
        output = io.StringIO(newline="")
        keys = [
            "sync_id",
            "tx_type",
            "amount",
            "happened_at",
            "note",
            "category_id",
            "category_name",
            "account_id",
            "account_name",
            "from_account_id",
            "from_account_name",
            "to_account_id",
            "to_account_name",
            "tags",
            "currency_code",
            "native_amount",
            "exclude_from_stats",
            "exclude_from_budget",
        ]
        writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            item = rt._serialize_tx(row, row.category_name)
            item["currency_code"] = row.currency_code or ledger.currency
            # Prevent spreadsheet formula injection in human-provided string fields.
            for key, value in item.items():
                if isinstance(value, str) and value.lstrip().startswith(
                    ("=", "+", "-", "@", "\t", "\r")
                ):
                    item[key] = "'" + value
            writer.writerow(item)
        if revision(db, [ledger.id], ledger.user_id) != baseline:
            raise ValueError("REVISION_CONFLICT: data changed during export; retry")
        db.expunge(ledger)
    csv_data = "\ufeff" + output.getvalue()
    # Export is a read capability: save the snapshot without requiring write scope/ledger role.
    from ..models import MCPStateRecord

    with rt.SessionLocal() as db:
        row = MCPStateRecord(
            user_id=user.id,
            ledger_id=ledger.id,
            kind="export",
            status="ready",
            data_json=dict(csv=csv_data, row_count=len(rows), dataset_revision=baseline),
        )
        db.add(row)
        db.commit()
        return dict(
            export_id=row.id,
            row_count=len(rows),
            revision=baseline,
            sha256=hashlib.sha256(csv_data.encode()).hexdigest(),
            resource_uri="beecount://exports/" + row.id,
        )


READ_FUNCTIONS = [read_attachment, list_attachments, export_transactions]
WRITE_FUNCTIONS = [parse_receipt, preview_text_transactions, preview_import_file]
