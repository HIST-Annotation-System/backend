import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.organization import AgreementMetric
from app.models.reannotation import ReannotationStatus


class ReannotationSummary(BaseModel):
    """Where a hotspot's reannotation stands, as everyone sees it (also listed with every hotspot)."""

    # False: annotated in the old shared way, marked reannotated by hand.
    layered: bool = True
    # The viewer's own status; null until they start.
    my_status: ReannotationStatus | None = None
    started_count: int = 0
    confirmed_count: int = 0
    # What the figures were judged against; all null while too few annotators have confirmed.
    metric: AgreementMetric | None = None
    threshold: float | None = None
    kappa: float | None = None
    iou: float | None = None


class ParticipantRead(BaseModel):
    account_id: int
    username: str
    full_name: str | None
    status: ReannotationStatus
    confirmed_at: datetime | None


class AnnotatorFiguresRead(BaseModel):
    """One confirmed annotator: how the group does without them, and what they changed of the model's cells."""

    account_id: int
    username: str
    full_name: str | None
    kappa_without: float
    iou_without: float
    iou_with_rest: float
    # unchanged / reshaped / relabeled / added: ids of their own cells; removed: ids of the model's cells.
    changes: dict[str, list[uuid.UUID]]


class AgreementRead(ReannotationSummary):
    min_confirmed: int
    ready: bool
    participants: list[ParticipantRead]
    # Left out (null) for annotators: they see how the group does, not how each colleague annotated.
    annotators: list[AnnotatorFiguresRead] | None = None


class AgreementSettings(BaseModel):
    metric: AgreementMetric
    threshold: Annotated[float, Field(ge=0, le=1)]
