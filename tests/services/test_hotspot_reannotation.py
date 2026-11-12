import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.exceptions.errors import ConflictError, ForbiddenError, InvalidInputError
from app.models import Account, Base, CellType, Hotspot, HotspotAnnotation, HotspotFolder, Organization, Role
from app.models.hotspot import AnnotationKind, AnnotationSource, SegmentationStatus
from app.models.organization import AgreementMetric
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.repositories.organizations import OrganizationRepository
from app.schemas.annotations import AnnotationCreate, AnnotationUpdate, PolygonGeometry
from app.schemas.reannotation import AgreementSettings
from app.services.hotspot_annotations import HotspotAnnotationService
from app.services.hotspot_reannotation import HotspotReannotationService


def square(x: float) -> dict[str, object]:
    ring = [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10], [x, 0]]
    return {"type": "Polygon", "coordinates": [ring]}


class World:
    """An organization with a hotspot the model has segmented into four cells, and annotators to work on it."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.organization = Organization(name="Org")
        session.add(self.organization)
        session.flush()
        self.types = [CellType(name="A"), CellType(name="B")]
        session.add_all(self.types)
        folder = HotspotFolder(name="F", organization_id=self.organization.id)
        session.add(folder)
        session.flush()
        self.hotspot = Hotspot(
            id=uuid.uuid4(),
            folder_id=folder.id,
            name="H",
            image_width=1000,
            image_height=1000,
            segmentation_status=SegmentationStatus.DONE,
        )
        session.add(self.hotspot)
        self.model_cells = [
            HotspotAnnotation(
                id=uuid.uuid4(),
                hotspot_id=self.hotspot.id,
                kind=AnnotationKind.CELL,
                source=AnnotationSource.MODEL,
                cell_type_id=self.types[0].id,
                geometry=square(index * 20),
            )
            for index in range(4)
        ]
        session.add_all(self.model_cells)
        self.users = [self.user(f"user{index}", Role.ANNOTATOR) for index in range(4)]
        self.admin = self.user("boss", Role.ORG_ADMIN)
        session.commit()
        hotspots = HotspotRepository(session)
        self.reannotation = HotspotReannotationService(hotspots, OrganizationRepository(session))
        self.annotations = HotspotAnnotationService(hotspots, CellTypeRepository(session))

    def user(self, name: str, role: Role) -> Account:
        account = Account(username=name, hashed_password="x", role=role, organization_id=self.organization.id)
        self.session.add(account)
        self.session.flush()
        return account

    def reannotated(self) -> bool:
        return self.reannotation.get(self.hotspot.id, self.admin).ready

    def finish(self, user: Account, relabel: int | None = None) -> None:
        """`user` starts and confirms; with `relabel`, they first give that many of their cells the other type."""
        self.reannotation.start(self.hotspot.id, user)
        mine = [item for item in self.annotations.list_annotations(self.hotspot.id, user) if item.source != "model"]
        for item in mine[: relabel or 0]:
            self.annotations.update_annotation(
                self.hotspot.id, item.id, AnnotationUpdate(cell_type_id=self.types[1].id), user
            )
        self.reannotation.confirm(self.hotspot.id, user)


@pytest.fixture
def world() -> Iterator[World]:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield World(session)


def test_start_copies_the_model_cells_into_the_users_own_layer(world: World) -> None:
    first, second = world.users[:2]
    world.reannotation.start(world.hotspot.id, first)
    world.reannotation.start(world.hotspot.id, first)  # starting again changes nothing
    world.reannotation.start(world.hotspot.id, second)

    own = world.annotations.list_annotations(world.hotspot.id, first)
    assert [item.source for item in own].count(AnnotationSource.MODEL) == 4
    copies = [item for item in own if item.source == AnnotationSource.HUMAN]
    assert len(copies) == 4
    assert {item.created_by_name for item in copies} == {first.username}
    # The model's cells stay as they were, and the other annotator's copies stay out of sight.
    assert all(item.created_by_id is None for item in world.model_cells)
    # An administrator gets the model's cells once, not again with each annotator's unchanged copy of them.
    everything = world.annotations.list_annotations(world.hotspot.id, world.admin)
    assert len(everything) == 4


def test_start_waits_for_the_model(world: World) -> None:
    world.hotspot.segmentation_status = SegmentationStatus.PENDING
    with pytest.raises(ConflictError):
        world.reannotation.start(world.hotspot.id, world.users[0])


def test_three_agreeing_annotators_make_the_hotspot_fully_reannotated(world: World) -> None:
    for user in world.users[:2]:
        world.finish(user)
        assert not world.reannotated()
    world.finish(world.users[2])

    agreement = world.reannotation.get(world.hotspot.id, world.users[0])
    assert world.reannotated()
    assert agreement.confirmed_count == 3
    assert (agreement.kappa, agreement.iou) == (1.0, 1.0)
    assert agreement.annotators is None  # annotators see the group, not each colleague
    assert world.hotspot.reannotated_by_id == world.users[2].id


def test_taking_a_confirmation_back_withdraws_the_verdict(world: World) -> None:
    for user in world.users[:3]:
        world.finish(user)
    assert world.reannotated()

    agreement = world.reannotation.unconfirm(world.hotspot.id, world.users[0])
    assert not agreement.ready
    assert agreement.confirmed_count == 2
    assert agreement.kappa is None
    assert world.hotspot.reannotated_at is None


def test_changing_the_metric_or_threshold_judges_the_hotspot_again(world: World) -> None:
    world.finish(world.users[0])
    world.finish(world.users[1])
    world.finish(world.users[2], relabel=4)
    agreement = world.reannotation.get(world.hotspot.id, world.admin)
    assert agreement.kappa is not None and agreement.kappa < 0.6
    assert not agreement.ready
    # The administrator sees who pulls the figure down.
    assert agreement.annotators is not None
    outlier = next(item for item in agreement.annotators if item.account_id == world.users[2].id)
    assert outlier.kappa_without == 1.0
    assert len(outlier.changes["relabeled"]) == 4

    # The outlines were not touched, so on IoU the same annotators agree perfectly.
    world.reannotation.update_settings(world.organization.id, AgreementSettings(metric=AgreementMetric.IOU, threshold=0.9))
    assert world.reannotated()
    world.reannotation.update_settings(world.organization.id, AgreementSettings(metric=AgreementMetric.KAPPA, threshold=0.6))
    assert not world.reannotated()


def test_a_fourth_annotator_counts_too(world: World) -> None:
    for user in world.users[:3]:
        world.finish(user)
    world.finish(world.users[3], relabel=4)
    assert not world.reannotated()


def test_only_the_author_edits_their_own_unconfirmed_layer(world: World) -> None:
    first, second = world.users[:2]
    new = AnnotationCreate(
        kind=AnnotationKind.CELL,
        cell_type_id=world.types[0].id,
        geometry=PolygonGeometry(coordinates=[[(500, 500), (520, 500), (520, 520), (500, 500)]]),
    )
    with pytest.raises(ForbiddenError):  # not started yet
        world.annotations.create_annotation(world.hotspot.id, new, first)
    world.reannotation.start(world.hotspot.id, first)
    world.reannotation.start(world.hotspot.id, second)
    own = world.annotations.create_annotation(world.hotspot.id, new, first)

    change = AnnotationUpdate(cell_type_id=world.types[1].id)
    with pytest.raises(ForbiddenError):  # the model's cell
        world.annotations.update_annotation(world.hotspot.id, world.model_cells[0].id, change, first)
    with pytest.raises(ForbiddenError):  # a colleague's cell
        world.annotations.update_annotation(world.hotspot.id, own.id, change, second)
    with pytest.raises(ForbiddenError):
        world.annotations.delete_annotation(world.hotspot.id, own.id, second)

    world.annotations.update_annotation(world.hotspot.id, own.id, change, first)
    world.reannotation.confirm(world.hotspot.id, first)
    with pytest.raises(ForbiddenError):  # confirmed means locked until taken back
        world.annotations.delete_annotation(world.hotspot.id, own.id, first)
    world.reannotation.unconfirm(world.hotspot.id, first)
    world.annotations.delete_annotation(world.hotspot.id, own.id, first)


def test_hotspots_annotated_the_old_way_keep_their_manual_mark(world: World) -> None:
    world.hotspot.layered = False
    with pytest.raises(InvalidInputError):
        world.reannotation.start(world.hotspot.id, world.users[0])
    # Everyone still sees and edits the one shared pool.
    world.annotations.update_annotation(
        world.hotspot.id, world.model_cells[0].id, AnnotationUpdate(cell_type_id=world.types[1].id), world.users[0]
    )
    assert len(world.annotations.list_annotations(world.hotspot.id, world.users[1])) == 4


def test_a_copy_of_a_model_cell_is_untouched_until_its_author_changes_it(world: World) -> None:
    first, second = world.users[:2]
    world.reannotation.start(world.hotspot.id, first)
    world.reannotation.start(world.hotspot.id, second)
    copies = [item for item in world.annotations.list_annotations(world.hotspot.id, first) if item.source != "model"]
    assert all(item.untouched for item in copies)

    retyped = world.annotations.update_annotation(
        world.hotspot.id, copies[0].id, AnnotationUpdate(cell_type_id=world.types[1].id), first
    )
    moved = world.annotations.update_annotation(
        world.hotspot.id, copies[1].id, AnnotationUpdate(geometry=PolygonGeometry.model_validate(square(500))), first
    )
    drawn = world.annotations.create_annotation(
        world.hotspot.id,
        AnnotationCreate(cell_type_id=world.types[0].id, geometry=PolygonGeometry.model_validate(square(700))),
        first,
    )

    assert not (retyped.untouched or moved.untouched or drawn.untouched)
    own = [item for item in world.annotations.list_annotations(world.hotspot.id, first) if item.source != "model"]
    assert sorted(item.id for item in own if not item.untouched) == sorted([retyped.id, moved.id, drawn.id])
    assert len([item for item in own if item.untouched]) == 2
    # An administrator sees what each annotator changed, not everyone's unchanged copies of the model's cells.
    everything = world.annotations.list_annotations(world.hotspot.id, world.admin)
    assert len([item for item in everything if item.source != "model"]) == 3
    assert not any(item.untouched for item in everything)


def test_confirming_a_copy_of_a_model_cell_counts_as_dealing_with_it(world: World) -> None:
    user = world.users[0]
    world.reannotation.start(world.hotspot.id, user)
    copy = next(item for item in world.annotations.list_annotations(world.hotspot.id, user) if item.untouched)
    assert copy.origin_id in {cell.id for cell in world.model_cells}

    confirmed = world.annotations.update_annotation(world.hotspot.id, copy.id, AnnotationUpdate(confirmed=True), user)

    assert not confirmed.untouched
    assert (confirmed.geometry, confirmed.cell_type_id) == (copy.geometry, copy.cell_type_id)
    own = [item for item in world.annotations.list_annotations(world.hotspot.id, user) if item.source != "model"]
    assert [item.id for item in own if not item.untouched] == [copy.id]
    # Someone else now sees it as that annotator's work.
    everything = world.annotations.list_annotations(world.hotspot.id, world.admin)
    assert [item.id for item in everything if item.source != "model"] == [copy.id]
