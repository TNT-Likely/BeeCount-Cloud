"""Register active-notice providers here. Each provider owns its scope and incident identity."""

from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

from ...models import User
from .backup import backup_notices
from .category import category_notices
from .device import device_notices
from .types import CloudNotice

NoticeProvider = Callable[[Session, User], Sequence[CloudNotice]]
PROVIDERS: tuple[NoticeProvider, ...] = (backup_notices, device_notices, category_notices)


def active_notices(db: Session, user: User) -> list[CloudNotice]:
    return [notice for provider in PROVIDERS for notice in provider(db, user)]
