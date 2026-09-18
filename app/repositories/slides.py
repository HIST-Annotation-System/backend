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

    def find_overlapping_storage_key(self, key: str) -> Slide | None:
        """A slide whose storage key equals `key`, lies inside folder `key`, or is a folder containing `key`."""
        exact = self.get_by_storage_key(key)
        if exact is not None:
            return exact
        if key.endswith("/"):
            inside = self._session.scalars(
                select(Slide).where(Slide.storage_key.startswith(key, autoescape=True))
            ).first()
            if inside is not None:
                return inside
        folders = self._session.scalars(select(Slide).where(Slide.storage_key.endswith("/"))).all()
        return next((slide for slide in folders if key.startswith(slide.storage_key)), None)

    def list_all(self) -> Sequence[Slide]:
        return self._session.scalars(select(Slide).order_by(Slide.created_at.desc())).all()

    def save(self, slide: Slide) -> Slide:
        self._session.add(slide)
        self._session.commit()
        return slide

    def delete(self, slide: Slide) -> None:
        self._session.delete(slide)
        self._session.commit()
