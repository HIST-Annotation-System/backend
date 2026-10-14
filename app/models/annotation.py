import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JsonType

if TYPE_CHECKING:
    from app.models.cell_type import CellType


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slide_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    cell_type_id: Mapped[int | None] = mapped_column(ForeignKey("cell_types.id", ondelete="RESTRICT"), index=True)
    # GeoJSON Polygon in level-0 slide pixels (closed exterior ring).
    geometry: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cell_type: Mapped["CellType | None"] = relationship(back_populates="annotations")
