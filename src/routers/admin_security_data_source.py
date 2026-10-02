"""股票資料來源設定後台(Phase 3,docs/STOCK_HOLDINGS_SD.md §12)。

掛在 `/api/v1/admin/security-data-source`,同 `admin_app_version.py`:疊
`require_admin_user` + `require_scopes(SCOPE_OPS_WRITE)`。API key 加密存放,
GET 只回「是否已設定」。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user, require_scopes
from ..models import AuditLog, SecurityDataSourceConfig, User
from ..security import SCOPE_OPS_WRITE
from ..services import secret_crypto
from ..services.securities import data_source
from ..services.securities.providers import twelvedata
from ..services.securities.providers.base import new_client

router = APIRouter()

_SCOPE_DEP = require_scopes(SCOPE_OPS_WRITE)


class SecurityDataSourceOut(BaseModel):
    provider: str
    providers: list[str]
    api_key_set: bool
    last_test_at: datetime | None = None
    last_test_error: str | None = None


class SecurityDataSourceUpdateRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=32)
    # 只有帶非空字串才覆蓋;留空/不帶 = 不變更。要清掉 key 用 clear_api_key。
    api_key: str | None = Field(default=None, max_length=500)
    clear_api_key: bool = False


class SecurityDataSourceTestOut(BaseModel):
    ok: bool
    message: str
    price: float | None = None


def _out(config: SecurityDataSourceConfig) -> SecurityDataSourceOut:
    return SecurityDataSourceOut(
        provider=config.provider,
        providers=list(data_source.PROVIDERS),
        api_key_set=bool(config.api_key_encrypted),
        last_test_at=config.last_test_at,
        last_test_error=config.last_test_error,
    )


@router.get("", response_model=SecurityDataSourceOut)
def get_security_data_source(
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> SecurityDataSourceOut:
    config = data_source.get_or_create_config(db)
    db.commit()
    return _out(config)


@router.put("", response_model=SecurityDataSourceOut)
def update_security_data_source(
    req: SecurityDataSourceUpdateRequest,
    admin_user: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> SecurityDataSourceOut:
    config = data_source.get_or_create_config(db)
    if req.provider is not None:
        if req.provider not in data_source.PROVIDERS:
            raise HTTPException(status_code=400, detail="unknown provider")
        config.provider = req.provider
    if req.clear_api_key:
        config.api_key_encrypted = None
    elif req.api_key is not None and req.api_key.strip():
        config.api_key_encrypted = secret_crypto.encrypt(req.api_key.strip())
    if config.provider == data_source.PROVIDER_TWELVEDATA and not config.api_key_encrypted:
        raise HTTPException(status_code=400, detail="api_key is required for twelvedata")
    config.last_test_at = None
    config.last_test_error = None
    db.add(
        AuditLog(
            user_id=admin_user.id,
            ledger_id=None,
            action="admin_security_data_source_update",
            metadata_json={"provider": config.provider, "api_key_set": bool(config.api_key_encrypted)},
        )
    )
    db.commit()
    return _out(config)


@router.post("/test", response_model=SecurityDataSourceTestOut)
async def test_security_data_source(
    _admin: User = Depends(require_admin_user),
    _scopes: set[str] = Depends(_SCOPE_DEP),
    db: Session = Depends(get_db),
) -> SecurityDataSourceTestOut:
    """用已儲存的 key 向付費來源抓一檔美股(AAPL)報價,驗證 key 可用。"""
    config = data_source.get_or_create_config(db)
    source = data_source.load_source(db)
    if not source.paid:
        return SecurityDataSourceTestOut(ok=False, message="目前使用免費來源,或尚未設定 API key")
    db.commit()
    error: str | None = None
    price: float | None = None
    try:
        async with new_client() as client:
            quote = await twelvedata.fetch_quote("US", "AAPL", source.api_key or "", client)
        price = quote.price
    except Exception as exc:  # noqa: BLE001
        error = twelvedata.redact(str(exc))[:900]
    config = data_source.get_or_create_config(db)
    config.last_test_at = datetime.now(timezone.utc)
    config.last_test_error = error
    db.commit()
    if error:
        return SecurityDataSourceTestOut(ok=False, message=error)
    return SecurityDataSourceTestOut(ok=True, message="連線成功", price=price)
