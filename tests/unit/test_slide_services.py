import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError, SlidePreparingError
from app.models import Hotspot
from app.models.hotspot import HotspotImageType, SegmentationStatus
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.schemas.slides import slide_version
from app.services.segmentation import SEGMENTATION_RESEND_AFTER, SegmentationDispatcher
from app.services.slide_upload import SlideUploadService
from app.services.slides import OpenedSlide, SlideHandleCache, SlideService
from conftest import Factory, MemoryStorage


def opened() -> OpenedSlide:
    return OpenedSlide(slide=object(), deepzoom=object())  # type: ignore[arg-type]


class TestSlideHandleCache:
    def test_get_put(self) -> None:
        cache = SlideHandleCache(max_size=2)
        key, entry = uuid.uuid4(), opened()
        assert cache.get(key) is None
        cache.put(key, entry)
        assert cache.get(key) is entry and len(cache) == 1

    def test_least_recently_used_is_evicted(self) -> None:
        cache = SlideHandleCache(max_size=2)
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        cache.put(a, opened())
        cache.put(b, opened())
        cache.get(a)  # a is now the most recently used
        cache.put(c, opened())
        assert cache.get(b) is None
        assert cache.get(a) is not None and cache.get(c) is not None
        assert len(cache) == 2

    def test_evict(self) -> None:
        cache = SlideHandleCache()
        key = uuid.uuid4()
        cache.put(key, opened())
        cache.evict(key)
        cache.evict(uuid.uuid4())  # unknown ids are fine
        assert len(cache) == 0


def test_slide_version_is_stable_and_follows_the_name() -> None:
    assert slide_version("a") == slide_version("a")
    assert slide_version("a") != slide_version("b")
    assert len(slide_version("a")) == 16


class FakeFiles:
    """No slide is cached."""

    def __init__(self) -> None:
        self.purged: list[uuid.UUID] = []
        self.background: list[uuid.UUID] = []
        self.touched: list[uuid.UUID] = []

    def purge(self, slide_id: uuid.UUID) -> None:
        self.purged.append(slide_id)

    def is_cached(self, slide_id: uuid.UUID) -> bool:
        return False

    def download_in_background(self, slide: Slide, pool: Any) -> None:
        self.background.append(slide.id)

    def touch(self, slide_id: uuid.UUID) -> None:
        self.touched.append(slide_id)


class DeletingStorage(MemoryStorage):
    def __init__(self) -> None:
        super().__init__()
        self.deleted_objects: list[str] = []

    def delete_objects(self, keys: Any) -> None:
        self.deleted_objects.extend(keys)

    def head_size(self, key: str) -> int:
        return 1234


class SlideWorld:
    def __init__(self, session: Session, factory: Factory) -> None:
        self.session = session
        self.org = factory.organization()
        self.storage = DeletingStorage()
        self.files = FakeFiles()
        self.handles = SlideHandleCache()
        self.repo = SlideRepository(session)
        self.service = SlideService(self.repo, self.storage, self.files, self.handles)  # type: ignore[arg-type]
        self.uploads = SlideUploadService(self.repo, self.storage)  # type: ignore[arg-type]

    def slide(self, **fields: Any) -> Slide:
        slide_id = uuid.uuid4()
        values: dict[str, Any] = {
            "name": "Biopsy",
            "original_filename": "b.svs",
            "storage_key": f"slides/{slide_id}/b.svs",
            "status": SlideStatus.READY,
            "organization_id": self.org.id,
            **fields,
        }
        slide = Slide(id=slide_id, **values)
        self.session.add(slide)
        self.session.commit()
        return slide


@pytest.fixture
def sw(session: Session, factory: Factory) -> SlideWorld:
    return SlideWorld(session, factory)


class TestRenameAndDelete:
    def test_rename_with_the_current_version(self, sw: SlideWorld) -> None:
        slide = sw.slide()
        assert sw.service.rename_slide(slide.id, "New", slide_version("Biopsy")).name == "New"

    def test_rename_without_a_version_skips_the_check(self, sw: SlideWorld) -> None:
        assert sw.service.rename_slide(sw.slide().id, "New").name == "New"

    def test_stale_version_is_a_conflict(self, sw: SlideWorld) -> None:
        slide = sw.slide()
        sw.service.rename_slide(slide.id, "Changed by someone else")
        with pytest.raises(ConflictError) as error:
            sw.service.rename_slide(slide.id, "Mine", slide_version("Biopsy"))
        assert error.value.code == "slide_modified"
        with pytest.raises(ConflictError):
            sw.service.delete_slide(slide.id, slide_version("Biopsy"))

    def test_unknown_slide(self, sw: SlideWorld) -> None:
        with pytest.raises(NotFoundError) as error:
            sw.service.rename_slide(uuid.uuid4(), "x")
        assert error.value.code == "slide_not_found"

    def test_delete_removes_files_handles_and_objects(self, sw: SlideWorld) -> None:
        slide = sw.slide()
        sw.handles.put(slide.id, opened())
        sw.service.delete_slide(slide.id)
        assert sw.files.purged == [slide.id]
        assert sw.handles.get(slide.id) is None
        assert sw.storage.deleted_prefixes == [f"slides/{slide.id}/"]
        assert sw.storage.aborted == [] and sw.storage.deleted_objects == []
        assert sw.repo.get(slide.id) is None

    def test_delete_of_an_unfinished_upload_aborts_it(self, sw: SlideWorld) -> None:
        slide = sw.slide(status=SlideStatus.UPLOADING)
        sw.service.delete_slide(slide.id)
        assert sw.storage.aborted == [slide.storage_key]

    def test_delete_of_an_imported_file_removes_it(self, sw: SlideWorld) -> None:
        slide = sw.slide(storage_key="imports/case.svs")
        sw.service.delete_slide(slide.id)
        assert sw.storage.deleted_objects == ["imports/case.svs"]

    def test_delete_of_an_imported_folder_removes_the_folder(self, sw: SlideWorld) -> None:
        slide = sw.slide(storage_key="imports/mrxs/")
        sw.service.delete_slide(slide.id)
        assert sw.storage.deleted_prefixes[-1] == "imports/mrxs/"

    def test_other_organizations_slides_are_invisible(self, sw: SlideWorld, factory: Factory) -> None:
        slide = sw.slide()
        other = SlideService(
            SlideRepository(sw.session, factory.organization().id), sw.storage, sw.files, sw.handles  # type: ignore[arg-type]
        )
        assert other.list_slides() == []
        with pytest.raises(NotFoundError):
            other.get_slide(slide.id)


class TestUncachedSlide:
    def service(self, sw: SlideWorld) -> SlideService:
        return SlideService(sw.repo, sw.storage, sw.files, sw.handles, download_pool=object())  # type: ignore[arg-type]

    def test_a_request_starts_the_download_instead_of_waiting(self, sw: SlideWorld) -> None:
        slide = sw.slide()
        with pytest.raises(SlidePreparingError) as error:
            self.service(sw).get_dzi(slide.id)
        assert error.value.status_code == 503 and error.value.code == "slide_preparing"
        assert sw.files.background == [slide.id]

    def test_an_open_slide_needs_no_download_and_counts_as_used(self, sw: SlideWorld) -> None:
        slide = sw.slide()
        sw.handles.put(slide.id, opened())
        assert self.service(sw)._open(slide.id) is sw.handles.get(slide.id)
        assert sw.files.background == []
        assert sw.files.touched == [slide.id]

    def test_a_slide_that_is_not_ready_is_still_a_conflict(self, sw: SlideWorld) -> None:
        slide = sw.slide(status=SlideStatus.PROCESSING)
        with pytest.raises(ConflictError):
            self.service(sw).get_dzi(slide.id)
        assert sw.files.background == []


class TestUploads:
    def test_start_creates_an_uploading_slide_with_a_safe_key(self, sw: SlideWorld) -> None:
        started = sw.uploads.start_upload(r"C:\scans\Case #1 (HE).SVS", 7, 100, organization_id=sw.org.id)
        slide = sw.repo.get(started.slide_id)
        assert slide is not None
        assert slide.status == SlideStatus.UPLOADING
        assert slide.name == "Case #1 (HE)"
        assert slide.original_filename == "Case #1 (HE).SVS"
        assert slide.storage_key == f"slides/{slide.id}/Case_1_HE.svs"
        assert sw.storage.multipart[started.upload_id] == slide.storage_key

    def test_name_of_only_unsafe_characters_gets_a_default_key(self, sw: SlideWorld) -> None:
        started = sw.uploads.start_upload("żółć.svs", organization_id=sw.org.id)
        slide = sw.repo.get(started.slide_id)
        assert slide is not None and slide.storage_key.endswith("/slide.svs")
        assert slide.name == "żółć"

    def test_invalid_file_type(self, sw: SlideWorld) -> None:
        with pytest.raises(InvalidInputError):
            sw.uploads.start_upload("a.png", organization_id=sw.org.id)

    def test_duplicate_of_the_same_file_in_the_organization(self, sw: SlideWorld, factory: Factory) -> None:
        sw.slide(original_filename="b.svs", file_size_bytes=100)
        with pytest.raises(ConflictError) as error:
            sw.uploads.start_upload("b.svs", file_size=100, organization_id=sw.org.id)
        assert error.value.code == "duplicate_slide"
        # Confirmed by the user, another organization, another size, or no size: allowed.
        sw.uploads.start_upload("b.svs", file_size=100, allow_duplicate=True, organization_id=sw.org.id)
        sw.uploads.start_upload("b.svs", file_size=100, organization_id=factory.organization().id)
        sw.uploads.start_upload("b.svs", file_size=101, organization_id=sw.org.id)
        sw.uploads.start_upload("b.svs", organization_id=sw.org.id)

    def test_failed_slides_are_not_duplicates(self, sw: SlideWorld) -> None:
        sw.slide(original_filename="b.svs", file_size_bytes=100, status=SlideStatus.FAILED)
        sw.uploads.start_upload("b.svs", file_size=100, organization_id=sw.org.id)

    def test_presign_progress_complete(self, sw: SlideWorld) -> None:
        started = sw.uploads.start_upload("a.svs", organization_id=sw.org.id)
        urls = sw.uploads.presign_parts(started.slide_id, started.upload_id, 3).urls
        assert [url.split("part=")[1].split("&")[0] for url in urls] == ["1", "2", "3"]
        refreshed = sw.uploads.refresh_part_urls(started.slide_id, started.upload_id, [2]).urls
        assert list(refreshed) == [2]
        sw.uploads.report_progress(started.slide_id, 50)
        assert sw.repo.get(started.slide_id).ingest_progress == 50  # type: ignore[union-attr]

        sw.uploads.complete_upload(started.slide_id, started.upload_id, {1: "e1", 2: "e2", 3: "e3"})
        slide = sw.repo.get(started.slide_id)
        assert slide is not None
        assert (slide.status, slide.ingest_progress, slide.file_size_bytes) == (SlideStatus.PROCESSING, None, 1234)
        assert sw.storage.completed[0][2] == {1: "e1", 2: "e2", 3: "e3"}

    def test_finished_upload_accepts_no_more_parts(self, sw: SlideWorld) -> None:
        slide = sw.slide(status=SlideStatus.PROCESSING)
        for call in (
            lambda: sw.uploads.presign_parts(slide.id, "u", 1),
            lambda: sw.uploads.complete_upload(slide.id, "u", {}),
            lambda: sw.uploads.report_progress(slide.id, 1),
            lambda: sw.uploads.cancel_upload(slide.id),
        ):
            with pytest.raises(ConflictError) as error:
                call()
            assert error.value.code == "slide_not_uploading"

    def test_cancel_aborts_and_drops_the_slide(self, sw: SlideWorld) -> None:
        started = sw.uploads.start_upload("a.svs", organization_id=sw.org.id)
        sw.uploads.cancel_upload(started.slide_id)
        assert sw.repo.get(started.slide_id) is None
        assert len(sw.storage.aborted) == 1


class TestImportFromStorage:
    def test_import_a_single_file(self, sw: SlideWorld) -> None:
        slide_id, created = sw.uploads.import_from_storage("imports/Case 2.ndpi", 1, organization_id=sw.org.id)
        slide = sw.repo.get(slide_id)
        assert created and slide is not None
        assert (slide.name, slide.status, slide.file_size_bytes) == ("Case 2", SlideStatus.PROCESSING, 1234)

    def test_import_is_idempotent(self, sw: SlideWorld) -> None:
        first, _ = sw.uploads.import_from_storage("imports/a.svs", organization_id=sw.org.id)
        again, created = sw.uploads.import_from_storage("imports/a.svs", organization_id=sw.org.id)
        assert (again, created) == (first, False)

    def test_application_objects_cannot_be_imported(self, sw: SlideWorld) -> None:
        with pytest.raises(InvalidInputError, match="managed by the application"):
            sw.uploads.import_from_storage("slides/x/a.svs", organization_id=sw.org.id)

    @pytest.mark.parametrize(("existing", "key"), [("imports/mrxs/", "imports/mrxs/s.mrxs"), ("imports/a/b.dcm", "imports/a/")])
    def test_overlapping_imports_are_refused(self, sw: SlideWorld, existing: str, key: str) -> None:
        sw.slide(storage_key=existing)
        with pytest.raises(ConflictError, match="overlaps"):
            sw.uploads.import_from_storage(key, organization_id=sw.org.id)


class TestSegmentationDispatcher:
    def make(self, session_factory: sessionmaker[Session], fail_after: int | None = None) -> tuple[Any, list[Any]]:
        published: list[tuple[uuid.UUID, str, float | None]] = []

        def publish(hotspot_id: uuid.UUID, key: str, mpp: float | None) -> None:
            if fail_after is not None and len(published) >= fail_after:
                raise ConnectionError("broker down")
            published.append((hotspot_id, key, mpp))

        return SegmentationDispatcher(session_factory, publish), published

    def pending(self, factory: Factory, session: Session, count: int) -> list[Hotspot]:
        folder = factory.folder(factory.organization())
        hotspots = [
            factory.hotspot(folder, image_type=HotspotImageType.JPEG, mpp=0.5) for _ in range(count)
        ]
        session.commit()
        return hotspots

    def test_sends_pending_hotspots_once_until_the_resend_delay(
        self, session_factory: sessionmaker[Session], session: Session, factory: Factory
    ) -> None:
        [hotspot] = self.pending(factory, session, 1)
        dispatcher, published = self.make(session_factory)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        assert dispatcher.dispatch_once(now) == 1
        assert published == [(hotspot.id, f"hotspots/{hotspot.id}/image.jpg", 0.5)]
        assert dispatcher.dispatch_once(now + timedelta(minutes=1)) == 0
        assert dispatcher.dispatch_once(now + SEGMENTATION_RESEND_AFTER + timedelta(seconds=1)) == 1

    def test_finished_hotspots_are_not_sent(
        self, session_factory: sessionmaker[Session], session: Session, factory: Factory
    ) -> None:
        folder = factory.folder(factory.organization())
        factory.hotspot(folder, segmentation_status=SegmentationStatus.DONE)
        session.commit()
        dispatcher, published = self.make(session_factory)
        assert dispatcher.dispatch_once() == 0 and published == []

    def test_a_hotspot_that_could_not_be_sent_stays_due(
        self, session_factory: sessionmaker[Session], session: Session, factory: Factory
    ) -> None:
        self.pending(factory, session, 3)
        failing, published = self.make(session_factory, fail_after=1)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(ConnectionError):
            failing.dispatch_once(now)
        assert len(published) == 1
        healthy, resent = self.make(session_factory)
        # Only the two that never went out are sent again right away.
        assert healthy.dispatch_once(now + timedelta(seconds=5)) == 2
        assert {item[0] for item in resent}.isdisjoint({published[0][0]})
