import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import NAME_MAX_LENGTH, STORAGE_KEY_MAX_LENGTH
from app.models.base import Base, JsonType


class SlideStatus(enum.StrEnum):
    UPLOADING = "uploading"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


# Must stay >= the longest SlideStatus value (currently "processing", 10 chars) - keep headroom for new ones.
SLIDE_STATUS_MAX_LENGTH = 16
VENDOR_MAX_LENGTH = 64


def _origin() -> dict[str, int]:
    return {"x": 0, "y": 0}


class Slide(Base):
    __tablename__ = "slides"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    original_filename: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    storage_key: Mapped[str] = mapped_column(String(STORAGE_KEY_MAX_LENGTH), unique=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[SlideStatus] = mapped_column(
        Enum(
            SlideStatus,
            name="slide_status",
            native_enum=False,
            create_constraint=True,
            length=SLIDE_STATUS_MAX_LENGTH,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=SlideStatus.UPLOADING,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    # 0-100 while downloading the file during ingestion; None before/after (upload progress is
    # tracked client-side, and there's nothing left to report once the slide is ready/failed).
    ingest_progress: Mapped[int | None]
    vendor: Mapped[str | None] = mapped_column(String(VENDOR_MAX_LENGTH))
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
