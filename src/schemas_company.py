"""Pydantic schemas for 报销公司档案(Company, 0037). Web-first, not sync."""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    name: str
    note: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_utc(self, v: datetime) -> str:
        return _iso_utc(v) or ""


class CompanyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    note: Optional[str] = None


class CompanyUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    note: Optional[str] = None
