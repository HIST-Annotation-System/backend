import re
import uuid
from collections.abc import Sequence
from pathlib import PurePosixPath

from app.exceptions.errors import ConflictError, InvalidInputError
from app.models.slide import Slide, SlideStatus
from app.repositories.slides import SlideRepository
from app.schemas.upload import PresignedUrlsResponse, RefreshUrlsResponse, StartUploadResponse
from app.services.slide_files import MRXS_SUFFIX, ZIP_SUFFIX
from app.services.slides import require_slide
from app.services.storage import ObjectStorage

SLIDE_OBJECT_KEY = "slides/{slide_id}/{filename}"
ALLOWED_UPLOAD_SUFFIXES = frozenset(
    {".svs", ".tif", ".tiff", ".ndpi", ".scn", ".bif", ".svslide", ".jpg", ".jpeg", ".png", ".bmp", ZIP_SUFFIX}
)
UNSAFE_FILENAME_CHARACTERS = r"[^A-Za-z0-9._-]+"
DEFAULT_FILENAME_STEM = "slide"
UNSUPPORTED_FILE_TYPE = "Unsupported file type '{suffix}'."
MRXS_ZIP_REQUIRED = (
    "MRXS slides consist of a .mrxs file and a data folder - upload them together as a single .zip archive."
)
SLIDE_NOT_UPLOADING = "Slide {slide_id} is not accepting uploads."
ALREADY_IMPORTED = "This file has already been imported as slide '{name}'."


class SlideUploadService:
    """Browser uploads: a slide record is created first, then the file goes straight to object storage."""

    def __init__(self, slides: SlideRepository, storage: ObjectStorage) -> None:
        self._slides = slides
        self._storage = storage

    def start_upload(self, filename: str) -> StartUploadResponse:
        original = PurePosixPath(filename.replace("\\", "/")).name
        stem, suffix = PurePosixPath(original).stem, PurePosixPath(original).suffix.lower()
        if suffix == MRXS_SUFFIX:
            raise InvalidInputError(MRXS_ZIP_REQUIRED)
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise InvalidInputError(UNSUPPORTED_FILE_TYPE.format(suffix=suffix or original))

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

    def import_from_storage(self, key: str) -> uuid.UUID:
        """Registers an object already sitting in the bucket as a new slide, skipping the upload step."""
        original = PurePosixPath(key).name
        if not original:
            raise InvalidInputError(UNSUPPORTED_FILE_TYPE.format(suffix=key))
        stem, suffix = PurePosixPath(original).stem, PurePosixPath(original).suffix.lower()
        if suffix == MRXS_SUFFIX:
            raise InvalidInputError(MRXS_ZIP_REQUIRED)
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise InvalidInputError(UNSUPPORTED_FILE_TYPE.format(suffix=suffix or original))

        existing = self._slides.get_by_storage_key(key)
        if existing is not None:
            raise ConflictError(ALREADY_IMPORTED.format(name=existing.name))

        slide_id = uuid.uuid4()
        self._slides.save(
            Slide(
                id=slide_id,
                name=stem or original,
                original_filename=original,
                storage_key=key,
                file_size_bytes=self._storage.head_size(key),
                status=SlideStatus.PROCESSING,
            )
        )
        return slide_id

    def _uploading_slide(self, slide_id: uuid.UUID) -> Slide:
        slide = require_slide(self._slides, slide_id)
        if slide.status != SlideStatus.UPLOADING:
            raise ConflictError(SLIDE_NOT_UPLOADING.format(slide_id=slide_id))
        return slide
