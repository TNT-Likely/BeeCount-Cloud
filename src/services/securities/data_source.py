"""報價/除息資料來源切換(Phase 3,docs/STOCK_HOLDINGS_SD.md §12)。

預設 `free`(Yahoo + 證交所/櫃買,行為跟 Phase 1/2 完全一樣)。管理者在後台選
`twelvedata` 並填 API key 後,逐檔報價與除息先走付費來源,失敗(額度用完、
代號不支援、網路錯誤)一律退回 Yahoo,不讓使用者畫面空掉。API key 用
`secret_crypto` 加密存 DB,後台 API 只回「是否已設定」。

呼叫方在打上游前(還拿得到 DB session 時)呼叫 `load_source(db)` 取得
`DataSource`(純資料,不綁 session),再把它帶進 async fetch。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ...models import SecurityDataSourceConfig
from .. import secret_crypto
from .providers import twelvedata, yahoo
from .providers.base import DividendData, QuoteData

logger = logging.getLogger(__name__)

PROVIDER_FREE = "free"
PROVIDER_TWELVEDATA = "twelvedata"
PROVIDERS = (PROVIDER_FREE, PROVIDER_TWELVEDATA)


@dataclass(frozen=True)
class DataSource:
    provider: str = PROVIDER_FREE
    api_key: str | None = None

    @property
    def paid(self) -> bool:
        return self.provider == PROVIDER_TWELVEDATA and bool(self.api_key)


FREE = DataSource()


def get_or_create_config(db: Session) -> SecurityDataSourceConfig:
    config = db.get(SecurityDataSourceConfig, 1)
    if config is None:
        config = SecurityDataSourceConfig(id=1, provider=PROVIDER_FREE, updated_at=datetime.now(timezone.utc))
        db.add(config)
        db.flush()
    return config


def load_source(db: Session) -> DataSource:
    """讀目前設定;任何錯誤(解密失敗、表不存在)都退回免費來源。"""
    try:
        config = db.get(SecurityDataSourceConfig, 1)
        if config is None or config.provider != PROVIDER_TWELVEDATA or not config.api_key_encrypted:
            return FREE
        return DataSource(PROVIDER_TWELVEDATA, secret_crypto.decrypt(config.api_key_encrypted))
    except Exception as exc:  # noqa: BLE001
        logger.warning("securities: load data source failed, using free err=%s", exc)
        return FREE


async def fetch_quote(
    source: DataSource, market: str, symbol: str, client: httpx.AsyncClient
) -> QuoteData:
    if source.paid:
        try:
            return await twelvedata.fetch_quote(market, symbol, source.api_key or "", client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("securities: twelvedata quote failed %s:%s err=%s", market, symbol, twelvedata.redact(str(exc)))
    return await yahoo.fetch_quote(market, symbol, client=client)


async def fetch_dividends(
    source: DataSource, market: str, symbol: str, client: httpx.AsyncClient
) -> list[DividendData]:
    if source.paid:
        try:
            events = await twelvedata.fetch_dividends(market, symbol, source.api_key or "", client)
            if events:
                return events
        except Exception as exc:  # noqa: BLE001
            logger.warning("securities: twelvedata dividends failed %s:%s err=%s", market, symbol, twelvedata.redact(str(exc)))
    return await yahoo.fetch_dividends(market, symbol, client)
