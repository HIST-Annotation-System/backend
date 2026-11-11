import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Float, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.account import Account
from app.models.base import Base, JsonType
from app.models.organization import AGREEMENT_METRIC_MAX_LENGTH, AgreementMetric


class ReannotationStatus(enum.StrEnum):
    """An annotator's own work on a hotspot: still being edited, or confirmed (and counted in the agreement)."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"


# Must stay >= the longest ReannotationStatus value.
REANNOTATION_STATUS_MAX_LENGTH = 16


class HotspotReannotation(Base):
    """One annotator's reannotation of a hotspot: their own layer of cells, started from the model's."""

    __tablename__ = "hotspot_reannotations"
    __table_args__ = (UniqueConstraint("hotspot_id", "account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    status: Mapped[ReannotationStatus] = mapped_column(
        Enum(
            ReannotationStatus,
            name="reannotation_status",
            native_enum=False,
            create_constraint=True,
            length=REANNOTATION_STATUS_MAX_LENGTH,
            values_callable=lambda statuses: [item.value for item in statuses],
        ),
        default=ReannotationStatus.DRAFT,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[Account] = relationship(lazy="joined")


class HotspotAgreement(Base):
    """How well a hotspot's confirmed annotators agree; rewritten whenever one confirms or takes it back, and gone while
    too few have confirmed. `metric` and `threshold` are what the verdict was last judged against (the organization's
    settings at that time).
    """

    __tablename__ = "hotspot_agreements"

    hotspot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hotspots.id", ondelete="CASCADE"), primary_key=True)
    kappa: Mapped[float | None] = mapped_column(Float)
    iou: Mapped[float | None] = mapped_column(Float)
    # Account id -> that annotator's figures and changes against the model (see app.services.agreement).
    per_annotator: Mapped[dict[str, Any]] = mapped_column(JsonType)
    metric: Mapped[AgreementMetric] = mapped_column(
        Enum(
            AgreementMetric,
            name="agreement_metric",
            native_enum=False,
            create_constraint=True,
            length=AGREEMENT_METRIC_MAX_LENGTH,
            values_callable=lambda metrics: [item.value for item in metrics],
        )
    )
    threshold: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
