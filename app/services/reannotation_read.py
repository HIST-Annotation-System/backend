"""A hotspot's reannotation as it is shown: the summary listed with every hotspot, and the full agreement."""

from app.models.account import Account
from app.models.hotspot import Hotspot
from app.models.organization import AgreementMetric
from app.models.reannotation import HotspotAgreement, HotspotReannotation, ReannotationStatus
from app.schemas.reannotation import AgreementRead, AnnotatorFiguresRead, ParticipantRead, ReannotationSummary

# A hotspot is fully reannotated once at least this many annotators have confirmed and agree enough.
MIN_CONFIRMED = 3


def full_name_of(account: Account) -> str | None:
    profile = account.profile
    name = " ".join(part for part in (profile.first_name, profile.last_name) if part) if profile else ""
    return name or None


def metric_value(agreement: HotspotAgreement) -> float | None:
    return agreement.kappa if agreement.metric == AgreementMetric.KAPPA else agreement.iou


def confirmed_of(hotspot: Hotspot) -> list[HotspotReannotation]:
    return [item for item in hotspot.reannotations if item.status == ReannotationStatus.CONFIRMED]


def summary_of(hotspot: Hotspot, viewer: Account | None) -> ReannotationSummary:
    mine = next((item for item in hotspot.reannotations if viewer is not None and item.account_id == viewer.id), None)
    agreement = hotspot.agreement
    return ReannotationSummary(
        layered=hotspot.layered,
        my_status=mine.status if mine else None,
        started_count=len(hotspot.reannotations),
        confirmed_count=len(confirmed_of(hotspot)),
        metric=agreement.metric if agreement else None,
        threshold=agreement.threshold if agreement else None,
        kappa=agreement.kappa if agreement else None,
        iou=agreement.iou if agreement else None,
    )


def agreement_read(hotspot: Hotspot, viewer: Account | None, per_annotator: bool) -> AgreementRead:
    """The full agreement; `per_annotator` adds each confirmed annotator's figures and changes."""
    participants = sorted(hotspot.reannotations, key=lambda item: item.started_at)
    names = {item.account_id: (item.account.username, full_name_of(item.account)) for item in participants}
    annotators = None
    if per_annotator:
        stored = hotspot.agreement.per_annotator if hotspot.agreement else {}
        annotators = [
            AnnotatorFiguresRead(
                account_id=int(account_id),
                username=names[int(account_id)][0],
                full_name=names[int(account_id)][1],
                **figures,
            )
            for account_id, figures in stored.items()
            if int(account_id) in names
        ]
    return AgreementRead(
        **summary_of(hotspot, viewer).model_dump(),
        min_confirmed=MIN_CONFIRMED,
        ready=hotspot.reannotated_at is not None,
        participants=[
            ParticipantRead(
                account_id=item.account_id,
                username=item.account.username,
                full_name=full_name_of(item.account),
                status=item.status,
                confirmed_at=item.confirmed_at,
            )
            for item in participants
        ],
        annotators=annotators,
    )
