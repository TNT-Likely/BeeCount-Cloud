from __future__ import annotations

from typing import Any

FEE_CATEGORY_NAME = "金融服务费"
DISCOUNT_CATEGORY_NAME = "优惠减免"
FEE_CATEGORY_ICON = "account_balance"
DISCOUNT_CATEGORY_ICON = "local_offer"

def _norm_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None

def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f

def _positive_or_none(v: Any) -> float | None:
    f = _to_float(v)
    if f is None or f <= 0:
        return None
    return float(f)

def _find_by_sync_id(items: list[dict], sync_id: str | None) -> dict | None:
    if not sync_id:
        return None
    for item in items:
        if isinstance(item, dict) and str(item.get("syncId") or "") == sync_id:
            return item
    return None

def _ensure_category(snapshot: dict, name: str, kind: str, icon: str) -> dict:
    cats = snapshot.setdefault("categories", [])
    for cat in cats:
        if isinstance(cat, dict) and cat.get("name") == name and cat.get("kind") == kind:
            return cat
    from .snapshot_mutator import create_category
    _snap, _ = create_category(snapshot, {"name": name, "kind": kind, "icon": icon, "icon_type": "material", "level": 1})
    for cat in snapshot.get("categories", []):
        if isinstance(cat, dict) and cat.get("name") == name and cat.get("kind") == kind:
            return cat
    raise ValueError(f"failed to ensure category {name}")

def _find_account(snapshot: dict, account_id: str | None, account_name: str | None) -> dict | None:
    accounts = snapshot.get("accounts", []) or []
    if account_id:
        for a in accounts:
            if isinstance(a, dict) and str(a.get("syncId") or "") == account_id:
                return a
    if account_name:
        name = account_name.strip()
        for a in accounts:
            if isinstance(a, dict) and str(a.get("name") or "").strip() == name:
                return a
    return None

def _same_currency_account(acc: dict | None, ref_acc: dict | None) -> bool:
    if not acc or not ref_acc:
        return False
    c1 = (acc.get("currency") or "CNY").upper()
    c2 = (ref_acc.get("currency") or "CNY").upper()
    return c1 == c2

def _resolve_fee_account(snapshot: dict, main: dict, payload: dict) -> dict | None:
    fee_id = _norm_str(payload.get("transfer_fee_account_id")) if "transfer_fee_account_id" in payload else _norm_str(main.get("transferFeeAccountId"))
    fee_name = _norm_str(payload.get("transfer_fee_account_name")) if "transfer_fee_account_name" in payload else _norm_str(main.get("transferFeeAccountName"))
    if fee_id or fee_name:
        return _find_account(snapshot, fee_id, fee_name)
    from_acc = _find_account(snapshot, _norm_str(main.get("fromAccountId")), _norm_str(main.get("fromAccountName")))
    return from_acc

def _compute_native_for_fee(amount: float, main: dict, fee_payload: dict) -> tuple[str | None, float | None]:
    # Prefer explicit values for non-transfer-like adjustments not used; for now follow main transfer currency context.
    # If main has nativeAmount and amount > 0, approximate rate by main.native/amount ratio.
    if fee_payload.get("currency_code") is not None:
        cc = _norm_str(fee_payload.get("currency_code"))
    else:
        cc = _norm_str(main.get("currencyCode"))
    if fee_payload.get("native_amount") is not None:
        return (cc.upper() if cc else None), _to_float(fee_payload.get("native_amount"))
    main_amt = _to_float(main.get("amount")) or 0.0
    main_native = _to_float(main.get("nativeAmount"))
    if main_native is not None and main_amt > 0:
        rate = main_native / main_amt
        return (cc.upper() if cc else None), float(amount * rate)
    if cc:
        # No reliable rate; let caller omit native to avoid lying for foreign currency
        return cc.upper(), None
    return None, None

def _upsert_linked_tx(snapshot: dict, *, tx_id: str | None, payload: dict, actor_payload: dict) -> str:
    from .snapshot_mutator import create_transaction, update_transaction
    if tx_id and _find_by_sync_id(snapshot.get("items", []), tx_id):
        update_transaction(snapshot, tx_id, {**payload, "__actor_user_id": actor_payload.get("__actor_user_id"), "__actor_in_shared_ledger": actor_payload.get("__actor_in_shared_ledger")})
        return tx_id
    # create a fresh tx
    _snap, new_id = create_transaction(snapshot, {**payload, "__actor_user_id": actor_payload.get("__actor_user_id"), "__actor_in_shared_ledger": actor_payload.get("__actor_in_shared_ledger")})
    return new_id

def _delete_linked_tx(snapshot: dict, tx_id: str | None, actor_payload: dict) -> None:
    if not tx_id:
        return
    from .snapshot_mutator import delete_transaction
    try:
        delete_transaction(snapshot, tx_id, {"__actor_user_id": actor_payload.get("__actor_user_id"), "__actor_in_shared_ledger": actor_payload.get("__actor_in_shared_ledger")})
    except Exception:
        # ignore missing/already deleted for robustness
        return

def apply_transfer_adjustments(snapshot: dict, main_tx_id: str, request_payload: dict, actor_payload: dict) -> dict:
    """Mutate snapshot so a transfer tx has linked fee/discount txs consistent with request.
    request_payload is the raw write payload (snake_case keys).
    """
    items = snapshot.get("items", []) or []
    main = _find_by_sync_id(items, main_tx_id)
    if main is None:
        return snapshot
    if str(main.get("type") or "") != "transfer":
        # ensure no dangling adjustments
        _delete_linked_tx(snapshot, _norm_str(main.get("transferFeeTxId")), actor_payload)
        _delete_linked_tx(snapshot, _norm_str(main.get("transferDiscountTxId")), actor_payload)
        for k in ("transferFeeAmount","transferDiscountAmount","transferFeeAccountId","transferFeeAccountName","transferFeeTxId","transferDiscountTxId"):
            main.pop(k, None)
        return snapshot

    # Determine desired values (on update, missing means keep existing)
    def desired(key_req: str, key_main: str):
        if key_req in request_payload:
            return request_payload.get(key_req)
        return main.get(key_main)
    fee_amount = _positive_or_none(desired("transfer_fee_amount", "transferFeeAmount"))
    discount_amount = _positive_or_none(desired("transfer_discount_amount", "transferDiscountAmount"))
    fee_account = _resolve_fee_account(snapshot, main, request_payload)
    happened_at = request_payload.get("happened_at") or main.get("happenedAt")
    note_base = _norm_str(request_payload.get("note")) if "note" in request_payload else _norm_str(main.get("note"))

    # Fee
    if fee_amount and fee_account:
        fee_cat = _ensure_category(snapshot, FEE_CATEGORY_NAME, "expense", FEE_CATEGORY_ICON)
        fee_note = f"转账手续费{('：' + note_base) if note_base else ''}"
        fee_currency, fee_native = _compute_native_for_fee(fee_amount, main, request_payload)
        fee_payload = {
            "tx_type": "expense",
            "amount": fee_amount,
            "happened_at": happened_at,
            "note": fee_note,
            "category_name": fee_cat.get("name"),
            "category_kind": "expense",
            "category_id": fee_cat.get("syncId"),
            "account_name": fee_account.get("name"),
            "account_id": fee_account.get("syncId"),
            "exclude_from_stats": False,
            "exclude_from_budget": False,
        }
        if fee_currency:
            fee_payload["currency_code"] = fee_currency
        if fee_native is not None:
            fee_payload["native_amount"] = fee_native
        fee_tx_id = _norm_str(main.get("transferFeeTxId"))
        new_fee_id = _upsert_linked_tx(snapshot, tx_id=fee_tx_id, payload=fee_payload, actor_payload=actor_payload)
        main["transferFeeAmount"] = fee_amount
        main["transferFeeAccountId"] = fee_account.get("syncId")
        main["transferFeeAccountName"] = fee_account.get("name")
        main["transferFeeTxId"] = new_fee_id
    else:
        _delete_linked_tx(snapshot, _norm_str(main.get("transferFeeTxId")), actor_payload)
        main.pop("transferFeeAmount", None)
        main.pop("transferFeeTxId", None)
        # keep/clear account override depending on request intent
        if "transfer_fee_account_id" in request_payload or "transfer_fee_account_name" in request_payload or fee_amount is None:
            if _norm_str(request_payload.get("transfer_fee_account_id")) or _norm_str(request_payload.get("transfer_fee_account_name")):
                if fee_account:
                    main["transferFeeAccountId"] = fee_account.get("syncId")
                    main["transferFeeAccountName"] = fee_account.get("name")
                else:
                    main.pop("transferFeeAccountId", None)
                    main.pop("transferFeeAccountName", None)
            else:
                main.pop("transferFeeAccountId", None)
                main.pop("transferFeeAccountName", None)

    # Discount - record to the transfer destination account as income "优惠减免"
    if discount_amount:
        to_acc = _find_account(snapshot, _norm_str(main.get("toAccountId")), _norm_str(main.get("toAccountName")))
        if to_acc:
            disc_cat = _ensure_category(snapshot, DISCOUNT_CATEGORY_NAME, "income", DISCOUNT_CATEGORY_ICON)
            disc_note = f"转账优惠{('：' + note_base) if note_base else ''}"
            disc_currency, disc_native = _compute_native_for_fee(discount_amount, main, request_payload)
            disc_payload = {
                "tx_type": "income",
                "amount": discount_amount,
                "happened_at": happened_at,
                "note": disc_note,
                "category_name": disc_cat.get("name"),
                "category_kind": "income",
                "category_id": disc_cat.get("syncId"),
                "account_name": to_acc.get("name"),
                "account_id": to_acc.get("syncId"),
                "exclude_from_stats": False,
                "exclude_from_budget": False,
            }
            if disc_currency:
                disc_payload["currency_code"] = disc_currency
            if disc_native is not None:
                disc_payload["native_amount"] = disc_native
            disc_tx_id = _norm_str(main.get("transferDiscountTxId"))
            new_disc_id = _upsert_linked_tx(snapshot, tx_id=disc_tx_id, payload=disc_payload, actor_payload=actor_payload)
            main["transferDiscountAmount"] = discount_amount
            main["transferDiscountTxId"] = new_disc_id
        else:
            _delete_linked_tx(snapshot, _norm_str(main.get("transferDiscountTxId")), actor_payload)
            main.pop("transferDiscountAmount", None)
            main.pop("transferDiscountTxId", None)
    else:
        _delete_linked_tx(snapshot, _norm_str(main.get("transferDiscountTxId")), actor_payload)
        main.pop("transferDiscountAmount", None)
        main.pop("transferDiscountTxId", None)

    return snapshot

def delete_transfer_adjustments(snapshot: dict, main_item: dict | None, actor_payload: dict) -> None:
    if not main_item:
        return
    _delete_linked_tx(snapshot, _norm_str(main_item.get("transferFeeTxId")), actor_payload)
    _delete_linked_tx(snapshot, _norm_str(main_item.get("transferDiscountTxId")), actor_payload)
