"""Web-first REST endpoints for 报销公司档案(Company, 0037)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Company, User
from ..schemas_company import CompanyCreateIn, CompanyOut, CompanyUpdateIn

company_router = APIRouter()


def _out(c: Company) -> CompanyOut:
    return CompanyOut.model_validate(c)


@company_router.get("", response_model=list[CompanyOut])
def list_companies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CompanyOut]:
    rows = db.scalars(
        select(Company).where(Company.user_id == current_user.id).order_by(Company.name)
    ).all()
    return [_out(c) for c in rows]


@company_router.post("", response_model=CompanyOut, status_code=201)
def create_company(
    req: CompanyCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyOut:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="company name is required")
    existing = db.scalar(
        select(Company).where(Company.user_id == current_user.id, Company.name == name)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="company already exists")
    c = Company(user_id=current_user.id, name=name, note=(req.note or "").strip() or None)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _out(c)


@company_router.patch("/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: str,
    req: CompanyUpdateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanyOut:
    c = db.get(Company, company_id)
    if c is None or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="company not found")
    changes = req.model_dump(exclude_unset=True)
    if "name" in changes:
        name = (changes["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="company name is required")
        duplicate = db.scalar(
            select(Company).where(
                Company.user_id == current_user.id, Company.name == name, Company.id != c.id
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="company already exists")
        c.name = name
    if "note" in changes:
        c.note = (changes["note"] or "").strip() or None
    c.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(c)
    return _out(c)


@company_router.delete("/{company_id}", status_code=204)
def delete_company(
    company_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """删除公司档案；报销项/报销单保留公司名快照，不受影响。"""
    c = db.get(Company, company_id)
    if c is None or c.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="company not found")
    db.delete(c)
    db.commit()
