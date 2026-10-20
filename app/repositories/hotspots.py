import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.hotspot import Hotspot, HotspotAnnotation, HotspotBatch, HotspotBatchStatus, HotspotFolder


class HotspotRepository:
    """Hotspot folders, hotspots, hotspot annotations and hotspot batches."""

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

    def list_hotspots(
        self, folder_id: uuid.UUID | None = None, slide_id: uuid.UUID | None = None
    ) -> Sequence[Hotspot]:
        statement = select(Hotspot).order_by(Hotspot.created_at.desc())
        if folder_id is not None:
            statement = statement.where(Hotspot.folder_id == folder_id)
        if slide_id is not None:
            statement = statement.where(Hotspot.slide_id == slide_id)
        return self._session.scalars(statement).all()

    def list_by_ids(self, hotspot_ids: Sequence[uuid.UUID]) -> Sequence[Hotspot]:
        return self._session.scalars(select(Hotspot).where(Hotspot.id.in_(hotspot_ids))).all()

    def get_batch(self, batch_id: uuid.UUID) -> HotspotBatch | None:
        return self._session.get(HotspotBatch, batch_id)

    def list_batches(self, failed_since: datetime) -> Sequence[HotspotBatch]:
        """Running batches, and those that failed after `failed_since`; newest first."""
        return self._session.scalars(
            select(HotspotBatch)
            .where(
                or_(
                    HotspotBatch.status == HotspotBatchStatus.RUNNING,
                    (HotspotBatch.status == HotspotBatchStatus.FAILED) & (HotspotBatch.created_at >= failed_since),
                )
            )
            .order_by(HotspotBatch.created_at.desc())
        ).all()

    def list_running_batches(self, created_before: datetime) -> Sequence[HotspotBatch]:
        return self._session.scalars(
            select(HotspotBatch).where(
                HotspotBatch.status == HotspotBatchStatus.RUNNING, HotspotBatch.created_at < created_before
            )
        ).all()

    def get_annotation(self, annotation_id: uuid.UUID) -> HotspotAnnotation | None:
        return self._session.get(HotspotAnnotation, annotation_id)

    def list_annotations(self, hotspot_id: uuid.UUID) -> Sequence[HotspotAnnotation]:
        return self._session.scalars(
            select(HotspotAnnotation)
            .where(HotspotAnnotation.hotspot_id == hotspot_id)
            .order_by(HotspotAnnotation.created_at)
        ).all()

    def save[T: HotspotFolder | Hotspot | HotspotAnnotation | HotspotBatch](self, entity: T) -> T:
        self._session.add(entity)
        try:
            self._session.commit()
        except Exception:
            # Leaves the session usable, so the caller can turn e.g. an IntegrityError into an API error.
            self._session.rollback()
            raise
        return entity

    def save_all(self, entities: Sequence[Hotspot | HotspotBatch]) -> None:
        self._session.add_all(entities)
        self._session.commit()

    def delete(self, entity: HotspotFolder | Hotspot | HotspotAnnotation) -> None:
        # Hotspots and their annotations go with their folder/hotspot through ON DELETE CASCADE.
        self._session.delete(entity)
        self._session.commit()
