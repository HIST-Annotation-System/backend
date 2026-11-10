import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.orm import Session

from app.models.hotspot import (
    AnnotationKind,
    Hotspot,
    HotspotAnnotation,
    HotspotBatch,
    HotspotBatchStatus,
    HotspotFolder,
    HotspotImageType,
    SegmentationStatus,
)


class HotspotRepository:
    """Hotspot folders, hotspots, hotspot annotations and hotspot batches.

    `organization_id` limits folders, and the hotspots and batches in them, to that organization (None: all).
    Annotations are reached through their hotspot, so callers check the hotspot first.
    """

    def __init__(self, session: Session, organization_id: int | None = None) -> None:
        self._session = session
        self.organization_id = organization_id

    def _in_scope(self, folder: HotspotFolder | None) -> bool:
        return folder is not None and (self.organization_id is None or folder.organization_id == self.organization_id)

    def _scoped[T: Any](self, statement: Select[T], folder_id: Any) -> Select[T]:
        """`statement` limited to rows whose folder (the `folder_id` column) is in scope."""
        if self.organization_id is None:
            return statement
        in_scope = select(HotspotFolder.id).where(HotspotFolder.organization_id == self.organization_id)
        return statement.where(folder_id.in_(in_scope))

    def get_folder(self, folder_id: uuid.UUID) -> HotspotFolder | None:
        folder = self._session.get(HotspotFolder, folder_id)
        return folder if self._in_scope(folder) else None

    def get_folder_by_name(self, name: str, organization_id: int) -> HotspotFolder | None:
        return self._session.scalars(
            select(HotspotFolder).where(HotspotFolder.organization_id == organization_id, HotspotFolder.name == name)
        ).first()

    def list_folders_with_counts(self) -> Sequence[tuple[HotspotFolder, int]]:
        count = func.count(Hotspot.id)
        statement = (
            self._scoped(select(HotspotFolder, count), HotspotFolder.id)
            .outerjoin(Hotspot, Hotspot.folder_id == HotspotFolder.id)
            .group_by(HotspotFolder.id)
            .order_by(HotspotFolder.name)
        )
        return [(folder, hotspot_count) for folder, hotspot_count in self._session.execute(statement).all()]

    def count_in_folder(self, folder_id: uuid.UUID) -> int:
        return self._session.scalar(select(func.count(Hotspot.id)).where(Hotspot.folder_id == folder_id)) or 0

    def get(self, hotspot_id: uuid.UUID) -> Hotspot | None:
        hotspot = self._session.get(Hotspot, hotspot_id)
        if hotspot is None or self.organization_id is None:
            return hotspot
        return hotspot if self._in_scope(self._session.get(HotspotFolder, hotspot.folder_id)) else None

    def get_for_update(self, hotspot_id: uuid.UUID) -> Hotspot | None:
        """The hotspot with its row locked until the next commit or rollback."""
        # The lock takes a query of its own: the hotspot's column properties and eager loads don't belong under it.
        locked = self._session.scalar(select(Hotspot.id).where(Hotspot.id == hotspot_id).with_for_update())
        return None if locked is None else self.get(locked)

    def claim_pending_segmentations(
        self, requested_before: datetime, limit: int
    ) -> Sequence[tuple[uuid.UUID, HotspotImageType]]:
        """(id, image type) of pending hotspots not requested lately, newest first, so current work goes ahead of any
        backlog. Their rows stay locked until the next commit, and other stacks skip them."""
        rows = self._session.execute(
            select(Hotspot.id, Hotspot.image_type)
            .where(
                Hotspot.segmentation_status == SegmentationStatus.PENDING,
                or_(Hotspot.segmentation_requested_at.is_(None), Hotspot.segmentation_requested_at < requested_before),
            )
            .order_by(Hotspot.created_at.desc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        return [(hotspot_id, image_type) for hotspot_id, image_type in rows]

    def mark_segmentations_requested(self, hotspot_ids: Sequence[uuid.UUID], requested_at: datetime) -> None:
        """Commits, which also releases the rows of the claim."""
        if hotspot_ids:
            self._session.execute(
                update(Hotspot).where(Hotspot.id.in_(hotspot_ids)).values(segmentation_requested_at=requested_at)
            )
        self._session.commit()

    def skip_pending_segmentation(self, hotspot_id: uuid.UUID) -> None:
        """Conditional in the database, so it can't overwrite a result saved a moment ago. Not committed here."""
        self._session.execute(
            update(Hotspot)
            .where(Hotspot.id == hotspot_id, Hotspot.segmentation_status == SegmentationStatus.PENDING)
            .values(segmentation_status=SegmentationStatus.SKIPPED)
        )

    def list_hotspots(
        self, folder_id: uuid.UUID | None = None, slide_id: uuid.UUID | None = None
    ) -> Sequence[Hotspot]:
        statement = self._scoped(select(Hotspot), Hotspot.folder_id).order_by(Hotspot.created_at.desc())
        if folder_id is not None:
            statement = statement.where(Hotspot.folder_id == folder_id)
        if slide_id is not None:
            statement = statement.where(Hotspot.slide_id == slide_id)
        return self._session.scalars(statement).all()

    def list_by_ids(self, hotspot_ids: Sequence[uuid.UUID]) -> Sequence[Hotspot]:
        return self._session.scalars(
            self._scoped(select(Hotspot), Hotspot.folder_id).where(Hotspot.id.in_(hotspot_ids))
        ).all()

    def get_batch(self, batch_id: uuid.UUID) -> HotspotBatch | None:
        batch = self._session.get(HotspotBatch, batch_id)
        return batch if batch is not None and self._in_scope(batch.folder) else None

    def list_batches(self, failed_since: datetime) -> Sequence[HotspotBatch]:
        """Running batches, and those that failed after `failed_since`; newest first."""
        return self._session.scalars(
            self._scoped(select(HotspotBatch), HotspotBatch.folder_id)
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

    def list_excluded_areas(self, hotspot_id: uuid.UUID) -> Sequence[HotspotAnnotation]:
        return self._session.scalars(
            select(HotspotAnnotation).where(
                HotspotAnnotation.hotspot_id == hotspot_id, HotspotAnnotation.kind == AnnotationKind.EXCLUDED
            )
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

    def save_merged(self, kept: HotspotAnnotation, absorbed: Sequence[HotspotAnnotation]) -> HotspotAnnotation:
        """Stores a merged annotation and deletes the ones merged into it, in one transaction."""
        self._session.add(kept)
        for annotation in absorbed:
            self._session.delete(annotation)
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return kept

    def delete(self, entity: HotspotFolder | Hotspot | HotspotAnnotation) -> None:
        # Hotspots and their annotations go with their folder/hotspot through ON DELETE CASCADE.
        self._session.delete(entity)
        self._session.commit()
