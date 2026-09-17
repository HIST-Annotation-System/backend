"""Local, re-creatable copies of slides stored in object storage (OpenSlide needs filesystem paths)."""

import os
import shutil
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from app.exceptions.errors import InvalidInputError
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

FOLDER_SEPARATOR = "/"
ZIP_MAX_UNCOMPRESSED_BYTES = 50 * 1024**3
INVALID_ARCHIVE = "The uploaded file is not a valid ZIP archive."
ARCHIVE_TOO_LARGE = "The archive is too large once extracted."
UNSAFE_ARCHIVE_ENTRY = "Archive entry '{name}' has an unsafe path."


class SlideFileService:
    def __init__(self, storage: ObjectStorage, cache_dir: Path) -> None:
        self._storage = storage
        self._cache_dir = cache_dir

    def entry_path(self, slide: Slide) -> Path:
        """The file OpenSlide should open for `slide`, downloading the slide first if needed."""
        return find_slide_entry(self.materialize(slide.id, slide.storage_key))

    def materialize(
        self, slide_id: uuid.UUID, storage_key: str, on_progress: Callable[[int], None] | None = None
    ) -> Path:
        """Ensure the slide's files exist in the cache and return their directory.

        The download happens in a temporary directory that is renamed atomically, so concurrent workers
        never see a partial copy; if another worker finished first, its copy is kept. `on_progress`,
        when given, is called with each chunk's byte count as the file downloads (see `ObjectStorage`).
        """
        slide_dir = self._cache_dir / str(slide_id)
        if slide_dir.is_dir():
            return slide_dir
        temp_dir = self._cache_dir / f"{slide_id}.tmp-{uuid.uuid4().hex}"
        archive = temp_dir.with_name(f"{temp_dir.name}{ARCHIVE_SUFFIX}")
        temp_dir.mkdir(parents=True)
        try:
            filename = PurePosixPath(storage_key).name
            if storage_key.endswith(FOLDER_SEPARATOR):
                self._download_folder(storage_key, temp_dir, on_progress)
            elif filename.lower().endswith(ARCHIVE_SUFFIX):
                self._storage.download_file(storage_key, archive, on_progress)
                extract_zip_safely(archive, temp_dir)
            else:
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

    def _download_folder(self, prefix: str, destination: Path, on_progress: Callable[[int], None] | None) -> None:
        """Download an extracted multi-file slide, re-validating its layout (the folder may have changed)."""
        members = list_folder_members(self._storage, prefix)
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


def list_folder_members(storage: ObjectStorage, prefix: str) -> dict[PurePosixPath, StorageObject]:
    """The files under a storage folder keyed by path relative to it, without folder markers and archive metadata."""
    members = {PurePosixPath(obj.key[len(prefix) :]): obj for obj in storage.iter_keys(prefix)}
    return {
        path: obj
        for path, obj in members.items()
        if not obj.key.endswith(FOLDER_SEPARATOR) and not is_ignored_member(path)
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
            if sum(member.file_size for member in members.values()) > ZIP_MAX_UNCOMPRESSED_BYTES:
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
