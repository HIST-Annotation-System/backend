from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.group import AnnotationGroup
from app.models.profile import AnnotatorProfile


class ProfileRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_categories_by_ids(self, category_ids: list[int]) -> list[Category]:
        if not category_ids:
            return []
        return list(
            self.session.execute(select(Category).where(Category.id.in_(category_ids))).scalars().all()
        )

    def get_group_by_id(self, group_id: int) -> AnnotationGroup | None:
        return self.session.execute(select(AnnotationGroup).where(AnnotationGroup.id == group_id)).scalar_one_or_none()

    def create(self, profile: AnnotatorProfile) -> AnnotatorProfile:
        self.session.add(profile)
        self.session.flush()
        return profile

    def update(self) -> None:
        self.session.flush()

    def delete(self, profile: AnnotatorProfile) -> None:
        self.session.delete(profile)
        self.session.flush()
