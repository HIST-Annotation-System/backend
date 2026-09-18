from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.group import AnnotationGroup


class CategoryGroupRepository:

    def __init__(self, session: Session):
        self._session = session

    def get_all_categories(self) -> list[Category]:
        return list(self._session.execute(select(Category)).scalars().all())

    def get_category_by_id(self, category_id: int) -> Category | None:
        return self._session.execute(select(Category).where(Category.id == category_id)).scalar_one_or_none()

    def create_category(self, name: str) -> Category:
        category = Category(name=name)
        self._session.add(category)
        self._session.flush()
        return category

    def delete_category(self, category: Category) -> None:
        self._session.delete(category)
        self._session.flush()

    def get_all_groups(self) -> list[AnnotationGroup]:
        return list(self._session.execute(select(AnnotationGroup)).scalars().all())

    def get_group_by_id(self, group_id: int) -> AnnotationGroup | None:
        return self._session.execute(select(AnnotationGroup).where(AnnotationGroup.id == group_id)).scalar_one_or_none()

    def create_group(self, name: str) -> AnnotationGroup:
        group = AnnotationGroup(name=name)
        self._session.add(group)
        self._session.flush()
        return group

    def delete_group(self, group: AnnotationGroup) -> None:
        self._session.delete(group)
        self._session.flush()
