import enum

from sqlalchemy import Enum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Must stay >= the longest PriorityComponent value.
PRIORITY_COMPONENT_MAX_LENGTH = 32


class PriorityComponent(enum.StrEnum):
    """A measurement the estimated priority of an unrated hotspot is made of (see app.services.priority)."""

    CELL_DENSITY = "cell_density"
    HEMATOXYLIN = "hematoxylin"
    GLCM_CONTRAST = "glcm_contrast"
    EDGE_DENSITY = "edge_density"
    ENTROPY = "entropy"


class PriorityWeight(Base):
    """How much one component counts towards the estimated priority; set by the admin, 0 switches it off."""

    __tablename__ = "priority_weights"

    component: Mapped[PriorityComponent] = mapped_column(
        Enum(
            PriorityComponent,
            name="priority_component",
            native_enum=False,
            create_constraint=True,
            length=PRIORITY_COMPONENT_MAX_LENGTH,
            values_callable=lambda components: [item.value for item in components],
        ),
        primary_key=True,
    )
    weight: Mapped[float]
