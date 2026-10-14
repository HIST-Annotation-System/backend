import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.cell_type import CellType

from app.core.constants import NAME_MAX_LENGTH
from app.models.base import Base, JsonType


class HotspotFolder(Base):
    """A named folder of the hotspot catalog (flat: folders do not nest)."""

    __tablename__ = "hotspot_folders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HotspotImageType(enum.StrEnum):
    PNG = "png"
    JPEG = "jpeg"


class HotspotFeature(enum.StrEnum):
    """Image descriptors the catalog can be sorted by (see app.services.hotspot_features)."""

    ENTROPY = "entropy"
    GLCM_CONTRAST = "glcm_contrast"
    EDGE_DENSITY = "edge_density"
    COLORFULNESS = "colorfulness"


# Must stay >= the longest HotspotImageType value.
HOTSPOT_IMAGE_TYPE_MAX_LENGTH = 8


class Hotspot(Base):
    """An image to annotate, stored on its own so it outlives any source slide.

    Either a square captured from a slide (slide_id/slide_name/x/y/size are set), or an image uploaded by
    the user (they are all null).
    """

    __tablename__ = "hotspots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspot_folders.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    # The source slide; kept as a name too, since deleting the slide keeps the hotspot.
    slide_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("slides.id", ondelete="SET NULL"), index=True)
    slide_name: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH))
    # The captured square in level-0 slide pixels.
    x: Mapped[int | None]
    y: Mapped[int | None]
    size: Mapped[int | None]
    # The stored image in pixels (a captured square larger than the cap is scaled down).
    image_width: Mapped[int]
    image_height: Mapped[int]
    image_type: Mapped[HotspotImageType] = mapped_column(
        Enum(
            HotspotImageType,
            name="hotspot_image_type",
            native_enum=False,
            create_constraint=True,
            length=HOTSPOT_IMAGE_TYPE_MAX_LENGTH,
            values_callable=lambda types: [item.value for item in types],
        ),
        default=HotspotImageType.PNG,
    )
    # Microns per pixel of the stored image, when the slide reports its resolution.
    mpp: Mapped[float | None]
    # HotspotFeature value -> descriptor of the thumbnail; null (or missing a key) until computed.
    features: Mapped[dict[str, float] | None] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HotspotAnnotation(Base):
    __tablename__ = "hotspot_annotations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), index=True)
    cell_type_id: Mapped[int | None] = mapped_column(ForeignKey("cell_types.id", ondelete="RESTRICT"), index=True)
    # GeoJSON Polygon in hotspot image pixels (closed exterior ring).
    geometry: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cell_type: Mapped["CellType | None"] = relationship(back_populates="hotspot_annotations")
