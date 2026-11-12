import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, CellType, Hotspot, HotspotAnnotation, HotspotFolder, Organization
from app.models.cell_type import UNCLASSIFIED_CELL_TYPE_NAME
from app.models.hotspot import AnnotationSource, SegmentationStatus
from app.services.segmentation import (
    RESULT_UNREADABLE,
    SCHEMA_VERSION,
    STATUS_DONE,
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

    def save(self, cells: list[Cell]) -> None:
        result = SegmentationResult(
            schema_version=SCHEMA_VERSION,
            hotspot_id=str(self.hotspot_id),
            model_version=MODEL_VERSION,
            image_width=100,
            image_height=100,
            cells=cells,
        )
        self.storage.objects[RESULT_KEY] = result.model_dump_json().encode()
        self.service.save(SCHEMA_VERSION, self.hotspot_id, STATUS_DONE, MODEL_VERSION, RESULT_KEY)

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
