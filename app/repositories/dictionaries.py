from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category


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
