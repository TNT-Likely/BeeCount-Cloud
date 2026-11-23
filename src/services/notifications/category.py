from sqlalchemy.orm import Session

from ...models import User
from ..data_cleanup.category_orphans import list_category_parent_issues
from .types import CategoryNoticeItem, CategoryParentNotice, notice_id


def category_notices(db: Session, user: User) -> list[CategoryParentNotice]:
    issues = list_category_parent_issues(db, user_id=user.id)
    if not issues:
        return []
    return [
        CategoryParentNotice(
            id=notice_id("category_missing_parent", user.id, [tuple(issue) for issue in issues]),
            count=len(issues),
            categories=[
                CategoryNoticeItem(id=r.sync_id, name=r.name, parent_name=r.parent_name)
                for r in issues[:5]
            ],
        )
    ]
