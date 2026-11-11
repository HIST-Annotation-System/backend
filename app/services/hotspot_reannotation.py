"""Reannotation of a hotspot by several annotators, each in a layer of their own.

An annotator starts (their layer is a copy of the model's cells), edits their layer, and confirms it. Once enough have
confirmed, their agreement is worked out (see `agreement`), and the hotspot counts as fully reannotated while it
reaches the organization's threshold. The verdict is never set by hand: every confirmation, withdrawn confirmation and
change of the settings sets it again.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.account import Account
from app.models.hotspot import AnnotationKind, AnnotationSource, Hotspot, HotspotAnnotation, SegmentationStatus
from app.models.organization import Organization
from app.models.reannotation import HotspotAgreement, HotspotReannotation, ReannotationStatus
from app.repositories.hotspots import HotspotRepository
from app.repositories.organizations import OrganizationRepository
from app.schemas.reannotation import AgreementRead, AgreementSettings
from app.services.agreement import Cell, compute, polygon_of
from app.services.hotspot_ratings import RATING_MANAGER_ROLES
from app.services.hotspots import require_hotspot
from app.services.reannotation_read import MIN_CONFIRMED, agreement_read, confirmed_of, metric_value

NOT_LAYERED = "This hotspot was annotated before layers existed; it is marked reannotated by hand."
SEGMENTATION_PENDING = "The model is still segmenting this hotspot; start once its cells have arrived."
NOT_STARTED = "You have not started reannotating this hotspot."
ORGANIZATION_NOT_FOUND = "Organization {organization_id} does not exist."


def _cell(annotation: HotspotAnnotation) -> Cell:
    return Cell(
        annotation.id,
        annotation.cell_type_id or 0,
        polygon_of(annotation.geometry),
        annotation.origin_id,
    )


class HotspotReannotationService:
    def __init__(self, hotspots: HotspotRepository, organizations: OrganizationRepository) -> None:
        self._hotspots = hotspots
        self._organizations = organizations

    def get(self, hotspot_id: uuid.UUID, viewer: Account) -> AgreementRead:
        return self._read(require_hotspot(self._hotspots, hotspot_id), viewer)

    def start(self, hotspot_id: uuid.UUID, user: Account) -> AgreementRead:
        """Gives `user` a layer of their own, copied from the model's cells (nothing happens if they have one)."""
        hotspot = self._require_layered(hotspot_id)
        if self._own(hotspot, user) is not None:
            return self._read(hotspot, user)
        if hotspot.segmentation_status == SegmentationStatus.PENDING:
            raise ConflictError(SEGMENTATION_PENDING)
        copies = [
            HotspotAnnotation(
                id=uuid.uuid4(),
                hotspot_id=hotspot.id,
                kind=AnnotationKind.CELL,
                source=AnnotationSource.HUMAN,
                model_version=cell.model_version,
                cell_type_id=cell.cell_type_id,
                geometry=cell.geometry,
                curve=cell.curve,
                created_by_id=user.id,
                origin_id=cell.id,
            )
            for cell in self._model_cells(hotspot.id)
        ]
        hotspot.reannotations.append(HotspotReannotation(account=user))
        try:
            self._hotspots.save_all([hotspot, *copies])
        except IntegrityError:
            # A second request of the same user started it first.
            hotspot.reannotations = [item for item in hotspot.reannotations if item.account_id != user.id]
        return self._read(hotspot, user)

    def confirm(self, hotspot_id: uuid.UUID, user: Account) -> AgreementRead:
        return self._set_status(hotspot_id, user, ReannotationStatus.CONFIRMED)

    def unconfirm(self, hotspot_id: uuid.UUID, user: Account) -> AgreementRead:
        return self._set_status(hotspot_id, user, ReannotationStatus.DRAFT)

    def get_settings(self, organization_id: int) -> AgreementSettings:
        organization = self._require_organization(organization_id)
        return AgreementSettings(metric=organization.agreement_metric, threshold=organization.agreement_threshold)

    def update_settings(self, organization_id: int, settings: AgreementSettings) -> AgreementSettings:
        """Judges the organization's hotspots again by the new settings, from the figures already worked out."""
        organization = self._require_organization(organization_id)
        organization.agreement_metric, organization.agreement_threshold = settings.metric, settings.threshold
        hotspots = list(self._hotspots.list_judged(organization_id))
        for hotspot in hotspots:
            agreement = hotspot.agreement
            if agreement is not None:
                agreement.metric, agreement.threshold = settings.metric, settings.threshold
                self._apply(hotspot, (metric_value(agreement) or 0) >= settings.threshold, None)
        self._hotspots.save_all(hotspots)
        self._organizations.save(organization)
        return settings

    def _set_status(self, hotspot_id: uuid.UUID, user: Account, status: ReannotationStatus) -> AgreementRead:
        hotspot = self._require_layered(hotspot_id)
        own = self._own(hotspot, user)
        if own is None:
            raise ConflictError(NOT_STARTED)
        own.status = status
        own.confirmed_at = datetime.now(UTC) if status == ReannotationStatus.CONFIRMED else None
        self._judge(hotspot, user)
        self._hotspots.save_all([hotspot])
        return self._read(hotspot, user)

    def _judge(self, hotspot: Hotspot, actor: Account) -> None:
        """Works out the agreement of those who confirmed, and with it whether the hotspot is fully reannotated."""
        confirmed = confirmed_of(hotspot)
        if len(confirmed) < MIN_CONFIRMED:
            hotspot.agreement = None
            self._apply(hotspot, False, actor)
            return
        annotations = [item for item in self._hotspots.list_annotations(hotspot.id) if item.kind == AnnotationKind.CELL]
        layers: dict[int, list[Cell]] = {item.account_id: [] for item in confirmed}
        for annotation in annotations:
            if annotation.created_by_id in layers:
                layers[annotation.created_by_id].append(_cell(annotation))
        result = compute([_cell(item) for item in annotations if item.source == AnnotationSource.MODEL], layers)
        organization = self._organization_of(hotspot)
        agreement = hotspot.agreement or HotspotAgreement(hotspot_id=hotspot.id)
        agreement.kappa, agreement.iou = result.kappa, result.iou
        agreement.per_annotator = {
            str(account_id): {
                "kappa_without": figures.kappa_without,
                "iou_without": figures.iou_without,
                "iou_with_rest": figures.iou_with_rest,
                "changes": figures.changes,
            }
            for account_id, figures in result.annotators.items()
        }
        agreement.metric, agreement.threshold = organization.agreement_metric, organization.agreement_threshold
        hotspot.agreement = agreement
        self._apply(hotspot, (metric_value(agreement) or 0) >= agreement.threshold, actor)

    @staticmethod
    def _apply(hotspot: Hotspot, ready: bool, actor: Account | None) -> None:
        """Marks the hotspot fully reannotated (by whoever's confirmation made it so) or takes the mark off."""
        if not ready:
            hotspot.reannotated_at, hotspot.reannotated_by_id = None, None
        elif hotspot.reannotated_at is None:
            hotspot.reannotated_at, hotspot.reannotated_by_id = datetime.now(UTC), actor.id if actor else None

    def _read(self, hotspot: Hotspot, viewer: Account) -> AgreementRead:
        return agreement_read(hotspot, viewer, per_annotator=viewer.role in RATING_MANAGER_ROLES)

    def _model_cells(self, hotspot_id: uuid.UUID) -> Sequence[HotspotAnnotation]:
        return [
            item
            for item in self._hotspots.list_annotations(hotspot_id)
            if item.source == AnnotationSource.MODEL and item.kind == AnnotationKind.CELL
        ]

    @staticmethod
    def _own(hotspot: Hotspot, user: Account) -> HotspotReannotation | None:
        return next((item for item in hotspot.reannotations if item.account_id == user.id), None)

    def _require_layered(self, hotspot_id: uuid.UUID) -> Hotspot:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        if not hotspot.layered:
            raise InvalidInputError(NOT_LAYERED)
        return hotspot

    def _organization_of(self, hotspot: Hotspot) -> Organization:
        folder = self._hotspots.get_folder(hotspot.folder_id)
        return self._require_organization(folder.organization_id if folder else 0)

    def _require_organization(self, organization_id: int) -> Organization:
        organization = self._organizations.get(organization_id)
        if organization is None:
            raise NotFoundError(ORGANIZATION_NOT_FOUND.format(organization_id=organization_id))
        return organization
