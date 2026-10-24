from typing import TYPE_CHECKING

from sqlalchemy import Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.hotspot import HotspotAnnotation

CELL_TYPE_NAME_MAX_LENGTH = 128
CELL_TYPE_NAME_LOWER_INDEX = "uq_cell_types_name_lower"


class CellType(Base):
    __tablename__ = "cell_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(CELL_TYPE_NAME_MAX_LENGTH), nullable=False, unique=True)

    hotspot_annotations: Mapped[list["HotspotAnnotation"]] = relationship(back_populates="cell_type")


# "CD8" and "cd8" are the same cell type: the database rejects names that differ only in letter case.
Index(CELL_TYPE_NAME_LOWER_INDEX, func.lower(CellType.name), unique=True)
