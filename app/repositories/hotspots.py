import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.hotspot import Hotspot, HotspotAnnotation, HotspotFolder


class HotspotRepository:
    """Hotspot folders, hotspots and hotspot annotations."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_folder(self, folder_id: uuid.UUID) -> HotspotFolder | None:
        return self._session.get(HotspotFolder, folder_id)

    def get_folder_by_name(self, name: str) -> HotspotFolder | None:
        return self._session.scalars(select(HotspotFolder).where(HotspotFolder.name == name)).first()

    def list_folders_with_counts(self) -> Sequence[tuple[HotspotFolder, int]]:
        count = func.count(Hotspot.id)
        statement = (
            select(HotspotFolder, count)
            .outerjoin(Hotspot, Hotspot.folder_id == HotspotFolder.id)
            .group_by(HotspotFolder.id)
            .order_by(HotspotFolder.name)
        )
        return [(folder, hotspot_count) for folder, hotspot_count in self._session.execute(statement).all()]

    def count_in_folder(self, folder_id: uuid.UUID) -> int:
        return self._session.scalar(select(func.count(Hotspot.id)).where(Hotspot.folder_id == folder_id)) or 0

    def get(self, hotspot_id: uuid.UUID) -> Hotspot | None:
        return self._session.get(Hotspot, hotspot_id)

    def list_hotspots(self, folder_id: uuid.UUID | None = None) -> Sequence[Hotspot]:
        statement = select(Hotspot).order_by(Hotspot.created_at.desc())
        if folder_id is not None:
            statement = statement.where(Hotspot.folder_id == folder_id)
        return self._session.scalars(statement).all()

    def get_annotation(self, annotation_id: uuid.UUID) -> HotspotAnnotation | None:
        return self._session.get(HotspotAnnotation, annotation_id)

    def list_annotations(self, hotspot_id: uuid.UUID) -> Sequence[HotspotAnnotation]:
        return self._session.scalars(
            select(HotspotAnnotation)
            .where(HotspotAnnotation.hotspot_id == hotspot_id)
            .order_by(HotspotAnnotation.created_at)
        ).all()

    def save[T: HotspotFolder | Hotspot | HotspotAnnotation](self, entity: T) -> T:
        self._session.add(entity)
        try:
            self._session.commit()
        except Exception:
            # Leaves the session usable, so the caller can turn e.g. an IntegrityError into an API error.
            self._session.rollback()
            raise
        return entity

    def save_all(self, hotspots: Sequence[Hotspot]) -> None:
        self._session.add_all(hotspots)
        self._session.commit()

    def delete(self, entity: HotspotFolder | Hotspot | HotspotAnnotation) -> None:
        # Hotspots and their annotations go with their folder/hotspot through ON DELETE CASCADE.
        self._session.delete(entity)
        self._session.commit()
