from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user, require_any_scopes
from ..models import User, UserProfile
from ..schemas import (
    UserProfileAvatarUploadOut,
    UserProfileOut,
    UserProfilePatchRequest,
)
from ..security import SCOPE_APP_WRITE, SCOPE_OPS_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE

router = APIRouter()
settings = get_settings()
logger = logging.getLogger(__name__)


async def _broadcast_profile_change(
    request: Request, *, user_id: str, payload: dict[str, Any]
) -> None:
    """Profile 是 user-scoped 小状态：上传头像 / 改昵称后，把变更推给该用户所
    有连着的 WS（mobile、多标签 web）。对齐 sync.py:393-409 的 sync_change
    广播模式，但 type 用 `profile_change` 区分，客户端只做 profile refetch
    不去重拉交易等其它 section。失败不 break 请求。"""
    try:
        ws_manager = getattr(request.app.state, "ws_manager", None)
        if ws_manager is None:
            logger.info("avatar_sync: ws_manager unavailable, skip broadcast user=%s", user_id)
            return
        message = {"type": "profile_change", **payload}
        logger.info("avatar_sync: broadcast profile_change user=%s payload=%s", user_id, payload)
        await ws_manager.broadcast_to_user(user_id, message)
        logger.info("avatar_sync: broadcast done user=%s", user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("avatar_sync: broadcast failed user=%s err=%s", user_id, exc)
_READ_SCOPE_DEP = require_any_scopes(
    SCOPE_APP_WRITE,
    SCOPE_WEB_READ,
    SCOPE_WEB_WRITE,
    SCOPE_OPS_WRITE,
)
_AVATAR_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE)
# PATCH /profile/me 需要让 mobile（app.write）也能调：mobile 要推收支颜色 /
# 主题色到 server。如果只开 web/ops，mobile 的推送全部返回 Insufficient scope。
# 跟 avatar upload 的 scope 集合保持一致。
_PATCH_SCOPE_DEP = require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_WRITE, SCOPE_OPS_WRITE)

_AVATAR_MAX_UPLOAD_BYTES = 1 * 1024 * 1024
_ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _avatar_root() -> Path:
    root = Path(settings.attachment_storage_dir).expanduser() / "profile-avatars"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _avatar_url(*, user_id: str, avatar_version: int | None = None) -> str:
    base = f"{settings.api_prefix}/profile/avatar/{user_id}"
    if avatar_version is None:
        return base
    return f"{base}?v={avatar_version}"


def _guess_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _resolve_avatar_extension(file: UploadFile) -> str:
    content_type = (file.content_type or "").strip().lower()
    if content_type in _ALLOWED_IMAGE_MIME_TYPES:
        return _ALLOWED_IMAGE_MIME_TYPES[content_type]

    file_name = (file.filename or "").strip().lower()
    if file_name.endswith(".jpg") or file_name.endswith(".jpeg"):
        return "jpg"
    if file_name.endswith(".png"):
        return "png"
    if file_name.endswith(".webp"):
        return "webp"

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Profile avatar format invalid",
    )


def _parse_appearance_json(raw: str | None) -> dict | None:
    """把 DB 里的 appearance_json TEXT 解析成 dict。无值 / 非法 JSON 都返 None,
    让客户端按"未设置"处理,不至于请求失败。"""
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        logger.warning("profile appearance_json parse failed: %s", stripped[:80])
        return None
    return parsed if isinstance(parsed, dict) else None


def _dump_appearance_json(value: dict | None) -> str | None:
    """把客户端传来的 dict 序列化成 TEXT 存库。None / 空 dict 都存 NULL 清掉。"""
    if value is None or not isinstance(value, dict) or not value:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@router.get("/me", response_model=UserProfileOut)
def get_my_profile(
    _scopes: set[str] = Depends(_READ_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    display_name = profile.display_name if profile is not None else None
    avatar_file_id = profile.avatar_file_id if profile is not None else None
    avatar_version = profile.avatar_version if profile is not None else 0
    income_is_red = profile.income_is_red if profile is not None else None
    theme_primary_color = profile.theme_primary_color if profile is not None else None
    appearance = _parse_appearance_json(profile.appearance_json) if profile is not None else None
    ai_config = _parse_appearance_json(profile.ai_config_json) if profile is not None else None
    return UserProfileOut(
        user_id=current_user.id,
        email=current_user.email,
        display_name=display_name,
        avatar_url=_avatar_url(user_id=current_user.id, avatar_version=avatar_version)
        if avatar_file_id
        else None,
        avatar_version=avatar_version,
        income_is_red=income_is_red,
        theme_primary_color=theme_primary_color,
        appearance=appearance,
        ai_config=ai_config,
    )


@router.patch("/me", response_model=UserProfileOut)
async def patch_my_profile(
    req: UserProfilePatchRequest,
    request: Request,
    _scopes: set[str] = Depends(_PATCH_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    now = datetime.now(timezone.utc)
    if profile is None:
        profile = UserProfile(
            user_id=current_user.id,
            display_name=req.display_name,
            income_is_red=req.income_is_red,
            theme_primary_color=req.theme_primary_color,
            appearance_json=_dump_appearance_json(req.appearance),
            ai_config_json=_dump_appearance_json(req.ai_config),
            updated_at=now,
        )
        db.add(profile)
    else:
        # 只更新显式提供的字段，None 表示不改。这样 mobile 切单项配色时
        # 不会把其它字段清掉；反之亦然。appearance / ai_config 例外:客户端
        # 传 {} 时视为清空（_dump_appearance_json 返回 None），传 dict 时
        # 整体替换。
        if req.display_name is not None:
            profile.display_name = req.display_name
        if req.income_is_red is not None:
            profile.income_is_red = req.income_is_red
        if req.theme_primary_color is not None:
            profile.theme_primary_color = req.theme_primary_color
        if req.appearance is not None:
            profile.appearance_json = _dump_appearance_json(req.appearance)
        if req.ai_config is not None:
            profile.ai_config_json = _dump_appearance_json(req.ai_config)
        profile.updated_at = now
    db.commit()
    db.refresh(profile)
    logger.info(
        "profile_patch: user=%s display_name=%s income_is_red=%s theme=%s appearance=%s ai_config_len=%s avatar_version=%s",
        current_user.id,
        profile.display_name,
        profile.income_is_red,
        profile.theme_primary_color,
        profile.appearance_json,
        len(profile.ai_config_json or ""),
        profile.avatar_version,
    )
    appearance = _parse_appearance_json(profile.appearance_json)
    ai_config = _parse_appearance_json(profile.ai_config_json)
    await _broadcast_profile_change(
        request,
        user_id=current_user.id,
        payload={
            "avatar_version": profile.avatar_version,
            "display_name": profile.display_name,
            "income_is_red": profile.income_is_red,
            "theme_primary_color": profile.theme_primary_color,
            "appearance": appearance,
            # ai_config 可能很大(providers 数组里若干对象),WS payload 不塞,
            # 客户端收到 profile_change 自己拉 /profile/me 即可。
        },
    )
    return UserProfileOut(
        user_id=current_user.id,
        email=current_user.email,
        display_name=profile.display_name,
        avatar_url=_avatar_url(
            user_id=current_user.id,
            avatar_version=profile.avatar_version,
        )
        if profile.avatar_file_id
        else None,
        avatar_version=profile.avatar_version,
        income_is_red=profile.income_is_red,
        theme_primary_color=profile.theme_primary_color,
        appearance=appearance,
        ai_config=ai_config,
    )


@router.post("/avatar", response_model=UserProfileAvatarUploadOut)
async def upload_my_avatar(
    request: Request,
    file: UploadFile = File(...),
    _scopes: set[str] = Depends(_AVATAR_SCOPE_DEP),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserProfileAvatarUploadOut:
    ext = _resolve_avatar_extension(file)
    payload = await file.read()
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile avatar file is empty",
        )
    if len(payload) > _AVATAR_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Profile avatar upload too large",
        )
    logger.info(
        "avatar_sync: upload start user=%s size=%d content_type=%s",
        current_user.id,
        len(payload),
        file.content_type,
    )

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == current_user.id))
    if profile is None:
        profile = UserProfile(
            user_id=current_user.id,
            avatar_version=0,
        )
        db.add(profile)
        db.flush()

    now = datetime.now(timezone.utc)
    avatar_file_id = f"avatar_{uuid4().hex}.{ext}"
    storage_dir = _avatar_root() / current_user.id
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / avatar_file_id
    storage_path.write_bytes(payload)

    # Best-effort cleanup of the previous avatar file.
    previous_avatar = (profile.avatar_file_id or "").strip()
    if previous_avatar:
        old_path = storage_dir / previous_avatar
        if old_path.exists() and old_path.is_file():
            old_path.unlink(missing_ok=True)

    profile.avatar_file_id = avatar_file_id
    profile.avatar_version = int(profile.avatar_version or 0) + 1
    profile.updated_at = now
    db.commit()
    db.refresh(profile)
    logger.info(
        "avatar_sync: saved user=%s file_id=%s new_version=%d",
        current_user.id,
        avatar_file_id,
        profile.avatar_version,
    )
    await _broadcast_profile_change(
        request,
        user_id=current_user.id,
        payload={
            "avatar_version": profile.avatar_version,
            "display_name": profile.display_name,
            "income_is_red": profile.income_is_red,
            "theme_primary_color": profile.theme_primary_color,
        },
    )
    return UserProfileAvatarUploadOut(
        avatar_url=_avatar_url(
            user_id=current_user.id,
            avatar_version=profile.avatar_version,
        ),
        avatar_version=profile.avatar_version,
    )


@router.get("/avatar/{user_id}")
def download_avatar(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> FileResponse:
    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None or not (profile.avatar_file_id or "").strip():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile avatar not found")

    path = _avatar_root() / user_id / profile.avatar_file_id
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile avatar not found")

    cache_control = (
        "public, max-age=31536000, immutable"
        if (request.query_params.get("v") or "").strip()
        else "no-cache"
    )
    return FileResponse(
        path=path,
        media_type=_guess_mime_type(path),
        filename=path.name,
        headers={"Cache-Control": cache_control},
    )
