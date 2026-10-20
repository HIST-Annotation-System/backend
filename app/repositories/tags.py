from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tag import Tag


class TagRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, tag_id: int) -> Tag | None:
        return self._session.get(Tag, tag_id)

    def get_by_name(self, name: str) -> Tag | None:
        return self._session.scalars(select(Tag).where(Tag.name == name)).first()

    def list_all(self) -> Sequence[Tag]:
        """Core tags first (importance, then accuracy; heaviest first), then the rest by name."""
        return self._session.scalars(
            select(Tag).order_by(Tag.dimension.is_(None), Tag.dimension.desc(), Tag.weight.desc(), Tag.name)
        ).all()

    def list_by_ids(self, tag_ids: Sequence[int]) -> Sequence[Tag]:
        return self._session.scalars(select(Tag).where(Tag.id.in_(tag_ids))).all()

    def save(self, tag: Tag) -> Tag:
        self._session.add(tag)
        self._session.flush()
        return tag

    def delete(self, tag: Tag) -> None:
        self._session.delete(tag)
        self._session.flush()
