import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, CellType, Hotspot, HotspotAnnotation, HotspotFolder, Organization
from app.models.cell_type import UNCLASSIFIED_CELL_TYPE_NAME
from app.models.hotspot import AnnotationKind, AnnotationSource, SegmentationStatus
from app.repositories.hotspots import HotspotRepository
from app.services.segmentation import (
    RESCORES_IN_FLIGHT,
    RESULT_UNREADABLE,
    SCHEMA_VERSION,
    SEGMENTATION_RESEND_AFTER,
    STATUS_DONE,
    STATUS_FAILED,
    Cell,
    SegmentationDispatcher,
    SegmentationResult,
    SegmentationResultService,
)

RESULT_KEY = "result.json"
MODEL_VERSION = "classpose-test/conic"


def ring(x: float) -> list[tuple[float, float]]:
    return [(x, 0), (x + 10, 0), (x + 10, 10), (x, 10)]


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]


class World:
    """A pending hotspot, and the service that saves what the worker reports about it."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.storage = FakeStorage()
        self.service = SegmentationResultService(factory, self.storage)  # type: ignore[arg-type]
        with factory() as session:
            organization = Organization(name="Org")
            session.add(organization)
            session.flush()
            folder = HotspotFolder(name="F", organization_id=organization.id)
            session.add(folder)
            session.flush()
            hotspot = Hotspot(id=uuid.uuid4(), folder_id=folder.id, name="H", image_width=100, image_height=100, mpp=0.25)
            session.add(hotspot)
            session.commit()
            self.hotspot_id = hotspot.id

    def save(self, cells: list[Cell], model_version: str = MODEL_VERSION) -> None:
        result = SegmentationResult(
            schema_version=SCHEMA_VERSION,
            hotspot_id=str(self.hotspot_id),
            model_version=model_version,
            image_width=100,
            image_height=100,
            cells=cells,
        )
        self.storage.objects[RESULT_KEY] = result.model_dump_json().encode()
        self.service.save(SCHEMA_VERSION, self.hotspot_id, STATUS_DONE, model_version, RESULT_KEY)

    def rescore_folder(self) -> int:
        with self.factory() as session:
            hotspot = session.get_one(Hotspot, self.hotspot_id)
            return HotspotRepository(session).request_rescores(hotspot.folder_id)

    def cell_type_names(self) -> list[str]:
        """The cell type of each saved cell, left to right."""
        with self.factory() as session:
            cells = session.scalars(select(HotspotAnnotation)).all()
            assert all(cell.source == AnnotationSource.MODEL and cell.model_version == MODEL_VERSION for cell in cells)
            ordered = sorted(cells, key=lambda cell: cell.geometry["coordinates"][0][0][0])
            return [cell.cell_type.name for cell in ordered if cell.cell_type]

    def hotspot(self) -> Hotspot:
        with self.factory() as session:
            return session.get_one(Hotspot, self.hotspot_id)


@pytest.fixture
def world() -> Iterator[World]:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield World(sessionmaker(bind=engine, expire_on_commit=False))


def test_cells_get_the_models_cell_types(world: World) -> None:
    with world.factory() as session:
        session.add(CellType(name="lymphocyte"))
        session.commit()

    world.save(
        [
            Cell(ring=ring(0), cell_type="Lymphocyte"),
            Cell(ring=ring(20), cell_type="Epithelial"),
            Cell(ring=ring(40), cell_type="Epithelial"),
            Cell(ring=ring(60), cell_type=None),
        ]
    )

    # An existing type is reused whatever its letter case; missing ones are created once.
    assert world.cell_type_names() == ["lymphocyte", "Epithelial", "Epithelial", UNCLASSIFIED_CELL_TYPE_NAME]
    with world.factory() as session:
        assert len(session.scalars(select(CellType)).all()) == 3
    assert world.hotspot().segmentation_status == SegmentationStatus.DONE


def test_an_invalid_cell_is_dropped_without_its_cell_type(world: World) -> None:
    world.save([Cell(ring=[(0, 0), (5, 5)], cell_type="Neutrophil"), Cell(ring=ring(20), cell_type="Plasma cell")])

    assert world.cell_type_names() == ["Plasma cell"]
    with world.factory() as session:
        assert [cell_type.name for cell_type in session.scalars(select(CellType))] == ["Plasma cell"]


def test_a_result_in_the_old_format_fails_the_hotspot(world: World) -> None:
    world.storage.objects[RESULT_KEY] = (
        b'{"schema_version": 1, "hotspot_id": "x", "model_version": "m", "image_width": 100, "image_height": 100,'
        b' "cells": [[[0, 0], [10, 0], [10, 10]]]}'
    )

    world.service.save(SCHEMA_VERSION, world.hotspot_id, STATUS_DONE, MODEL_VERSION, RESULT_KEY)

    hotspot = world.hotspot()
    assert (hotspot.segmentation_status, hotspot.segmentation_error) == (SegmentationStatus.FAILED, RESULT_UNREADABLE)


def test_a_job_carries_the_resolution_of_the_image(world: World) -> None:
    sent: list[tuple[uuid.UUID, str, float | None]] = []

    def publish(hotspot_id: uuid.UUID, image_key: str, mpp: float | None) -> None:
        sent.append((hotspot_id, image_key, mpp))

    assert SegmentationDispatcher(world.factory, publish).dispatch_once() == 1

    ((hotspot_id, image_key, mpp),) = sent
    assert (hotspot_id, mpp) == (world.hotspot_id, 0.25)
    assert str(world.hotspot_id) in image_key


class Sent:
    """The jobs a dispatcher publishes, with whether each is a rescore."""

    def __init__(self) -> None:
        self.jobs: list[tuple[uuid.UUID, bool]] = []

    def __call__(self, hotspot_id: uuid.UUID, image_key: str, mpp: float | None, rescore: bool = False) -> None:
        self.jobs.append((hotspot_id, rescore))


def test_the_hotspot_keeps_the_models_mean_confidence(world: World) -> None:
    world.save(
        [
            Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.9, class_confidence=0.6),
            Cell(ring=ring(20), cell_type="Tumor", outline_confidence=0.7, class_confidence=0.8),
            # No type, so no class confidence: it is left out of that mean only.
            Cell(ring=ring(40), cell_type=None, outline_confidence=0.5, class_confidence=None),
        ]
    )

    hotspot = world.hotspot()
    assert hotspot.model_outline_confidence == pytest.approx(0.7)
    assert hotspot.model_class_confidence == pytest.approx(0.7)


def test_a_result_without_confidences_leaves_them_empty(world: World) -> None:
    world.save([Cell(ring=ring(0), cell_type="Tumor")])

    hotspot = world.hotspot()
    assert (hotspot.model_outline_confidence, hotspot.model_class_confidence) == (None, None)


def test_segmented_hotspots_are_not_rescored_unless_asked(world: World) -> None:
    world.save([Cell(ring=ring(0), cell_type="Tumor")])
    sent = Sent()

    assert SegmentationDispatcher(world.factory, sent).dispatch_once() == 0


def test_rescoring_a_folder_sends_its_segmented_hotspots_once(world: World) -> None:
    world.save([Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.9)])
    sent = Sent()
    dispatcher = SegmentationDispatcher(world.factory, sent)
    start = datetime.now(UTC)

    assert world.rescore_folder() == 1
    assert dispatcher.dispatch_once(start) == 1
    assert dispatcher.dispatch_once(start + SEGMENTATION_RESEND_AFTER / 2) == 0
    # Lost: sent once more after the wait.
    assert dispatcher.dispatch_once(start + SEGMENTATION_RESEND_AFTER * 2) == 1
    assert sent.jobs == [(world.hotspot_id, True), (world.hotspot_id, True)]


def test_a_rescore_replaces_the_confidence_and_leaves_the_cells_alone(world: World) -> None:
    world.save([Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.9, class_confidence=0.9)])
    world.rescore_folder()

    world.save([Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.8, class_confidence=0.4)])

    assert world.cell_type_names() == ["Tumor"]
    hotspot = world.hotspot()
    assert (hotspot.model_outline_confidence, hotspot.model_class_confidence) == (0.8, 0.4)
    assert (hotspot.confidence_pending, hotspot.confidence_error) == (False, None)
    # Over: a late duplicate changes nothing.
    world.save([Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.1, class_confidence=0.1)])
    assert world.hotspot().model_outline_confidence == 0.8


def test_a_rescore_by_another_model_keeps_the_old_confidence(world: World) -> None:
    world.save([Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.9)])
    world.rescore_folder()

    world.save([Cell(ring=ring(0), cell_type="Tumor", outline_confidence=0.2)], model_version="classpose-test/puma")

    hotspot = world.hotspot()
    assert hotspot.model_outline_confidence == 0.9
    assert hotspot.confidence_pending is False
    assert hotspot.confidence_error is not None and "classpose-test/puma" in hotspot.confidence_error


def test_a_failed_rescore_is_not_sent_again(world: World) -> None:
    world.save([Cell(ring=ring(0), cell_type="Tumor")])
    world.rescore_folder()
    dispatcher = SegmentationDispatcher(world.factory, Sent())
    start = datetime.now(UTC)
    dispatcher.dispatch_once(start)

    world.service.save(SCHEMA_VERSION, world.hotspot_id, STATUS_FAILED, error="Too large")

    hotspot = world.hotspot()
    assert (hotspot.segmentation_status, hotspot.confidence_error) == (SegmentationStatus.DONE, "Too large")
    assert dispatcher.dispatch_once(start + SEGMENTATION_RESEND_AFTER * 2) == 0


def test_only_a_few_rescores_wait_at_once(world: World) -> None:
    with world.factory() as session:
        folder_id = session.get_one(Hotspot, world.hotspot_id).folder_id
        for index in range(RESCORES_IN_FLIGHT + 5):
            hotspot = Hotspot(
                id=uuid.uuid4(),
                folder_id=folder_id,
                name=f"H{index}",
                image_width=100,
                image_height=100,
                segmentation_status=SegmentationStatus.DONE,
            )
            session.add(hotspot)
            session.add(
                HotspotAnnotation(
                    id=uuid.uuid4(),
                    hotspot_id=hotspot.id,
                    kind=AnnotationKind.CELL,
                    source=AnnotationSource.MODEL,
                    model_version=MODEL_VERSION,
                    geometry={"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]]},
                )
            )
        session.commit()
    sent = Sent()
    dispatcher = SegmentationDispatcher(world.factory, sent)
    start = datetime.now(UTC)

    # The world's own hotspot is still pending, so it is not rescored, only segmented.
    assert world.rescore_folder() == RESCORES_IN_FLIGHT + 5
    dispatcher.dispatch_once(start)
    dispatcher.dispatch_once(start)

    assert sum(1 for _, rescore in sent.jobs if rescore) == RESCORES_IN_FLIGHT
    assert [rescore for hotspot_id, rescore in sent.jobs if hotspot_id == world.hotspot_id] == [False]
