import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import NAME_MAX_LENGTH
from app.models.base import Base


class AgreementMetric(enum.StrEnum):
    """What an organization's threshold applies to."""

    KAPPA = "kappa"
    IOU = "iou"


# Must stay >= the longest AgreementMetric value.
AGREEMENT_METRIC_MAX_LENGTH = 8
DEFAULT_AGREEMENT_THRESHOLD = 0.6


class Organization(Base):
    """A team that owns its slides and hotspot folders; its members see nothing of other organizations."""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # What a hotspot's annotators have to agree on, and how much, before it counts as fully reannotated.
    agreement_metric: Mapped[AgreementMetric] = mapped_column(
        Enum(
            AgreementMetric,
            name="agreement_metric",
            native_enum=False,
            create_constraint=True,
            length=AGREEMENT_METRIC_MAX_LENGTH,
            values_callable=lambda metrics: [item.value for item in metrics],
        ),
        default=AgreementMetric.KAPPA,
        server_default=AgreementMetric.KAPPA.value,
    )
    agreement_threshold: Mapped[float] = mapped_column(
        Float, default=DEFAULT_AGREEMENT_THRESHOLD, server_default=str(DEFAULT_AGREEMENT_THRESHOLD)
    )
