import re
import uuid
from collections.abc import Sequence
from pathlib import PurePosixPath

from sqlalchemy.exc import IntegrityError

from app.exceptions.errors import ConflictError, InvalidInputError
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.schemas.upload import (
    PresignedUrlsResponse,
    RefreshUrlsResponse,
    StartUploadResponse,
    UploadedPart,
    UploadedPartsResponse,
)
from app.services.slide_files import list_folder_members
from app.services.slide_formats import validate_slide_members, validate_upload_name
from app.services.slides import SLIDE_OBJECTS_PREFIX, SLIDE_OBJECTS_ROOT, require_slide
from app.services.storage import ObjectStorage

SLIDE_OBJECT_KEY = SLIDE_OBJECTS_PREFIX + "{filename}"
UNSAFE_FILENAME_CHARACTERS = r"[^A-Za-z0-9._-]+"
DEFAULT_FILENAME_STEM = "slide"
SLIDE_NOT_UPLOADING = "Slide {slide_id} is not accepting uploads."
SLIDE_NOT_UPLOADING_CODE = "slide_not_uploading"
OVERLAPS_IMPORTED = "'{key}' overlaps the files of slide '{name}'."
DUPLICATE_SLIDE = "This file is already in the library as slide '{name}'."
DUPLICATE_SLIDE_CODE = "duplicate_slide"
RESERVED_STORAGE_PREFIX = "Objects under {prefix} are managed by the application and can't be imported."


class SlideUploadService:
    """Browser uploads: a slide record is created first, then the file goes straight to object storage."""

    def __init__(self, slides: SlideRepository, storage: ObjectStorage) -> None:
        self._slides = slides
        self._storage = storage

    def start_upload(
        self,
        filename: str,
        uploaded_by_id: int | None = None,
        file_size: int | None = None,
        allow_duplicate: bool = False,
    ) -> StartUploadResponse:
        original = PurePosixPath(filename.replace("\\", "/")).name
        stem, suffix = validate_upload_name(original)
        if file_size is not None and not allow_duplicate:
            # Serialized like imports, so two admins starting the same file at once can't both pass.
            self._slides.lock_imports()
            duplicate = self._slides.find_duplicate(original, file_size)
            if duplicate is not None:
                raise ConflictError(DUPLICATE_SLIDE.format(name=duplicate.name), DUPLICATE_SLIDE_CODE)

        slide_id = uuid.uuid4()
        safe_stem = re.sub(UNSAFE_FILENAME_CHARACTERS, "_", stem).strip("._")[:200] or DEFAULT_FILENAME_STEM
        storage_key = SLIDE_OBJECT_KEY.format(slide_id=slide_id, filename=f"{safe_stem}{suffix}")
        upload_id = self._storage.create_multipart_upload(storage_key)
        self._slides.save(
            Slide(
                id=slide_id,
                name=stem or safe_stem,
                original_filename=original,
                storage_key=storage_key,
                status=SlideStatus.UPLOADING,
                # The declared size, replaced by the stored object's real size on completion.
                file_size_bytes=file_size,
                uploaded_by_id=uploaded_by_id,
            )
        )
        return StartUploadResponse(slide_id=slide_id, upload_id=upload_id)

    def presign_parts(self, slide_id: uuid.UUID, upload_id: str, total_parts: int) -> PresignedUrlsResponse:
        parts = range(1, total_parts + 1)
        urls = self._storage.get_presigned_part_urls(self._uploading_slide(slide_id).storage_key, upload_id, parts)
        return PresignedUrlsResponse(urls=[urls[part] for part in parts])

    def refresh_part_urls(self, slide_id: uuid.UUID, upload_id: str, parts: Sequence[int]) -> RefreshUrlsResponse:
        key = self._uploading_slide(slide_id).storage_key
        return RefreshUrlsResponse(urls=self._storage.get_presigned_part_urls(key, upload_id, parts))

    def complete_upload(self, slide_id: uuid.UUID, upload_id: str, etags: dict[int, str]) -> None:
        # Row-locked: a concurrent cancel/delete either waits for this or makes it fail with 404.
        slide = self._uploading_slide(slide_id, for_update=True)
        self._storage.complete_multipart_upload(slide.storage_key, upload_id, etags)
        slide.file_size_bytes = self._storage.head_size(slide.storage_key)
        slide.status, slide.ingest_progress = SlideStatus.PROCESSING, None
        self._slides.save(slide)

    def report_progress(self, slide_id: uuid.UUID, percent: int) -> None:
        """Upload progress from the uploading browser, so every other client can show it too."""
        slide = self._uploading_slide(slide_id)
        slide.ingest_progress = percent
        self._slides.save(slide)

    def list_uploaded_parts(self, slide_id: uuid.UUID, upload_id: str) -> UploadedPartsResponse:
        key = self._uploading_slide(slide_id).storage_key
        return UploadedPartsResponse(
            parts=[
                UploadedPart(PartNumber=part.number, ETag=part.etag, Size=part.size)
                for part in self._storage.list_parts(key, upload_id)
            ]
        )

    def cancel_upload(self, slide_id: uuid.UUID) -> None:
        """Aborts an in-progress browser upload and drops the slide record created for it."""
        slide = self._uploading_slide(slide_id, for_update=True)
        self._storage.abort_multipart_uploads(slide.storage_key)
        self._slides.delete(slide)

    def import_from_storage(self, key: str, uploaded_by_id: int | None = None) -> tuple[uuid.UUID, bool]:
        """Registers an object - or an extracted multi-file slide folder, a key ending in "/" - already
        sitting in the bucket as a new slide, skipping the upload step.

        Idempotent: importing the same key again (also by two admins at once) returns the existing
        slide and False instead of creating a second one.
        """
        if key.startswith(SLIDE_OBJECTS_ROOT):
            raise InvalidInputError(RESERVED_STORAGE_PREFIX.format(prefix=SLIDE_OBJECTS_ROOT))
        self._slides.lock_imports()
        # Deleting a slide deletes its objects, so no two slides may share any.
        existing = self._slides.find_overlapping_storage_key(key)
        if existing is not None:
            if existing.storage_key == key:
                return existing.id, False
            raise ConflictError(OVERLAPS_IMPORTED.format(key=key, name=existing.name))

        original = PurePosixPath(key).name
        if key.endswith("/"):
            members = list_folder_members(self._storage, key)
            validate_slide_members(list(members))
            name, file_size = original, sum(member.size for member in members.values())
        else:
            stem, _ = validate_upload_name(original)
            name, file_size = stem or original, self._storage.head_size(key)

        slide_id = uuid.uuid4()
        try:
            self._slides.save(
                Slide(
                    id=slide_id,
                    name=name,
                    original_filename=original,
                    storage_key=key,
                    file_size_bytes=file_size,
                    status=SlideStatus.PROCESSING,
                    uploaded_by_id=uploaded_by_id,
                )
            )
        except IntegrityError:
            # The unique storage key caught a concurrent import the lock didn't (e.g. an older API instance).
            self._slides.rollback()
            existing = self._slides.get_by_storage_key(key)
            if existing is None:
                raise
            return existing.id, False
        return slide_id, True

    def _uploading_slide(self, slide_id: uuid.UUID, *, for_update: bool = False) -> Slide:
        slide = require_slide(self._slides, slide_id, for_update=for_update)
        if slide.status != SlideStatus.UPLOADING:
            raise ConflictError(SLIDE_NOT_UPLOADING.format(slide_id=slide_id), SLIDE_NOT_UPLOADING_CODE)
        return slide
