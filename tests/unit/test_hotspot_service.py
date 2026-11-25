import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models import Hotspot, Role
from app.models.hotspot import HotspotBatch, HotspotBatchStatus, HotspotFeature, SegmentationStatus
from app.models.organization import AgreementMetric
from app.models.reannotation import HotspotAgreement
from app.models.slide import Slide, SlideStatus
from app.models.tag import TagDimension
from app.repositories.hotspots import HotspotRepository
from app.repositories.priority_weights import PriorityWeightRepository
from app.repositories.slides import SlideRepository
from app.schemas.hotspots import HotspotBatchCreate, HotspotSort, HotspotStatus, HotspotUpdate, SortOrder
from app.services.hotspot_reader import hotspot_status
from app.services.hotspots import (
    BATCH_INTERRUPTED,
    HOTSPOT_BOX_SIZE,
    HOTSPOT_THUMBNAIL_KEY,
    HotspotService,
    fail_interrupted_batches,
)
from app.services.reannotation_read import full_name_of, metric_value
from conftest import Factory, MemoryStorage

ALL_FEATURES = {feature.value: 0.0 for feature in HotspotFeature}


class FakeSlideService:
    def __init__(self) -> None:
        self.reads: list[tuple[int, int]] = []
        self.fail_at: tuple[int, int] | None = None

    def read_square(self, slide_id: uuid.UUID, x: int, y: int, size: int, max_size: int) -> Image.Image:
        if (x, y) == self.fail_at:
            raise RuntimeError("disk on fire")
        self.reads.append((x, y))
        return Image.new("RGB", (min(size, max_size), min(size, max_size)), (150, 80, 160))


class World:
    def __init__(self, session: Session, factory: Factory, storage: MemoryStorage) -> None:
        self.session = session
        self.factory = factory
        self.storage = storage
        self.org = factory.organization("Org")
        self.folder = factory.folder(self.org, "Main")
        self.slides = FakeSlideService()
        self.service = self.scoped(None)

    def scoped(self, organization_id: int | None) -> HotspotService:
        return HotspotService(
            HotspotRepository(self.session, organization_id),
            SlideRepository(self.session, organization_id),
            self.slides,  # type: ignore[arg-type]
            self.storage,  # type: ignore[arg-type]
            PriorityWeightRepository(self.session),
        )

    def hotspot(self, minutes_ago: int = 0, **fields: Any) -> Hotspot:
        fields.setdefault("features", dict(ALL_FEATURES))
        created = datetime(2026, 1, 1, tzinfo=UTC) - timedelta(minutes=minutes_ago)
        return self.factory.hotspot(self.folder, created_at=created, **fields)

    def slide(self, **fields: Any) -> Slide:
        values: dict[str, Any] = {
            "name": "S",
            "original_filename": "s.svs",
            "storage_key": f"slides/{uuid.uuid4()}/s.svs",
            "status": SlideStatus.READY,
            "width": 4096,
            "height": 2048,
            "mpp_x": 0.25,
            "organization_id": self.org.id,
            **fields,
        }
        slide = Slide(id=uuid.uuid4(), **values)
        self.session.add(slide)
        self.session.flush()
        return slide


@pytest.fixture
def world(session: Session, factory: Factory, storage: MemoryStorage) -> World:
    return World(session, factory, storage)


class TestFolders:
    def test_create_list_rename_delete(self, world: World) -> None:
        created = world.service.create_folder("Second", world.org.id)
        world.hotspot()
        assert [(f.name, f.hotspot_count) for f in world.service.list_folders()] == [("Main", 1), ("Second", 0)]
        assert world.service.rename_folder(created.id, "Third").name == "Third"
        world.service.delete_folder(created.id)
        assert [f.name for f in world.service.list_folders()] == ["Main"]

    def test_names_are_unique_per_organization(self, world: World) -> None:
        with pytest.raises(ConflictError):
            world.service.create_folder("Main", world.org.id)
        other = world.factory.organization()
        assert world.service.create_folder("Main", other.id).organization_id == other.id

    def test_rename_onto_a_taken_name(self, world: World) -> None:
        second = world.service.create_folder("Second", world.org.id)
        with pytest.raises(ConflictError):
            world.service.rename_folder(second.id, "Main")

    def test_deleting_a_folder_deletes_its_hotspots_images(self, world: World) -> None:
        hotspot = world.hotspot()
        world.service.delete_folder(world.folder.id)
        assert world.storage.deleted_prefixes == [f"hotspots/{hotspot.id}/"]

    def test_folders_of_other_organizations_are_invisible(self, world: World) -> None:
        other = world.factory.organization()
        scoped = world.scoped(other.id)
        assert scoped.list_folders() == []
        with pytest.raises(NotFoundError):
            scoped.rename_folder(world.folder.id, "X")


class TestListing:
    def test_newest_first_by_default_and_oldest_first_ascending(self, world: World) -> None:
        old, new = world.hotspot(10, name="old"), world.hotspot(0, name="new")
        assert [h.id for h in world.service.list_hotspots(None)] == [new.id, old.id]
        assert [h.id for h in world.service.list_hotspots(None, order=SortOrder.ASC)] == [old.id, new.id]

    def test_sort_by_a_feature_puts_hotspots_without_it_last(self, world: World) -> None:
        low = world.hotspot(3, features={**ALL_FEATURES, "entropy": 1.0})
        high = world.hotspot(2, features={**ALL_FEATURES, "entropy": 5.0})
        world.storage.objects.clear()
        unknown = world.hotspot(1, features={"glcm_contrast": 1.0})  # missing, thumbnail unreadable
        desc = world.service.list_hotspots(None, sort=HotspotSort.ENTROPY)
        asc = world.service.list_hotspots(None, sort=HotspotSort.ENTROPY, order=SortOrder.ASC)
        assert [h.id for h in desc] == [high.id, low.id, unknown.id]
        assert [h.id for h in asc] == [low.id, high.id, unknown.id]

    def test_missing_features_are_computed_from_the_thumbnail(self, world: World) -> None:
        hotspot = world.hotspot(features=None)
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), "white").save(buffer, format="JPEG")
        world.storage.objects[HOTSPOT_THUMBNAIL_KEY.format(hotspot_id=hotspot.id)] = buffer.getvalue()
        [read] = world.service.list_hotspots(None)
        assert read.features is not None and set(read.features) == set(ALL_FEATURES)

    def test_filter_by_status_and_tags(self, world: World) -> None:
        user = world.factory.account(Role.ADMIN)
        important = world.factory.tag("Important", 10, TagDimension.IMPORTANCE)
        right = world.factory.tag("Right", 5, TagDimension.ACCURACY)
        from app.repositories.tags import TagRepository
        from app.services.tags import TagService

        tagged, untagged = world.hotspot(1), world.hotspot(0)
        TagService(TagRepository(world.session), HotspotRepository(world.session)).tag_hotspots(
            [tagged.id], add=[important.id, right.id], remove=[], user=user
        )
        assert hotspot_status(tagged) == HotspotStatus.TO_REANNOTATE
        assert hotspot_status(untagged) == HotspotStatus.UNTAGGED
        ids = lambda **kw: [h.id for h in world.service.list_hotspots(None, **kw)]  # noqa: E731
        assert ids(status=HotspotStatus.TO_REANNOTATE) == [tagged.id]
        assert ids(status=HotspotStatus.UNTAGGED) == [untagged.id]
        assert ids(tag_ids=[important.id, right.id]) == [tagged.id]
        assert ids(sort=HotspotSort.SCORE) == [tagged.id, untagged.id]

    def test_reannotated_status_wins(self, world: World) -> None:
        assert hotspot_status(world.hotspot(reannotated_at=datetime.now(UTC))) == HotspotStatus.REANNOTATED

    def test_sort_by_disagreement_uses_the_agreement_metric(self, world: World) -> None:
        agree, disagree, unknown = world.hotspot(3), world.hotspot(2), world.hotspot(1)
        world.session.add_all(
            [
                HotspotAgreement(hotspot_id=agree.id, kappa=0.9, iou=0.1, per_annotator={},
                                 metric=AgreementMetric.KAPPA, threshold=0.5),
                HotspotAgreement(hotspot_id=disagree.id, kappa=0.9, iou=0.2, per_annotator={},
                                 metric=AgreementMetric.IOU, threshold=0.5),
            ]
        )
        world.session.flush()
        world.session.expire_all()
        ordered = world.service.list_hotspots(None, sort=HotspotSort.DISAGREEMENT)
        # 1 - 0.9 (kappa) = 0.1 vs 1 - 0.2 (iou) = 0.8
        assert [h.id for h in ordered] == [disagree.id, agree.id, unknown.id]

    def test_sort_by_cell_count_needs_a_finished_segmentation(self, world: World) -> None:
        pending = world.hotspot(1, segmentation_status=SegmentationStatus.PENDING)
        done = world.hotspot(0, segmentation_status=SegmentationStatus.DONE)
        ordered = world.service.list_hotspots(None, sort=HotspotSort.CELL_COUNT)
        assert [h.id for h in ordered] == [done.id, pending.id]

    def test_sort_by_the_models_uncertainty_puts_the_least_sure_first(self, world: World) -> None:
        unsure_outlines = world.hotspot(0, model_outline_confidence=0.5, model_class_confidence=0.9)
        unsure_types = world.hotspot(1, model_outline_confidence=0.9, model_class_confidence=0.3)
        sure = world.hotspot(2, model_outline_confidence=0.95, model_class_confidence=0.95)
        unknown = world.hotspot(3)

        def ids(sort: HotspotSort) -> list[Any]:
            return [h.id for h in world.service.list_hotspots(None, sort=sort)]

        assert ids(HotspotSort.OUTLINE_UNCERTAINTY) == [unsure_outlines.id, unsure_types.id, sure.id, unknown.id]
        assert ids(HotspotSort.CLASS_UNCERTAINTY) == [unsure_types.id, unsure_outlines.id, sure.id, unknown.id]
        # Mean uncertainty: (0.1 + 0.7) / 2 = 0.4 against (0.5 + 0.1) / 2 = 0.3.
        assert ids(HotspotSort.MODEL_UNCERTAINTY) == [unsure_types.id, unsure_outlines.id, sure.id, unknown.id]


class TestUpdateAndDelete:
    def test_rename_and_move_within_the_organization(self, world: World) -> None:
        hotspot = world.hotspot()
        other_folder = world.factory.folder(world.org, "Other")
        read = world.service.update_hotspot(
            hotspot.id, HotspotUpdate(name="Renamed", folder_id=other_folder.id), world.factory.account()
        )
        assert (read.name, read.folder_id) == ("Renamed", other_folder.id)

    def test_cannot_move_to_another_organization(self, world: World) -> None:
        hotspot = world.hotspot()
        foreign = world.factory.folder(world.factory.organization())
        with pytest.raises(InvalidInputError, match="another organization"):
            world.service.update_hotspot(hotspot.id, HotspotUpdate(folder_id=foreign.id), world.factory.account())

    def test_manual_reannotation_mark_on_legacy_hotspots(self, world: World) -> None:
        user = world.factory.account()
        hotspot = world.hotspot(layered=False)
        read = world.service.update_hotspot(hotspot.id, HotspotUpdate(reannotated=True), user)
        assert read.reannotated_at is not None and hotspot.reannotated_by_id == user.id
        first_mark = hotspot.reannotated_at
        world.service.update_hotspot(hotspot.id, HotspotUpdate(reannotated=True), world.factory.account())
        assert hotspot.reannotated_at == first_mark and hotspot.reannotated_by_id == user.id
        world.service.update_hotspot(hotspot.id, HotspotUpdate(reannotated=False), user)
        assert (hotspot.reannotated_at, hotspot.reannotated_by_id) == (None, None)

    def test_layered_hotspots_cannot_be_marked_by_hand(self, world: World) -> None:
        hotspot = world.hotspot(layered=True)
        with pytest.raises(InvalidInputError, match="in layers"):
            world.service.update_hotspot(hotspot.id, HotspotUpdate(reannotated=True), world.factory.account())

    def test_empty_update_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HotspotUpdate()

    def test_delete_removes_the_images(self, world: World) -> None:
        hotspot = world.hotspot()
        world.service.delete_hotspot(hotspot.id)
        assert world.storage.deleted_prefixes == [f"hotspots/{hotspot.id}/"]
        with pytest.raises(NotFoundError):
            world.service.get_hotspot(hotspot.id)


class TestUploadImage:
    def png(self) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), "purple").save(buffer, format="PNG")
        return buffer.getvalue()

    def test_stores_image_thumbnail_and_features(self, world: World) -> None:
        read = world.service.upload_image(world.folder.id, "  case 7.png", self.png())
        assert read.name == "case 7"
        assert (read.image_width, read.image_height) == (32, 24)
        assert read.features is not None and set(read.features) == set(ALL_FEATURES)
        assert set(world.storage.objects) == {f"hotspots/{read.id}/image.png", f"hotspots/{read.id}/thumbnail.jpg"}
        image, media_type = world.service.get_image(read.id)
        assert (image, media_type) == (self.png(), "image/png")

    def test_nameless_file_gets_the_default_name(self, world: World) -> None:
        assert world.service.upload_image(world.folder.id, "   .png", self.png()).name == "Obraz"

    def test_unknown_folder(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            world.service.upload_image(uuid.uuid4(), "a.png", self.png())


class TestBatches:
    def request(self, world: World, slide: Slide, boxes: list[tuple[int, int]], **fields: Any) -> HotspotBatchCreate:
        return HotspotBatchCreate(
            id=fields.get("id", uuid.uuid4()), slide_id=slide.id, folder_id=world.folder.id, name_prefix="Box",
            boxes=boxes,
        )

    def test_duplicate_boxes_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HotspotBatchCreate(
                id=uuid.uuid4(), slide_id=uuid.uuid4(), folder_id=uuid.uuid4(), name_prefix="B", boxes=[(0, 0), (0, 0)]
            )

    def test_create_and_run(self, world: World) -> None:
        slide = world.slide()
        batch, new = world.service.create_batch(self.request(world, slide, [(0, 0), (1024, 0)]))
        assert new and (batch.total, batch.done, batch.status) == (2, 0, HotspotBatchStatus.RUNNING)
        world.service.run_batch(batch.id)
        done = world.service.get_batch(batch.id)
        assert (done.done, done.status) == (2, HotspotBatchStatus.DONE)
        names = sorted(h.name for h in world.service.list_hotspots(world.folder.id))
        assert names == ["Box (0, 0)", "Box (1024, 0)"]
        assert all(h.size == HOTSPOT_BOX_SIZE and h.mpp == 0.25 for h in world.service.list_hotspots(None))

    def test_retried_request_returns_the_existing_batch(self, world: World) -> None:
        slide = world.slide()
        request = self.request(world, slide, [(0, 0)])
        first, _ = world.service.create_batch(request)
        again, new = world.service.create_batch(request)
        assert not new and again.id == first.id

    def test_box_must_lie_inside_the_slide(self, world: World) -> None:
        slide = world.slide(width=2000, height=2000)
        with pytest.raises(InvalidInputError, match=r"\(1000, 0\)"):
            world.service.create_batch(self.request(world, slide, [(0, 0), (1000, 0)]))

    def test_slide_must_be_ready(self, world: World) -> None:
        slide = world.slide(status=SlideStatus.PROCESSING)
        with pytest.raises(ConflictError, match="not ready"):
            world.service.create_batch(self.request(world, slide, [(0, 0)]))

    def test_folder_must_belong_to_the_slides_organization(self, world: World) -> None:
        slide = world.slide(organization_id=world.factory.organization().id)
        with pytest.raises(InvalidInputError, match="another organization"):
            world.service.create_batch(self.request(world, slide, [(0, 0)]))

    def test_failure_keeps_finished_hotspots_and_records_the_error(self, world: World) -> None:
        slide = world.slide()
        batch, _ = world.service.create_batch(self.request(world, slide, [(0, 0), (1024, 0), (2048, 0)]))
        world.slides.fail_at = (1024, 0)
        world.service.run_batch(batch.id)
        failed = world.service.get_batch(batch.id)
        assert (failed.done, failed.status) == (1, HotspotBatchStatus.FAILED)
        assert failed.error_message == "Unexpected error while cutting out the hotspots."
        assert len(world.service.list_hotspots(None)) == 1

    def test_finished_batch_is_not_run_again(self, world: World) -> None:
        slide = world.slide()
        batch, _ = world.service.create_batch(self.request(world, slide, [(0, 0)]))
        world.service.run_batch(batch.id)
        world.service.run_batch(batch.id)
        assert world.slides.reads == [(0, 0)]

    def test_unknown_batch(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            world.service.get_batch(uuid.uuid4())
        world.service.run_batch(uuid.uuid4())  # nothing to do, no error

    def test_interrupted_batches_are_failed_at_startup(
        self, world: World, session_factory: sessionmaker[Session]
    ) -> None:
        slide = world.slide()
        now = datetime(2026, 1, 1, 12, tzinfo=UTC)
        for hours_ago, key in ((2, "stale"), (0.5, "fresh")):
            world.session.add(
                HotspotBatch(
                    id=uuid.uuid4(), slide_id=slide.id, slide_name="S", folder_id=world.folder.id, name_prefix=key,
                    boxes=[[0, 0]], status=HotspotBatchStatus.RUNNING, created_at=now - timedelta(hours=hours_ago),
                )
            )
        world.session.commit()
        fail_interrupted_batches(session_factory, now=now)
        world.session.expire_all()
        statuses = {b.name_prefix: (b.status, b.error_message) for b in world.session.query(HotspotBatch)}
        assert statuses == {
            "stale": (HotspotBatchStatus.FAILED, BATCH_INTERRUPTED),
            "fresh": (HotspotBatchStatus.RUNNING, None),
        }


class TestReannotationRead:
    def test_full_name(self, factory: Factory) -> None:
        account = factory.account()
        account.profile.first_name, account.profile.last_name = "Ada", ""
        assert full_name_of(account) == "Ada"
        account.profile.first_name = ""
        assert full_name_of(account) is None
        assert full_name_of(factory.account(with_profile=False)) is None

    def test_metric_value_follows_the_metric(self) -> None:
        agreement = HotspotAgreement(kappa=0.3, iou=0.7, metric=AgreementMetric.KAPPA)
        assert metric_value(agreement) == 0.3
        agreement.metric = AgreementMetric.IOU
        assert metric_value(agreement) == 0.7
