import os
import uuid
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from app.exceptions.errors import InsufficientStorageError, InvalidInputError, StorageError
from app.models.slide import Slide
from app.services import slide_files
from app.services.slide_files import (
    SlideDownloadLocks,
    SlideFileService,
    extract_zip_safely,
    find_slide_entry,
    list_folder_members,
)
from app.services.storage import StorageObject

MRXS = {"s.mrxs": b"index", "s/Slidedat.ini": b"[GENERAL]", "s/Data0000.dat": b"\x00" * 64}


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


class TestExtractZipSafely:
    def test_extracts_a_valid_slide(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", MRXS)
        out = tmp_path / "out"
        extract_zip_safely(archive, out)
        assert (out / "s.mrxs").read_bytes() == b"index"
        assert (out / "s" / "Data0000.dat").stat().st_size == 64

    def test_leaves_out_archive_metadata(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {**MRXS, "__MACOSX/s/._x": b"meta", ".DS_Store": b"meta"})
        out = tmp_path / "out"
        extract_zip_safely(archive, out)
        assert not (out / "__MACOSX").exists()
        assert not (out / ".DS_Store").exists()

    def test_backslash_separators_become_folders(self, tmp_path: Path) -> None:
        members = {"s.mrxs": b"i", "s\\Slidedat.ini": b"x", "s\\Data0000.dat": b"d"}
        archive = make_zip(tmp_path / "a.zip", members)
        out = tmp_path / "out"
        extract_zip_safely(archive, out)
        assert (out / "s" / "Slidedat.ini").is_file()

    def test_path_traversal_is_rejected_and_nothing_escapes(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"../../evil.dcm": b"x", "../../evil2.dcm": b"y"})
        out = tmp_path / "deep" / "out"
        # Validation sees both in one folder; the traversal check must refuse them.
        with pytest.raises(InvalidInputError, match="unsafe path"):
            extract_zip_safely(archive, out)
        assert not (tmp_path / "evil.dcm").exists()

    def test_oversized_content_is_rejected_before_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # More reserved than the disk has: nothing fits.
        monkeypatch.setattr(slide_files, "CACHE_MIN_FREE_BYTES", 10**18)
        archive = make_zip(tmp_path / "a.zip", MRXS)
        out = tmp_path / "out"
        with pytest.raises(InvalidInputError, match="too large"):
            extract_zip_safely(archive, out)
        assert not out.exists()

    def test_not_a_zip(self, tmp_path: Path) -> None:
        bogus = tmp_path / "a.zip"
        bogus.write_bytes(b"definitely not a zip")
        with pytest.raises(InvalidInputError, match="not a valid ZIP"):
            extract_zip_safely(bogus, tmp_path / "out")

    def test_archive_that_is_not_one_slide(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"inner.svs": b"x"})
        with pytest.raises(InvalidInputError, match="uploaded directly"):
            extract_zip_safely(archive, tmp_path / "out")


class TestFindSlideEntry:
    def write(self, root: Path, *names: str) -> None:
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    def test_single_file(self, tmp_path: Path) -> None:
        self.write(tmp_path, "slide.svs")
        assert find_slide_entry(tmp_path) == tmp_path / "slide.svs"

    def test_index_file_of_a_multi_file_slide(self, tmp_path: Path) -> None:
        self.write(tmp_path, "s.mrxs", "s/Slidedat.ini", "s/Data0000.dat")
        assert find_slide_entry(tmp_path) == tmp_path / "s.mrxs"

    def test_any_file_of_a_dicom_series(self, tmp_path: Path) -> None:
        self.write(tmp_path, "b.dcm", "a.dcm")
        assert find_slide_entry(tmp_path) == tmp_path / "a.dcm"

    @pytest.mark.parametrize("names", [(), ("a.txt", "b.txt"), ("a.mrxs", "b.vms")])
    def test_ambiguous_or_empty(self, tmp_path: Path, names: tuple[str, ...]) -> None:
        self.write(tmp_path, *names)
        with pytest.raises(InvalidInputError):
            find_slide_entry(tmp_path)


class FakeStorage:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def iter_keys(self, prefix: str) -> Iterator[StorageObject]:
        for key in self.keys:
            if key.startswith(prefix):
                yield StorageObject(key=key, size=1, last_modified=datetime(2026, 1, 1, tzinfo=UTC))


def test_list_folder_members_skips_folder_markers_and_metadata() -> None:
    storage = FakeStorage(["imp/x/", "imp/x/s.mrxs", "imp/x/s/", "imp/x/s/Slidedat.ini", "imp/x/.DS_Store", "other/y"])
    members = list_folder_members(storage, "imp/x/")  # type: ignore[arg-type]
    assert set(members) == {PurePosixPath("s.mrxs"), PurePosixPath("s/Slidedat.ini")}
    assert members[PurePosixPath("s.mrxs")].key == "imp/x/s.mrxs"


SLIDE_BYTES = 30
DISK_BYTES = 100


class FileStorage:
    """Serves one fixed-size file per key, or fails every download while `error` is set."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.downloads = 0

    def head_size(self, key: str) -> int:
        return SLIDE_BYTES

    def download_file(self, key: str, path: Path, on_progress: Any = None) -> None:
        self.downloads += 1
        if self.error is not None:
            raise self.error
        path.write_bytes(b"x" * SLIDE_BYTES)


class Cache:
    """A slide cache on a pretend 100-byte disk that keeps 10 bytes free."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(slide_files, "CACHE_MIN_FREE_BYTES", 10)
        self.root = root
        self.storage = FileStorage()
        self.locks = SlideDownloadLocks()
        self.open: set[uuid.UUID] = set()
        self.files = SlideFileService(self.storage, root, self.locks, lambda: self.open)  # type: ignore[arg-type]
        self.files._free_bytes = self.free  # type: ignore[method-assign]

    def free(self) -> int:
        return DISK_BYTES - sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())

    def cached(self, age: int) -> uuid.UUID:
        """A cached slide last opened `age` seconds ago."""
        slide_id = uuid.uuid4()
        slide_dir = self.root / str(slide_id)
        slide_dir.mkdir()
        (slide_dir / "s.svs").write_bytes(b"x" * SLIDE_BYTES)
        os.utime(slide_dir, (0, 1_000_000 - age))
        return slide_id

    def has(self, slide_id: uuid.UUID) -> bool:
        return self.files.is_cached(slide_id)


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Cache:
    return Cache(tmp_path, monkeypatch)


def new_slide() -> Slide:
    slide_id = uuid.uuid4()
    return Slide(id=slide_id, storage_key=f"slides/{slide_id}/s.svs")


class TestEviction:
    def test_least_recently_opened_slide_makes_room(self, cache: Cache) -> None:
        oldest, middle, newest = cache.cached(age=30), cache.cached(age=20), cache.cached(age=10)
        slide = new_slide()
        cache.files.materialize(slide.id, slide.storage_key)
        assert cache.has(slide.id)
        assert not cache.has(oldest) and cache.has(middle) and cache.has(newest)

    def test_open_slides_and_slides_in_use_stay(self, cache: Cache) -> None:
        opened, busy, idle = cache.cached(age=30), cache.cached(age=20), cache.cached(age=10)
        cache.open.add(opened)
        with cache.locks.get(busy):
            slide = new_slide()
            cache.files.materialize(slide.id, slide.storage_key)
        assert cache.has(opened) and cache.has(busy) and not cache.has(idle)

    def test_downloads_in_progress_are_not_touched(self, cache: Cache) -> None:
        partial = cache.root / f"{uuid.uuid4()}.tmp-abc"
        partial.mkdir()
        (partial / "s.svs").write_bytes(b"x" * SLIDE_BYTES)
        cache.cached(age=10)
        slide = new_slide()
        cache.files.materialize(slide.id, slide.storage_key)
        assert partial.is_dir()

    def test_a_full_cache_of_open_slides_refuses_the_download(self, cache: Cache) -> None:
        cache.open.update({cache.cached(age=30), cache.cached(age=20), cache.cached(age=10)})
        slide = new_slide()
        with pytest.raises(InsufficientStorageError):
            cache.files.materialize(slide.id, slide.storage_key)
        assert not cache.has(slide.id) and cache.storage.downloads == 0

    def test_opening_a_slide_marks_it_recently_used(self, cache: Cache) -> None:
        reopened, other = cache.cached(age=30), cache.cached(age=20)
        cache.cached(age=10)
        cache.files.entry_path(Slide(id=reopened, storage_key="unused"))
        slide = new_slide()
        cache.files.materialize(slide.id, slide.storage_key)
        assert cache.has(reopened) and not cache.has(other)


class QueueingExecutor:
    """Collects submitted jobs; `run` executes them."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Callable[..., Any], tuple[Any, ...]]] = []

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        self.jobs.append((fn, args))

    def run(self) -> None:
        jobs, self.jobs = self.jobs, []
        for fn, args in jobs:
            fn(*args)


class TestBackgroundDownload:
    def test_starts_once_and_caches_the_slide(self, cache: Cache) -> None:
        pool, slide = QueueingExecutor(), new_slide()
        cache.files.download_in_background(slide, pool)  # type: ignore[arg-type]
        cache.files.download_in_background(slide, pool)  # type: ignore[arg-type]
        assert len(pool.jobs) == 1
        pool.run()
        assert cache.has(slide.id)

    def test_a_failure_is_reported_once_then_retried(self, cache: Cache) -> None:
        pool, slide = QueueingExecutor(), new_slide()
        cache.storage.error = RuntimeError("connection reset")
        cache.files.download_in_background(slide, pool)  # type: ignore[arg-type]
        pool.run()
        with pytest.raises(StorageError):
            cache.files.download_in_background(slide, pool)  # type: ignore[arg-type]
        assert pool.jobs == []
        cache.storage.error = None
        cache.files.download_in_background(slide, pool)  # type: ignore[arg-type]
        pool.run()
        assert cache.has(slide.id)
