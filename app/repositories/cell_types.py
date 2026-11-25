from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cell_type import CellType
from app.models.hotspot import HotspotAnnotation


class CellTypeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, cell_type_id: int) -> CellType | None:
        return self._session.get(CellType, cell_type_id)

    def get_by_name(self, name: str) -> CellType | None:
        """Case-insensitive, matching the unique index on lower(name)."""
        return self._session.scalars(select(CellType).where(func.lower(CellType.name) == name.lower())).first()

    def list_all(self) -> Sequence[CellType]:
        return self._session.scalars(select(CellType).order_by(CellType.name)).all()

    def create(self, name: str) -> CellType:
        return self.save(CellType(name=name))

    def save(self, cell_type: CellType) -> CellType:
        # A savepoint, so a unique violation (a concurrent request took the name) leaves the session usable
        # and the caller can turn the IntegrityError into an API error.
        with self._session.begin_nested():
            self._session.add(cell_type)
            self._session.flush()
        return cell_type

    def delete(self, cell_type: CellType) -> None:
        self._session.delete(cell_type)
        self._session.flush()

    def is_linked_to_annotations(self, cell_type_id: int) -> bool:
        """Return True if any hotspot annotation references this cell type."""
        return (
            self._session.scalars(
                select(HotspotAnnotation.id).where(HotspotAnnotation.cell_type_id == cell_type_id).limit(1)
            ).first()
            is not None
        )
