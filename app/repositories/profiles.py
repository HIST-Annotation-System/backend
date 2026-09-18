from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category
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

    def create(self, profile: AnnotatorProfile) -> AnnotatorProfile:
        self.session.add(profile)
        self.session.flush()
        return profile
