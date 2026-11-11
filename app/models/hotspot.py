import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func, select, true
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.cell_type import CellType
    from app.models.reannotation import HotspotAgreement, HotspotReannotation
    from app.models.tag import HotspotTag, Tag

from app.core.constants import NAME_MAX_LENGTH
from app.models.base import Base, JsonType, NullableJsonType
from app.models.tag import TagDimension


class HotspotFolder(Base):
    """A named folder of the hotspot catalog (flat: folders do not nest)."""

    __tablename__ = "hotspot_folders"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    # Hotspots belong to the organization of their folder.
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
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


class SegmentationStatus(enum.StrEnum):
    """Where the model's run on a hotspot stands. It only ever moves out of `pending`.

    `skipped` means someone started annotating by hand first, so model cells would land on top of their work.
    """

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


# Must stay >= the longest SegmentationStatus value.
SEGMENTATION_STATUS_MAX_LENGTH = 16


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
    # Reannotated one layer per annotator, with the verdict derived from their agreement (see
    # app.services.hotspot_reannotation). False for hotspots annotated in the old shared way: they keep their pool and
    # their manual mark.
    layered: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    segmentation_status: Mapped[SegmentationStatus] = mapped_column(
        Enum(
            SegmentationStatus,
            name="segmentation_status",
            native_enum=False,
            create_constraint=True,
            length=SEGMENTATION_STATUS_MAX_LENGTH,
            values_callable=lambda statuses: [item.value for item in statuses],
        ),
        default=SegmentationStatus.PENDING,
        server_default=SegmentationStatus.PENDING.value,
        index=True,
    )
    segmentation_error: Mapped[str | None] = mapped_column(Text)
    # When a stack last sent the job to its worker; null until one has. See app.services.segmentation.
    segmentation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tag_links: Mapped[list["HotspotTag"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )
    reannotations: Mapped[list["HotspotReannotation"]] = relationship(
        lazy="selectin", cascade="all, delete-orphan", passive_deletes=True
    )
    agreement: Mapped["HotspotAgreement | None"] = relationship(
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


class AnnotationKind(enum.StrEnum):
    """A cell outline, or an area left out of model training (blur, fold, dust, slide edge)."""

    CELL = "cell"
    EXCLUDED = "excluded"


# Must stay >= the longest AnnotationKind value.
ANNOTATION_KIND_MAX_LENGTH = 16


class AnnotationSource(enum.StrEnum):
    """`model` is an outline as the model produced it, a starting point for the annotator; editing it makes it `human`."""

    HUMAN = "human"
    MODEL = "model"


# Must stay >= the longest AnnotationSource value.
ANNOTATION_SOURCE_MAX_LENGTH = 16
MODEL_VERSION_MAX_LENGTH = 128


class HotspotAnnotation(Base):
    __tablename__ = "hotspot_annotations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), index=True)
    cell_type_id: Mapped[int | None] = mapped_column(ForeignKey("cell_types.id", ondelete="RESTRICT"), index=True)
    # Excluded areas have no cell type.
    kind: Mapped[AnnotationKind] = mapped_column(
        Enum(
            AnnotationKind,
            name="annotation_kind",
            native_enum=False,
            create_constraint=True,
            length=ANNOTATION_KIND_MAX_LENGTH,
            values_callable=lambda kinds: [item.value for item in kinds],
        ),
        default=AnnotationKind.CELL,
        server_default=AnnotationKind.CELL.value,
    )
    source: Mapped[AnnotationSource] = mapped_column(
        Enum(
            AnnotationSource,
            name="annotation_source",
            native_enum=False,
            create_constraint=True,
            length=ANNOTATION_SOURCE_MAX_LENGTH,
            values_callable=lambda sources: [item.value for item in sources],
        ),
        default=AnnotationSource.HUMAN,
        server_default=AnnotationSource.HUMAN.value,
    )
    # The model that drew the outline; kept after a person edits it.
    model_version: Mapped[str | None] = mapped_column(String(MODEL_VERSION_MAX_LENGTH))
    # GeoJSON Polygon in hotspot image pixels (closed exterior ring).
    geometry: Mapped[dict[str, Any]] = mapped_column(JsonType)
    # Bézier path the polygon was drawn as (same pixels); NULL for plain polygons.
    curve: Mapped[dict[str, Any] | None] = mapped_column(NullableJsonType, nullable=True)
    # The model cell this one was copied from when its author started reannotating; null for the model's own cells
    # and for shapes drawn from scratch.
    origin_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hotspot_annotations.id", ondelete="SET NULL"), index=True
    )
    # Who drew or last changed it; null for untouched model cells, annotations drawn before this was recorded, and
    # once that account is deleted.
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    cell_type: Mapped["CellType | None"] = relationship(back_populates="hotspot_annotations")
    created_by: Mapped["Account | None"] = relationship(lazy="joined")

    @property
    def created_by_name(self) -> str | None:
        return self.created_by.username if self.created_by else None


# Loaded with every hotspot (a correlated subquery), so each read shows how many cells people have annotated.
Hotspot.annotation_count = column_property(
    select(func.count(HotspotAnnotation.id))
    .where(
        HotspotAnnotation.hotspot_id == Hotspot.id,
        HotspotAnnotation.kind == AnnotationKind.CELL,
        HotspotAnnotation.source == AnnotationSource.HUMAN,
    )
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
