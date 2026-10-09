import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.slide import Slide, SlideStatus

# Arbitrary app-wide key of the PostgreSQL advisory lock that serializes imports from storage.
IMPORT_LOCK_KEY = 0x5A_50_49_01


class SlideRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, slide_id: uuid.UUID) -> Slide | None:
        return self._session.get(Slide, slide_id)

    def get_for_update(self, slide_id: uuid.UUID) -> Slide | None:
        """The slide, row-locked until the next commit so concurrent edits of it run one after another.

        `of=Slide`: the uploader is outer-joined in, and PostgreSQL refuses to lock the nullable side
        of an outer join - only the slide row needs the lock anyway.
        """
        return self._session.get(Slide, slide_id, with_for_update={"of": Slide}, populate_existing=True)

    def exists(self, slide_id: uuid.UUID) -> bool:
        """Checked against the database, not the session's identity map (another request may have deleted it)."""
        return self._session.scalar(select(Slide.id).where(Slide.id == slide_id)) is not None

    def lock_imports(self) -> None:
        """Serializes imports until the next commit (the overlap check and the insert must be atomic).

        A no-op outside PostgreSQL, where tests run on a single connection anyway.
        """
        if self._session.get_bind().dialect.name == "postgresql":
            self._session.execute(select(func.pg_advisory_xact_lock(IMPORT_LOCK_KEY)))

    def get_by_storage_key(self, storage_key: str) -> Slide | None:
        return self._session.scalars(select(Slide).where(Slide.storage_key == storage_key)).first()

    def find_duplicate(self, original_filename: str, file_size_bytes: int) -> Slide | None:
        """A slide (not failed) uploaded from a file with the same name and size - most likely the same file."""
        return self._session.scalars(
            select(Slide)
            .where(
                Slide.original_filename == original_filename,
                Slide.file_size_bytes == file_size_bytes,
                Slide.status != SlideStatus.FAILED,
            )
            .order_by(Slide.created_at)
        ).first()

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

    def rollback(self) -> None:
        self._session.rollback()
