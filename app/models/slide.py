import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JsonType


class SlideStatus(enum.StrEnum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


def _origin() -> dict[str, int]:
    return {"x": 0, "y": 0}


class Slide(Base):
    __tablename__ = "slides"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[SlideStatus] = mapped_column(
        Enum(
            SlideStatus,
            name="slide_status",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=SlideStatus.UPLOADING,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    vendor: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None]
    height: Mapped[int | None]
    level_count: Mapped[int | None]
    mpp_x: Mapped[float | None]
    objective_power: Mapped[float | None]
    # Deep Zoom image coordinates + image_offset = level-0 slide coordinates.
    image_offset: Mapped[dict[str, int]] = mapped_column(JsonType, default=_origin)
    # {x, y, width, height} of the detected tissue, in level-0 pixels.
    tissue_bbox: Mapped[dict[str, int] | None] = mapped_column(JsonType)
    properties: Mapped[dict[str, str]] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
