from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.annotation import Annotation
from app.models.cell_type import CellType
from app.models.hotspot import HotspotAnnotation


class CellTypeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, cell_type_id: int) -> CellType | None:
        return self._session.get(CellType, cell_type_id)

    def get_by_name(self, name: str) -> CellType | None:
        return self._session.scalars(select(CellType).where(CellType.name == name)).first()

    def list_all(self) -> Sequence[CellType]:
        return self._session.scalars(select(CellType).order_by(CellType.name)).all()

    def create(self, name: str) -> CellType:
        cell_type = CellType(name=name)
        self._session.add(cell_type)
        self._session.flush()
        return cell_type

    def save(self, cell_type: CellType) -> CellType:
        self._session.add(cell_type)
        self._session.flush()
        return cell_type

    def delete(self, cell_type: CellType) -> None:
        self._session.delete(cell_type)
        self._session.flush()

    def is_linked_to_annotations(self, cell_type_id: int) -> bool:
        """Return True if any slide or hotspot annotation references this cell type."""
        has_slide_annotations = (
            self._session.scalars(
                select(Annotation.id).where(Annotation.cell_type_id == cell_type_id).limit(1)
            ).first()
            is not None
        )
        if has_slide_annotations:
            return True
        has_hotspot_annotations = (
            self._session.scalars(
                select(HotspotAnnotation.id).where(HotspotAnnotation.cell_type_id == cell_type_id).limit(1)
            ).first()
            is not None
        )
        return has_hotspot_annotations
