"""Local, re-creatable copies of slides stored in object storage (OpenSlide needs filesystem paths)."""

import logging
import os
import shutil
import threading
import uuid
import zipfile
from collections.abc import Callable, Collection
from concurrent.futures import Executor
from pathlib import Path, PurePosixPath

from app.exceptions.errors import AppError, InsufficientStorageError, InvalidInputError, StorageError
from app.models.slide import Slide
from app.services.slide_formats import (
    AMBIGUOUS_ARCHIVE,
    ARCHIVE_SUFFIX,
    DICOM_SUFFIX,
    INDEX_SUFFIXES,
    is_ignored_member,
    validate_slide_members,
)
from app.services.storage import ObjectStorage, StorageObject

logger = logging.getLogger(__name__)

# Left free on the cache disk after every download, for logs, temporary files and the system itself.
CACHE_MIN_FREE_BYTES = 2 * 1024**3
CACHE_FULL = "Not enough disk space on the server for this slide ({needed:.1f} GB); the other cached slides are in use."
DOWNLOAD_FAILED = "The slide could not be copied to the server."
INVALID_ARCHIVE = "The uploaded file is not a valid ZIP archive."
ARCHIVE_TOO_LARGE = "The archive is too large to extract on the server's disk."
UNSAFE_ARCHIVE_ENTRY = "Archive entry '{name}' has an unsafe path."


class SlideDownloadLocks:
    """One lock per slide, so concurrent requests for a slide not cached yet download it only once; plus which
    background downloads are under way, and why the last one of each slide failed.

    Process-wide (built in the lifespan); locks are tiny and slides few, so they are never dropped.
    """

    def __init__(self) -> None:
        self._locks: dict[uuid.UUID, threading.Lock] = {}
        self._background: set[uuid.UUID] = set()
        self._failures: dict[uuid.UUID, AppError] = {}
        self._guard = threading.Lock()

    def get(self, slide_id: uuid.UUID) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(slide_id, threading.Lock())

    def start_background(self, slide_id: uuid.UUID) -> bool:
        """False when a background download of the slide is already queued or running."""
        with self._guard:
            if slide_id in self._background:
                return False
            self._background.add(slide_id)
            return True

    def finish_background(self, slide_id: uuid.UUID, failure: AppError | None) -> None:
        with self._guard:
            self._background.discard(slide_id)
            if failure is not None:
                self._failures[slide_id] = failure

    def take_failure(self, slide_id: uuid.UUID) -> AppError | None:
        with self._guard:
            return self._failures.pop(slide_id, None)


class SlideFileService:
    """The slide cache. Its disk is bounded, so the least recently opened slides are deleted to make room."""

    def __init__(
        self,
        storage: ObjectStorage,
        cache_dir: Path,
        locks: SlideDownloadLocks,
        open_slides: Callable[[], Collection[uuid.UUID]] = tuple,
    ) -> None:
        self._storage = storage
        self._cache_dir = cache_dir
        self._locks = locks
        # Slides with an open OpenSlide handle: never deleted from under it.
        self._open_slides = open_slides

    def entry_path(self, slide: Slide) -> Path:
        """The file OpenSlide should open for `slide`, downloading the slide first if needed."""
        slide_dir = self.materialize(slide.id, slide.storage_key)
        # The directory's mtime is the slide's last use: eviction deletes the least recently opened slides first.
        os.utime(slide_dir)
        return find_slide_entry(slide_dir)

    def is_cached(self, slide_id: uuid.UUID) -> bool:
        return (self._cache_dir / str(slide_id)).is_dir()

    def download_in_background(self, slide: Slide, pool: Executor) -> None:
        """Starts copying the slide to the cache unless that is under way; raises, once, why the last attempt failed."""
        failure = self._locks.take_failure(slide.id)
        if failure is not None:
            raise failure
        if self._locks.start_background(slide.id):
            pool.submit(self._background_download, slide.id, slide.storage_key)

    def _background_download(self, slide_id: uuid.UUID, storage_key: str) -> None:
        failure: AppError | None = None
        try:
            self.materialize(slide_id, storage_key)
        except AppError as exc:
            logger.warning("Slide %s could not be cached: %s", slide_id, exc.message)
            failure = exc
        except Exception:
            logger.exception("Unexpected error while caching slide %s", slide_id)
            failure = StorageError(DOWNLOAD_FAILED)
        self._locks.finish_background(slide_id, failure)

    def materialize(
        self, slide_id: uuid.UUID, storage_key: str, on_progress: Callable[[int], None] | None = None
    ) -> Path:
        """Ensure the slide's files exist in the cache and return their directory.

        The download happens in a temporary directory that is renamed atomically, so concurrent workers
        never see a partial copy; within this process, concurrent callers wait for the first one's
        download instead of starting their own. `on_progress`, when given, is called with each chunk's
        byte count as the file downloads (see `ObjectStorage`).
        """
        slide_dir = self._cache_dir / str(slide_id)
        if slide_dir.is_dir():
            return slide_dir
        with self._locks.get(slide_id):
            if slide_dir.is_dir():
                return slide_dir
            return self._download(slide_id, storage_key, slide_dir, on_progress)

    def _download(
        self, slide_id: uuid.UUID, storage_key: str, slide_dir: Path, on_progress: Callable[[int], None] | None
    ) -> Path:
        temp_dir = self._cache_dir / f"{slide_id}.tmp-{uuid.uuid4().hex}"
        archive = temp_dir.with_name(f"{temp_dir.name}{ARCHIVE_SUFFIX}")
        temp_dir.mkdir(parents=True)
        try:
            filename = PurePosixPath(storage_key).name
            if storage_key.endswith("/"):
                members = list_folder_members(self._storage, storage_key)
                self._make_room(sum(member.size for member in members.values()))
                self._download_folder(members, temp_dir, on_progress)
            elif filename.lower().endswith(ARCHIVE_SUFFIX):
                # The archive and its extracted copy are both on disk until the archive is deleted.
                self._make_room(2 * self._storage.head_size(storage_key))
                self._storage.download_file(storage_key, archive, on_progress)
                extract_zip_safely(archive, temp_dir)
            else:
                self._make_room(self._storage.head_size(storage_key))
                self._storage.download_file(storage_key, temp_dir / filename, on_progress)
            try:
                os.replace(temp_dir, slide_dir)
            except OSError:
                if not slide_dir.is_dir():
                    raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            archive.unlink(missing_ok=True)
        return slide_dir

    def _make_room(self, needed: int) -> None:
        """Deletes the least recently opened slides until `needed` more bytes fit with CACHE_MIN_FREE_BYTES to spare.

        Open slides stay, and so do slides another thread is downloading or deleting (their lock is held).
        """
        open_slides = set(self._open_slides())
        for slide_dir in sorted(self._cached_slide_dirs(), key=_modified_at):
            if self._free_bytes() - needed >= CACHE_MIN_FREE_BYTES:
                return
            slide_id = uuid.UUID(slide_dir.name)
            lock = self._locks.get(slide_id)
            if slide_id in open_slides or not lock.acquire(blocking=False):
                continue
            try:
                logger.info("Removing slide %s from the cache to make room", slide_id)
                shutil.rmtree(slide_dir, ignore_errors=True)
            finally:
                lock.release()
        if self._free_bytes() - needed < CACHE_MIN_FREE_BYTES:
            raise InsufficientStorageError(CACHE_FULL.format(needed=needed / 1024**3))

    def _cached_slide_dirs(self) -> list[Path]:
        """Complete slide copies (downloads in progress are named `<id>.tmp-...`)."""
        return [path for path in self._cache_dir.iterdir() if path.is_dir() and _is_slide_id(path.name)]

    def _free_bytes(self) -> int:
        return shutil.disk_usage(self._cache_dir).free

    def _download_folder(
        self, members: dict[PurePosixPath, StorageObject], destination: Path, on_progress: Callable[[int], None] | None
    ) -> None:
        """Download an extracted multi-file slide, re-validating its layout (the folder may have changed)."""
        validate_slide_members(list(members))
        root = destination.resolve()
        for relative, member in members.items():
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                raise InvalidInputError(UNSAFE_ARCHIVE_ENTRY.format(name=member.key))
            target.parent.mkdir(parents=True, exist_ok=True)
            self._storage.download_file(member.key, target, on_progress)

    def purge(self, slide_id: uuid.UUID) -> None:
        """Removes the slide's local cache directory, if any (called when the slide itself is deleted)."""
        shutil.rmtree(self._cache_dir / str(slide_id), ignore_errors=True)


def _is_slide_id(name: str) -> bool:
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return True


def _modified_at(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:  # deleted meanwhile
        return 0.0


def list_folder_members(storage: ObjectStorage, prefix: str) -> dict[PurePosixPath, StorageObject]:
    """The files under a storage folder keyed by path relative to it, without folder markers and archive metadata."""
    members = {PurePosixPath(obj.key[len(prefix) :]): obj for obj in storage.iter_keys(prefix)}
    return {
        path: obj
        for path, obj in members.items()
        if not obj.key.endswith("/") and not is_ignored_member(path)
    }


def find_slide_entry(directory: Path) -> Path:
    """The file OpenSlide opens: a single file, the index file of a multi-file slide, or a DICOM file."""
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    if len(files) == 1:
        return files[0]
    entries = [path for path in files if path.suffix.lower() in INDEX_SUFFIXES]
    if len(entries) == 1:
        return entries[0]
    # OpenSlide finds the rest of a DICOM series in the folder of whichever file it is given.
    if files and all(path.suffix.lower() == DICOM_SUFFIX for path in files):
        return files[0]
    raise InvalidInputError(AMBIGUOUS_ARCHIVE)


def extract_zip_safely(archive: Path, destination: Path) -> None:
    """Extract the multi-file slide in `archive` into `destination`.

    Rejects archives that aren't exactly one multi-file slide, path traversal and oversized content;
    archive metadata (see `is_ignored_member`) is not extracted.
    """
    try:
        with zipfile.ZipFile(archive) as zip_file:
            # Some Windows tools (e.g. PowerShell 5.1 Compress-Archive) write backslash separators.
            entries = {
                PurePosixPath(member.filename.replace("\\", "/")): member
                for member in zip_file.infolist()
                if not member.is_dir()
            }
            members = {path: member for path, member in entries.items() if not is_ignored_member(path)}
            validate_slide_members(list(members))
            # ZipExtFile never yields more than the declared size, so the declared sizes bound the output.
            free = shutil.disk_usage(archive.parent).free - CACHE_MIN_FREE_BYTES
            if sum(member.file_size for member in members.values()) > free:
                raise InvalidInputError(ARCHIVE_TOO_LARGE)
            root = destination.resolve()
            for path, member in members.items():
                target = (root / path).resolve()
                if not target.is_relative_to(root):
                    raise InvalidInputError(UNSAFE_ARCHIVE_ENTRY.format(name=member.filename))
                target.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
    except zipfile.BadZipFile as exc:
        raise InvalidInputError(INVALID_ARCHIVE) from exc
