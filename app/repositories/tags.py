from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.models.tag import Tag

# Core tags first (importance, then accuracy; heaviest first), then the rest by name.
_ORDER = (Tag.dimension.is_(None), Tag.dimension.desc(), Tag.weight.desc(), Tag.name)


class TagRepository:
    """Tags seen from an organization: the global ones and its own.

    `organization_id` None (the system admin, or background work) sees every organization's tags.
    """

    def __init__(self, session: Session, organization_id: int | None = None) -> None:
        self._session = session
        self.organization_id = organization_id

    def _visible[T: Any](self, statement: Select[T]) -> Select[T]:
        if self.organization_id is None:
            return statement
        return statement.where(or_(Tag.organization_id.is_(None), Tag.organization_id == self.organization_id))

    def get(self, tag_id: int) -> Tag | None:
        tag = self._session.get(Tag, tag_id)
        if tag is None or self.organization_id is None:
            return tag
        return tag if tag.organization_id in (None, self.organization_id) else None

    def name_taken(self, name: str, organization_id: int | None) -> bool:
        """Whether a tag created in `organization_id` (None: a global one) would share its name with a tag seen
        by the same people: a global name clashes with every tag, an organization's with its own and the global."""
        statement = select(Tag.id).where(Tag.name == name)
        if organization_id is not None:
            statement = statement.where(or_(Tag.organization_id.is_(None), Tag.organization_id == organization_id))
        return self._session.scalars(statement.limit(1)).first() is not None

    def list_all(self) -> Sequence[Tag]:
        return self._session.scalars(self._visible(select(Tag)).order_by(*_ORDER)).all()

    def list_every_organization(self) -> Sequence[Tag]:
        """Every tag, whatever the scope: the weight cap that keeps custom tags below the core ones is the same for
        every organization."""
        return self._session.scalars(select(Tag).order_by(*_ORDER)).all()

    def list_by_ids(self, tag_ids: Sequence[int]) -> Sequence[Tag]:
        return self._session.scalars(self._visible(select(Tag)).where(Tag.id.in_(tag_ids))).all()

    def save(self, tag: Tag) -> Tag:
        self._session.add(tag)
        self._session.flush()
        return tag

    def delete(self, tag: Tag) -> None:
        self._session.delete(tag)
        self._session.flush()
