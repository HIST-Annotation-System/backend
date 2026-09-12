import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.annotation import Annotation, AnnotationClass


class AnnotationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, annotation_id: uuid.UUID) -> Annotation | None:
        return self._session.get(Annotation, annotation_id)

    def list_for_slide(self, slide_id: uuid.UUID) -> Sequence[Annotation]:
        return self._session.scalars(
            select(Annotation).where(Annotation.slide_id == slide_id).order_by(Annotation.created_at)
        ).all()

    def save(self, annotation: Annotation) -> Annotation:
        self._session.add(annotation)
        self._session.commit()
        return annotation

    def delete(self, annotation: Annotation) -> None:
        self._session.delete(annotation)
        self._session.commit()

    def get_class(self, class_id: uuid.UUID) -> AnnotationClass | None:
        return self._session.get(AnnotationClass, class_id)

    def list_classes(self) -> Sequence[AnnotationClass]:
        return self._session.scalars(select(AnnotationClass).order_by(AnnotationClass.sort_order)).all()
