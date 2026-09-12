"""Local, re-creatable copies of slides stored in object storage (OpenSlide needs filesystem paths)."""

import os
import shutil
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from app.exceptions.errors import InvalidInputError
from app.models.slide import Slide
from app.services.storage import ObjectStorage

ZIP_SUFFIX = ".zip"
MRXS_SUFFIX = ".mrxs"
# Formats made of an entry file plus companion files; they arrive as a .zip archive.
MULTI_FILE_SLIDE_SUFFIXES = frozenset({MRXS_SUFFIX, ".vms"})
MACOS_ARCHIVE_METADATA_DIR = "__MACOSX"
ZIP_MAX_UNCOMPRESSED_BYTES = 50 * 1024**3
INVALID_ARCHIVE = "The uploaded file is not a valid ZIP archive."
ARCHIVE_TOO_LARGE = "The archive is too large once extracted."
UNSAFE_ARCHIVE_ENTRY = "Archive entry '{name}' has an unsafe path."
AMBIGUOUS_ARCHIVE = "The archive must contain exactly one slide (for MRXS: the .mrxs file and its data folder)."


class SlideFileService:
    def __init__(self, storage: ObjectStorage, cache_dir: Path) -> None:
        self._storage = storage
        self._cache_dir = cache_dir

    def entry_path(self, slide: Slide) -> Path:
        """The file OpenSlide should open for `slide`, downloading the slide first if needed."""
        return find_slide_entry(self.materialize(slide.id, slide.storage_key))

    def materialize(self, slide_id: uuid.UUID, storage_key: str) -> Path:
        """Ensure the slide's files exist in the cache and return their directory.

        The download happens in a temporary directory that is renamed atomically, so concurrent workers
        never see a partial copy; if another worker finished first, its copy is kept.
        """
        slide_dir = self._cache_dir / str(slide_id)
        if slide_dir.is_dir():
            return slide_dir
        temp_dir = self._cache_dir / f"{slide_id}.tmp-{uuid.uuid4().hex}"
        archive = temp_dir.with_name(f"{temp_dir.name}{ZIP_SUFFIX}")
        temp_dir.mkdir(parents=True)
        try:
            filename = PurePosixPath(storage_key).name
            if filename.lower().endswith(ZIP_SUFFIX):
                self._storage.download_file(storage_key, archive)
                extract_zip_safely(archive, temp_dir)
            else:
                self._storage.download_file(storage_key, temp_dir / filename)
            try:
                os.replace(temp_dir, slide_dir)
            except OSError:
                if not slide_dir.is_dir():
                    raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            archive.unlink(missing_ok=True)
        return slide_dir


def find_slide_entry(directory: Path) -> Path:
    """A single file, or the one .mrxs/.vms file of a multi-file slide."""
    files = [path for path in directory.rglob("*") if path.is_file()]
    if len(files) == 1:
        return files[0]
    entries = [path for path in files if path.suffix.lower() in MULTI_FILE_SLIDE_SUFFIXES]
    if len(entries) == 1:
        return entries[0]
    raise InvalidInputError(AMBIGUOUS_ARCHIVE)


def extract_zip_safely(archive: Path, destination: Path) -> None:
    """Extract `archive` into `destination`, rejecting path traversal and oversized content."""
    try:
        with zipfile.ZipFile(archive) as zip_file:
            members = [
                member
                for member in zip_file.infolist()
                if not member.is_dir() and not member.filename.startswith(MACOS_ARCHIVE_METADATA_DIR)
            ]
            # ZipExtFile never yields more than the declared size, so the declared sizes bound the output.
            if sum(member.file_size for member in members) > ZIP_MAX_UNCOMPRESSED_BYTES:
                raise InvalidInputError(ARCHIVE_TOO_LARGE)
            root = destination.resolve()
            for member in members:
                # Some Windows tools (e.g. PowerShell 5.1 Compress-Archive) write backslash separators.
                target = (root / member.filename.replace("\\", "/")).resolve()
                if not target.is_relative_to(root):
                    raise InvalidInputError(UNSAFE_ARCHIVE_ENTRY.format(name=member.filename))
                target.parent.mkdir(parents=True, exist_ok=True)
                with zip_file.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
    except zipfile.BadZipFile as exc:
        raise InvalidInputError(INVALID_ARCHIVE) from exc
