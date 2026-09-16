import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.slide import Slide


class SlideRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, slide_id: uuid.UUID) -> Slide | None:
        return self._session.get(Slide, slide_id)

    def get_by_storage_key(self, storage_key: str) -> Slide | None:
        return self._session.scalars(select(Slide).where(Slide.storage_key == storage_key)).first()

    def list_all(self) -> Sequence[Slide]:
        return self._session.scalars(select(Slide).order_by(Slide.created_at.desc())).all()

    def save(self, slide: Slide) -> Slide:
        self._session.add(slide)
        self._session.commit()
        return slide

    def delete(self, slide: Slide) -> None:
        self._session.delete(slide)
        self._session.commit()
