"""账本导入 endpoints —— upload / preview / execute(SSE)/ cancel。

设计:.docs/web-ledger-import.md §3.1

整体回滚契约:execute 的所有 mutate 在一个 DB transaction 里,任一行失败 →
db.rollback();WS broadcast 仅在 commit 后发,mobile 永远看不到脏数据。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ... import snapshot_builder
from ...concurrency import lock_ledger_for_materialize
from ...database import get_db
from ...deps import get_current_user
from ...models import AuditLog, Ledger, User
from ...routers.write._shared import (
    _TRANSACTION_WRITE_ROLES,
    _WRITE_SCOPE_DEP,
    _emit_entity_diffs,
    _payload_with_actor,
    get_accessible_ledger_by_external_id,
)
from ...services.import_data import (
    ImportError as ImpError,
    ImportFieldMapping,
    apply_mapping,
    cancel_token,
    consume_token,
    get_token_data,
    parse_csv_text,
    parse_excel_bytes,
)
from ...services.import_data.cache import save_token_data, update_token
from ...services.import_data.stats import build_existing_sets, compute_stats
from ...snapshot_mutator import (
    create_account,
    create_category,
    create_tag,
    create_transaction,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ──────────── 限额 ────────────

_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
_MAX_ROW_COUNT = 50_000


# ──────────── Pydantic schemas ────────────


class FieldMappingPayload(BaseModel):
    tx_type: str | None = None
    amount: str | None = None
    happened_at: str | None = None
    category_name: str | None = None
    subcategory_name: str | None = None
    account_name: str | None = None
    from_account_name: str | None = None
    to_account_name: str | None = None
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    datetime_format: str | None = None
    strip_currency_symbols: bool = True
    expense_is_negative: bool = False

    def to_internal(self) -> ImportFieldMapping:
        return ImportFieldMapping(
            tx_type=self.tx_type,
            amount=self.amount,
            happened_at=self.happened_at,
            category_name=self.category_name,
            subcategory_name=self.subcategory_name,
            account_name=self.account_name,
            from_account_name=self.from_account_name,
            to_account_name=self.to_account_name,
            note=self.note,
            tags=list(self.tags),
            datetime_format=self.datetime_format,
            strip_currency_symbols=self.strip_currency_symbols,
            expense_is_negative=self.expense_is_negative,
        )


def _mapping_to_payload(m: ImportFieldMapping) -> dict:
    return {
        "tx_type": m.tx_type,
        "amount": m.amount,
        "happened_at": m.happened_at,
        "category_name": m.category_name,
        "subcategory_name": m.subcategory_name,
        "account_name": m.account_name,
        "from_account_name": m.from_account_name,
        "to_account_name": m.to_account_name,
        "note": m.note,
        "tags": list(m.tags),
        "datetime_format": m.datetime_format,
        "strip_currency_symbols": m.strip_currency_symbols,
        "expense_is_negative": m.expense_is_negative,
    }


class PreviewRequest(BaseModel):
    mapping: FieldMappingPayload | None = None
    target_ledger_id: str | None = None
    dedup_strategy: Literal["skip_duplicates", "insert_all"] | None = None
    auto_tag_names: list[str] | None = None


class ImportSummary(BaseModel):
    """upload / preview 共享的响应主体。"""

    import_token: str
    expires_at: datetime
    source_format: str
    headers: list[str]
    suggested_mapping: dict
    current_mapping: dict
    target_ledger_id: str | None
    dedup_strategy: str
    auto_tag_names: list[str]
    stats: dict
    sample_rows: list[dict]  # 前 5 行原始 ParsedRow.cells(给映射 dialog 显示样本)
    # 解析后的前 10 笔(应用 mapping + transformer 后)— 给用户"长这样"参考,
    # 不满意 → 编辑映射 → 重 preview 这里也跟着变
    sample_transactions: list[dict] = Field(default_factory=list)


# ──────────── upload ────────────


@router.post(
    "/upload",
    response_model=ImportSummary,
)
async def upload_import(
    request: Request,
    file: UploadFile = File(...),
    target_ledger_id: str | None = Form(default=None),
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportSummary:
    payload = await file.read()
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "IMPORT_EMPTY_FILE"},
        )
    if len(payload) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": "IMPORT_FILE_TOO_LARGE",
                "limit_bytes": _MAX_FILE_BYTES,
            },
        )

    # 按文件类型分发:.xlsx → openpyxl;.csv/.tsv/.txt → 文本。
    # xlsx magic bytes = `PK\x03\x04`(zip),也按 filename 后缀容错。
    filename_lower = (file.filename or "").lower()
    is_xlsx = filename_lower.endswith(".xlsx") or payload[:4] == b"PK\x03\x04"

    if is_xlsx:
        try:
            data = parse_excel_bytes(payload=payload)
        except RuntimeError as exc:
            logger.warning("import.xlsx parse failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error_code": "IMPORT_XLSX_PARSE_FAILED", "message": str(exc)},
            )
    else:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = payload.decode("gbk")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"error_code": "IMPORT_DECODE_FAILED"},
                )
        data = parse_csv_text(raw_text=text)

    if not data.rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "IMPORT_NO_ROWS"},
        )
    if len(data.rows) > _MAX_ROW_COUNT:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error_code": "IMPORT_TOO_MANY_ROWS",
                "limit_rows": _MAX_ROW_COUNT,
            },
        )

    # 校验 target_ledger_id(可空 — 用户可在 preview 阶段再选)
    target_ext_id = (target_ledger_id or "").strip() or None
    if target_ext_id:
        _resolve_target_ledger(db, current_user, target_ext_id)

    # 默认 dedup;auto-tag 默认空(用户反馈:不需要自动加"导入-xxx"标签)
    dedup_strategy = "skip_duplicates"
    auto_tag_names: list[str] = []

    token, expires_at = save_token_data(
        user_id=current_user.id,
        data=data,
        mapping=data.suggested_mapping,
        target_ledger_id=target_ext_id,
        dedup_strategy=dedup_strategy,
        auto_tag_names=auto_tag_names,
    )

    summary = _build_summary(
        token=token,
        expires_at=expires_at,
        data=data,
        mapping=data.suggested_mapping,
        target_ledger_ext_id=target_ext_id,
        dedup_strategy=dedup_strategy,
        auto_tag_names=auto_tag_names,
        db=db,
        current_user=current_user,
    )
    logger.info(
        "import.upload user=%s token=%s source=%s rows=%d ledger=%s",
        current_user.id, token, data.source_format, len(data.rows), target_ext_id,
    )
    return summary


# ──────────── preview ────────────


@router.post(
    "/{token}/preview",
    response_model=ImportSummary,
)
async def preview_import(
    token: str,
    req: PreviewRequest,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportSummary:
    entry = get_token_data(token=token, user_id=current_user.id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error_code": "IMPORT_TOKEN_EXPIRED"},
        )

    new_mapping = req.mapping.to_internal() if req.mapping else entry.mapping
    new_target = req.target_ledger_id if req.target_ledger_id is not None else entry.target_ledger_id
    new_dedup = req.dedup_strategy or entry.dedup_strategy
    new_auto_tags = req.auto_tag_names if req.auto_tag_names is not None else entry.auto_tag_names

    if new_target:
        _resolve_target_ledger(db, current_user, new_target)

    update_token(
        token=token,
        user_id=current_user.id,
        mapping=new_mapping,
        target_ledger_id=new_target,
        dedup_strategy=new_dedup,
        auto_tag_names=new_auto_tags,
    )

    return _build_summary(
        token=token,
        expires_at=_expires_at_for(entry),
        data=entry.data,
        mapping=new_mapping,
        target_ledger_ext_id=new_target,
        dedup_strategy=new_dedup,
        auto_tag_names=new_auto_tags,
        db=db,
        current_user=current_user,
    )


# ──────────── execute(SSE) ────────────


@router.post("/{token}/execute")
async def execute_import(
    token: str,
    request: Request,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = get_token_data(token=token, user_id=current_user.id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"error_code": "IMPORT_TOKEN_EXPIRED"},
        )
    if not entry.target_ledger_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "IMPORT_NO_TARGET_LEDGER"},
        )
    ledger = _resolve_target_ledger(db, current_user, entry.target_ledger_id)

    # transform 一次,确认无 row error 再开 transaction(避免无意义锁占用)
    txs, errors, _ = apply_mapping(rows=entry.data.rows, mapping=entry.mapping)
    if errors:
        async def err_stream():
            err = errors[0]
            yield _sse_event(
                "error",
                {
                    "code": err.code,
                    "row_number": err.row_number,
                    "field_name": err.field_name,
                    "message": err.message,
                    "raw_line": err.raw_line[:300],
                    "total_errors": len(errors),
                },
            )
        return StreamingResponse(err_stream(), media_type="text/event-stream")

    user_id = current_user.id
    ledger_id_internal = ledger.id
    ledger_external_id = ledger.external_id
    ledger_user_id = ledger.user_id
    auto_tags = list(entry.auto_tag_names)
    dedup_strategy = entry.dedup_strategy

    async def stream():
        try:
            async for evt in _do_execute(
                request=request,
                db=db,
                user_id=user_id,
                ledger=ledger,
                txs=txs,
                auto_tags=auto_tags,
                dedup_strategy=dedup_strategy,
            ):
                yield evt
            # 成功后 consume token
            consume_token(token=token, user_id=user_id)
            # broadcast(commit 之后)
            try:
                await request.app.state.ws_manager.broadcast_to_user(
                    ledger_user_id,
                    {
                        "type": "sync_change",
                        "ledgerId": ledger_external_id,
                        "serverTimestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("import.broadcast failed: %s", exc)
        except _ImportFailed as exc:
            yield _sse_event(
                "error",
                {
                    "code": exc.code,
                    "row_number": exc.row_number,
                    "field_name": exc.field_name,
                    "message": exc.message,
                    "raw_line": exc.raw_line[:300],
                },
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _do_execute(
    *,
    request: Request,
    db: Session,
    user_id: str,
    ledger: Ledger,
    txs: list,
    auto_tags: list[str],
    dedup_strategy: str,
):
    """单事务 + SSE 进度。任一失败 → db.rollback() + raise _ImportFailed。"""
    lock_ledger_for_materialize(db, ledger.id)
    snapshot = snapshot_builder.build(db, ledger)
    prev = _deep_copy_snapshot(snapshot)

    existing_account_names, existing_category_keys, existing_tag_names, existing_dedup = build_existing_sets(snapshot)

    actor_payload_base = _payload_with_actor({}, _user_stub(user_id))

    try:
        # 1. accounts
        account_diff_names = _collect_new_accounts(txs, existing_account_names)
        for i, name in enumerate(account_diff_names, 1):
            try:
                snapshot, _ = create_account(
                    snapshot,
                    {**actor_payload_base, "name": name, "currency": ledger.currency},
                )
            except (KeyError, ValueError, PermissionError) as exc:
                raise _ImportFailed(
                    code="WRITE_ACCOUNT_FAILED",
                    row_number=0,
                    field_name="account_name",
                    message=f"create account {name!r} failed: {exc}",
                    raw_line="",
                )
            yield _sse_event(
                "stage",
                {"stage": "accounts", "done": i, "total": len(account_diff_names)},
            )

        # 2. categories
        category_diff = _collect_new_categories(txs, existing_category_keys)
        for i, (name, kind, parent) in enumerate(category_diff, 1):
            try:
                snapshot, _ = create_category(
                    snapshot,
                    {
                        **actor_payload_base,
                        "name": name,
                        "kind": kind,
                        "level": 2 if parent else 1,
                        "parent_name": parent,
                    },
                )
            except (KeyError, ValueError, PermissionError) as exc:
                raise _ImportFailed(
                    code="WRITE_CATEGORY_FAILED",
                    row_number=0,
                    field_name="category_name",
                    message=f"create category {name!r}/{kind!r} failed: {exc}",
                    raw_line="",
                )
            yield _sse_event(
                "stage",
                {"stage": "categories", "done": i, "total": len(category_diff)},
            )

        # 3. tags(包括 auto_tags)
        all_tag_names = _collect_new_tags(txs, auto_tags, existing_tag_names)
        for i, name in enumerate(all_tag_names, 1):
            try:
                snapshot, _ = create_tag(
                    snapshot, {**actor_payload_base, "name": name}
                )
            except (KeyError, ValueError, PermissionError) as exc:
                raise _ImportFailed(
                    code="WRITE_TAG_FAILED",
                    row_number=0,
                    field_name="tag",
                    message=f"create tag {name!r} failed: {exc}",
                    raw_line="",
                )
            yield _sse_event(
                "stage",
                {"stage": "tags", "done": i, "total": len(all_tag_names)},
            )

        # 4. transactions
        skipped = 0
        total = len(txs)
        last_progress_emit = 0
        # 计算 dedup keys(包括即将插入的,防止文件内自身有重复)
        seen_keys = set(existing_dedup)
        for idx, tx in enumerate(txs, 1):
            dedup_key = (tx.tx_type, f"{tx.amount}", tx.happened_at.isoformat())
            if dedup_strategy == "skip_duplicates" and dedup_key in seen_keys:
                skipped += 1
            else:
                seen_keys.add(dedup_key)
                tx_payload = _build_tx_payload(tx, auto_tags, actor_payload_base)
                try:
                    snapshot, _ = create_transaction(snapshot, tx_payload)
                except (KeyError, ValueError, PermissionError) as exc:
                    raise _ImportFailed(
                        code="WRITE_TX_FAILED",
                        row_number=tx.source_row_number,
                        field_name=None,
                        message=f"create transaction failed: {exc}",
                        raw_line=tx.source_raw_line,
                    )
            # 每 100 条 yield 一次进度,避免 SSE 太密
            if idx - last_progress_emit >= 100 or idx == total:
                last_progress_emit = idx
                yield _sse_event(
                    "stage",
                    {
                        "stage": "transactions",
                        "done": idx,
                        "total": total,
                        "skipped": skipped,
                    },
                )
                # 让 event loop 喘口气,前端能看到进度
                await asyncio.sleep(0)

        # 5. emit entity diffs(prev → final snapshot 一次性)
        now = datetime.now(timezone.utc)
        emitted = _emit_entity_diffs(
            db,
            ledger=ledger,
            current_user=_user_stub(user_id),
            device_id="web-import",
            prev=prev,
            next_snapshot=snapshot,
            now=now,
        )
        new_change_id = max(emitted) if emitted else snapshot_builder.latest_change_id(db, ledger.id)

        db.add(
            AuditLog(
                user_id=user_id,
                ledger_id=ledger.id,
                action="web_import_csv",
                metadata_json={
                    "ledgerId": ledger.external_id,
                    "newChangeId": new_change_id,
                    "createdTxCount": total - skipped,
                    "skippedCount": skipped,
                    "newAccounts": account_diff_names,
                    "newCategoriesCount": len(category_diff),
                    "newTagsCount": len(all_tag_names),
                    "dedupStrategy": dedup_strategy,
                    "autoTagNames": auto_tags,
                },
            )
        )

        db.commit()

        yield _sse_event(
            "complete",
            {
                "created_tx_count": total - skipped,
                "skipped_count": skipped,
                "new_change_id": new_change_id,
            },
        )

        logger.info(
            "import.execute.complete user=%s ledger=%s created=%d skipped=%d change_id=%d",
            user_id, ledger.external_id, total - skipped, skipped, new_change_id,
        )

    except _ImportFailed:
        db.rollback()
        logger.warning("import.execute.failed user=%s ledger=%s", user_id, ledger.external_id)
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("import.execute.unknown_error user=%s ledger=%s", user_id, ledger.external_id)
        raise _ImportFailed(
            code="WRITE_UNKNOWN",
            row_number=0,
            field_name=None,
            message=str(exc),
            raw_line="",
        )


# ──────────── cancel ────────────


@router.delete("/{token}")
async def cancel_import(
    token: str,
    _scopes: set[str] = Depends(_WRITE_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
):
    ok = cancel_token(token=token, user_id=current_user.id)
    return {"cancelled": ok}


# ──────────── helpers ────────────


class _ImportFailed(Exception):
    def __init__(
        self,
        *,
        code: str,
        row_number: int,
        field_name: str | None,
        message: str,
        raw_line: str,
    ):
        super().__init__(message)
        self.code = code
        self.row_number = row_number
        self.field_name = field_name
        self.message = message
        self.raw_line = raw_line


def _sse_event(event: str, data: dict[str, Any]) -> bytes:
    """SSE wire format。`bytes` 让 StreamingResponse 直传不再 encode。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _deep_copy_snapshot(snapshot: dict) -> dict:
    """浅拷贝 + 列表深拷贝(只对 5 个 list 字段够用)。"""
    out = {**snapshot}
    for key in ("items", "accounts", "categories", "tags", "budgets"):
        arr = snapshot.get(key)
        if isinstance(arr, list):
            out[key] = [dict(e) if isinstance(e, dict) else e for e in arr]
    return out


def _user_stub(user_id: str):
    """传给 _emit_entity_diffs / _payload_with_actor 用的 minimal user 对象。
    实际只读取 .id 属性。"""

    class _Stub:
        def __init__(self, uid: str) -> None:
            self.id = uid
            self.email = ""
            self.is_admin = False

    return _Stub(user_id)


def _resolve_target_ledger(db: Session, user: User, ledger_external_id: str) -> Ledger:
    # CSV 导入会写 tx,Owner 或 Editor 都可以。Viewer / outsider → 404。
    row = get_accessible_ledger_by_external_id(
        db,
        user_id=user.id,
        ledger_external_id=ledger_external_id,
        roles={"owner", "editor"},
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "IMPORT_LEDGER_NOT_FOUND"},
        )
    # row 是 (Ledger, role_str) tuple
    ledger, _role = row
    return ledger


def _expires_at_for(entry) -> datetime:
    # cache 内部用 monotonic 算 TTL,这里给前端展示用 wall-clock + 30min
    from datetime import timedelta as _td
    return datetime.now(timezone.utc) + _td(seconds=30 * 60)


def _build_summary(
    *,
    token: str,
    expires_at: datetime,
    data,
    mapping: ImportFieldMapping,
    target_ledger_ext_id: str | None,
    dedup_strategy: str,
    auto_tag_names: list[str],
    db: Session,
    current_user: User,
) -> ImportSummary:
    """跑一次 transform + stats 计算并打包。"""
    txs, errors, warnings = apply_mapping(rows=data.rows, mapping=mapping)

    if target_ledger_ext_id:
        try:
            ledger = _resolve_target_ledger(db, current_user, target_ledger_ext_id)
            snapshot = snapshot_builder.build(db, ledger)
            existing_acc, existing_cat, existing_tag, existing_dedup = build_existing_sets(snapshot)
        except HTTPException:
            existing_acc = set()
            existing_cat = set()
            existing_tag = set()
            existing_dedup = set()
    else:
        existing_acc = set()
        existing_cat = set()
        existing_tag = set()
        existing_dedup = set()

    stats = compute_stats(
        txs=txs,
        parse_errors=errors,
        parse_warnings=list(data.parse_warnings) + list(warnings),
        existing_account_names=existing_acc,
        existing_category_names=existing_cat,
        existing_tag_names=existing_tag,
        existing_dedup_keys=existing_dedup,
        extra_tag_names=auto_tag_names,
    )

    sample = [r.cells for r in data.rows[:5]]
    sample_txs = [_tx_to_payload(tx) for tx in txs[:10]]

    return ImportSummary(
        import_token=token,
        expires_at=expires_at,
        source_format=data.source_format,
        headers=list(data.headers),
        suggested_mapping=_mapping_to_payload(data.suggested_mapping),
        current_mapping=_mapping_to_payload(mapping),
        target_ledger_id=target_ledger_ext_id,
        dedup_strategy=dedup_strategy,
        auto_tag_names=list(auto_tag_names),
        stats=stats.to_payload(),
        sample_rows=sample,
        sample_transactions=sample_txs,
    )


def _tx_to_payload(tx) -> dict:
    """ImportTransaction → 前端展示用的 dict。Decimal → str。

    `category_name` = leaf(可能是二级 / 也可能是一级 if no sub),
    `parent_category_name` = level-1(仅当有二级时填)。语义跟 mobile tx
    模型一致。
    """
    return {
        "tx_type": tx.tx_type,
        "amount": str(tx.amount),
        "happened_at": tx.happened_at.isoformat(),
        "note": tx.note,
        "category_name": tx.category_name,
        "parent_category_name": tx.parent_category_name,
        "account_name": tx.account_name,
        "from_account_name": tx.from_account_name,
        "to_account_name": tx.to_account_name,
        "tag_names": list(tx.tag_names),
        "source_row_number": tx.source_row_number,
    }


def _collect_new_accounts(txs, existing: set[str]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for tx in txs:
        for n in (tx.account_name, tx.from_account_name, tx.to_account_name):
            if not n:
                continue
            if n in existing or n in seen_set:
                continue
            seen.append(n)
            seen_set.add(n)
    return seen


def _collect_new_categories(txs, existing: set[tuple[str, str]]) -> list[tuple[str, str, str | None]]:
    """返回 [(name, kind, parent_name), ...] —— parent 必须先创建,所以排序时
    parent 在前。"""
    parents: list[tuple[str, str, None]] = []
    children: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for tx in txs:
        if tx.tx_type == "transfer":
            continue
        kind = tx.tx_type
        # parent
        if tx.parent_category_name:
            key = (tx.parent_category_name, kind)
            if key not in existing and key not in seen:
                parents.append((tx.parent_category_name, kind, None))
                seen.add(key)
        # child / leaf
        if tx.category_name:
            key = (tx.category_name, kind)
            if key not in existing and key not in seen:
                if tx.parent_category_name:
                    children.append((tx.category_name, kind, tx.parent_category_name))
                else:
                    parents.append((tx.category_name, kind, None))
                seen.add(key)
    return parents + children


def _collect_new_tags(txs, auto_tags: list[str], existing: set[str]) -> list[str]:
    seen: list[str] = []
    seen_set: set[str] = set()
    for source_list in (auto_tags, *(tx.tag_names for tx in txs)):
        for n in source_list:
            if not n or n in existing or n in seen_set:
                continue
            seen.append(n)
            seen_set.add(n)
    return seen


def _build_tx_payload(tx, auto_tags: list[str], actor_base: dict) -> dict:
    user_tags = list(tx.tag_names)
    merged = user_tags + [t for t in auto_tags if t and t not in user_tags]
    payload = {
        **actor_base,
        "tx_type": tx.tx_type,
        "amount": float(tx.amount),
        "happened_at": tx.happened_at.isoformat(),
        "note": tx.note,
        "category_name": tx.category_name,
        "category_kind": tx.tx_type if tx.tx_type != "transfer" else None,
        "account_name": tx.account_name,
        "from_account_name": tx.from_account_name,
        "to_account_name": tx.to_account_name,
    }
    if merged:
        payload["tags"] = merged
    return payload
