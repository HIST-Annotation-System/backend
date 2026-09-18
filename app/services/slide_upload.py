import re
import uuid
from collections.abc import Sequence
from pathlib import PurePosixPath

from app.exceptions.errors import ConflictError, InvalidInputError
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.schemas.upload import PresignedUrlsResponse, RefreshUrlsResponse, StartUploadResponse
from app.services.slide_files import list_folder_members
from app.services.slide_formats import validate_slide_members, validate_upload_name
from app.services.slides import SLIDE_OBJECTS_PREFIX, SLIDE_OBJECTS_ROOT, require_slide
from app.services.storage import ObjectStorage

SLIDE_OBJECT_KEY = SLIDE_OBJECTS_PREFIX + "{filename}"
UNSAFE_FILENAME_CHARACTERS = r"[^A-Za-z0-9._-]+"
DEFAULT_FILENAME_STEM = "slide"
SLIDE_NOT_UPLOADING = "Slide {slide_id} is not accepting uploads."
ALREADY_IMPORTED = "This file has already been imported as slide '{name}'."
OVERLAPS_IMPORTED = "'{key}' overlaps the files of slide '{name}'."
RESERVED_STORAGE_PREFIX = "Objects under {prefix} are managed by the application and can't be imported."


class SlideUploadService:
    """Browser uploads: a slide record is created first, then the file goes straight to object storage."""

    def __init__(self, slides: SlideRepository, storage: ObjectStorage) -> None:
        self._slides = slides
        self._storage = storage

    def start_upload(self, filename: str) -> StartUploadResponse:
        original = PurePosixPath(filename.replace("\\", "/")).name
        stem, suffix = validate_upload_name(original)

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
        slide = self._uploading_slide(slide_id)
        self._storage.complete_multipart_upload(slide.storage_key, upload_id, etags)
        slide.file_size_bytes = self._storage.head_size(slide.storage_key)
        slide.status = SlideStatus.PROCESSING
        self._slides.save(slide)

    def cancel_upload(self, slide_id: uuid.UUID) -> None:
        """Aborts an in-progress browser upload and drops the slide record created for it."""
        slide = self._uploading_slide(slide_id)
        self._storage.abort_multipart_uploads(slide.storage_key)
        self._slides.delete(slide)

    def import_from_storage(self, key: str) -> uuid.UUID:
        """Registers an object - or an extracted multi-file slide folder, a key ending in "/" - already
        sitting in the bucket as a new slide, skipping the upload step.
        """
        if key.startswith(SLIDE_OBJECTS_ROOT):
            raise InvalidInputError(RESERVED_STORAGE_PREFIX.format(prefix=SLIDE_OBJECTS_ROOT))
        # Deleting a slide deletes its objects, so no two slides may share any.
        existing = self._slides.find_overlapping_storage_key(key)
        if existing is not None:
            message = ALREADY_IMPORTED if existing.storage_key == key else OVERLAPS_IMPORTED
            raise ConflictError(message.format(key=key, name=existing.name))

        original = PurePosixPath(key).name
        if key.endswith("/"):
            members = list_folder_members(self._storage, key)
            validate_slide_members(list(members))
            name, file_size = original, sum(member.size for member in members.values())
        else:
            stem, _ = validate_upload_name(original)
            name, file_size = stem or original, self._storage.head_size(key)

        slide_id = uuid.uuid4()
        self._slides.save(
            Slide(
                id=slide_id,
                name=name,
                original_filename=original,
                storage_key=key,
                file_size_bytes=file_size,
                status=SlideStatus.PROCESSING,
            )
        )
        return slide_id

    def _uploading_slide(self, slide_id: uuid.UUID) -> Slide:
        slide = require_slide(self._slides, slide_id)
        if slide.status != SlideStatus.UPLOADING:
            raise ConflictError(SLIDE_NOT_UPLOADING.format(slide_id=slide_id))
        return slide
