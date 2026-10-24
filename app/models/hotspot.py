import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.cell_type import CellType
    from app.models.tag import HotspotTag, Tag

from app.core.constants import NAME_MAX_LENGTH
from app.models.base import Base, JsonType, NullableJsonType
from app.models.tag import TagDimension


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
    # When an annotator marked the hotspot as reannotated; cleared when an expert changes its verdict.
    reannotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who marked it reannotated; null while it is not, for marks made before this was recorded, and once that
    # account is deleted.
    reannotated_by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tag_links: Mapped[list["HotspotTag"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def tags(self) -> list["Tag"]:
        """Core tags first (in TagDimension order), then the rest by name."""
        order = list(TagDimension)
        tags = (link.tag for link in self.tag_links)
        return sorted(tags, key=lambda tag: (order.index(tag.dimension) if tag.dimension else len(order), tag.name))

    @property
    def score(self) -> int:
        return sum(link.tag.weight for link in self.tag_links)

    def dimension_tag(self, dimension: TagDimension) -> "Tag | None":
        return next((link.tag for link in self.tag_links if link.tag.dimension == dimension), None)

    @property
    def assessed_by(self) -> str | None:
        """Who set the latest importance/accuracy verdict (None if nobody did, or the account is gone)."""
        verdicts = [link for link in self.tag_links if link.tag.dimension is not None]
        latest = max(verdicts, key=lambda link: link.created_at, default=None)
        return latest.added_by.username if latest and latest.added_by else None


class HotspotAnnotation(Base):
    __tablename__ = "hotspot_annotations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), index=True)
    cell_type_id: Mapped[int | None] = mapped_column(ForeignKey("cell_types.id", ondelete="RESTRICT"), index=True)
    # GeoJSON Polygon in hotspot image pixels (closed exterior ring).
    geometry: Mapped[dict[str, Any]] = mapped_column(JsonType)
    # Bézier path the polygon was drawn as (same pixels); NULL for plain polygons.
    curve: Mapped[dict[str, Any] | None] = mapped_column(NullableJsonType, nullable=True)
    # Who drew it; null for annotations drawn before this was recorded and once that account is deleted.
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cell_type: Mapped["CellType | None"] = relationship(back_populates="hotspot_annotations")


# Loaded with every hotspot (a correlated subquery), so each read shows how much is already annotated.
Hotspot.annotation_count = column_property(
    select(func.count(HotspotAnnotation.id))
    .where(HotspotAnnotation.hotspot_id == Hotspot.id)
    .correlate_except(HotspotAnnotation)
    .scalar_subquery()
)


class HotspotBatchStatus(enum.StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


# Must stay >= the longest HotspotBatchStatus value.
HOTSPOT_BATCH_STATUS_MAX_LENGTH = 16


class HotspotBatch(Base):
    """Fixed-size squares of a slide being cut out into hotspots in the background.

    The id comes from the client, so a retried request finds the batch it already started.
    """

    __tablename__ = "hotspot_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    slide_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("slides.id", ondelete="CASCADE"), index=True)
    slide_name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspot_folders.id", ondelete="CASCADE"), index=True)
    name_prefix: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    # Level-0 [x, y] top-left corner of each box to cut out, in order.
    boxes: Mapped[list[list[int]]] = mapped_column(JsonType)
    done: Mapped[int] = mapped_column(default=0)
    status: Mapped[HotspotBatchStatus] = mapped_column(
        Enum(
            HotspotBatchStatus,
            name="hotspot_batch_status",
            native_enum=False,
            create_constraint=True,
            length=HOTSPOT_BATCH_STATUS_MAX_LENGTH,
            values_callable=lambda statuses: [item.value for item in statuses],
        ),
        default=HotspotBatchStatus.RUNNING,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    folder: Mapped[HotspotFolder] = relationship(lazy="joined")
