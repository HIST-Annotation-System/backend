import uuid
from pathlib import PurePath

from sqlalchemy.exc import IntegrityError

from app.core.constants import NAME_MAX_LENGTH
from app.exceptions.errors import ConflictError, NotFoundError
from app.models.hotspot import Hotspot, HotspotFolder
from app.repositories.hotspots import HotspotRepository
from app.repositories.slides import SlideRepository
from app.schemas.hotspots import HotspotCreate, HotspotFolderRead, HotspotRead, HotspotUpdate
from app.services.hotspot_images import EXTENSIONS, MEDIA_TYPES, EncodedImage, decode_upload, encode_captured
from app.services.slide_analysis import MEDIA_TYPE_JPEG
from app.services.slides import SlideService, require_slide
from app.services.storage import ObjectStorage

# Outside "slides/": a hotspot keeps its images when its source slide is deleted.
HOTSPOT_OBJECTS_PREFIX = "hotspots/{hotspot_id}/"
HOTSPOT_IMAGE_KEY = HOTSPOT_OBJECTS_PREFIX + "image.{extension}"
HOTSPOT_THUMBNAIL_KEY = HOTSPOT_OBJECTS_PREFIX + "thumbnail.jpg"
HOTSPOT_IMAGE_MAX_SIZE = 2048
UPLOADED_IMAGE_DEFAULT_NAME = "Obraz"
FOLDER_NOT_FOUND = "Hotspot folder {folder_id} does not exist."
FOLDER_NAME_TAKEN = "A hotspot folder named '{name}' already exists."
HOTSPOT_NOT_FOUND = "Hotspot {hotspot_id} does not exist."


def require_hotspot(hotspots: HotspotRepository, hotspot_id: uuid.UUID) -> Hotspot:
    hotspot = hotspots.get(hotspot_id)
    if hotspot is None:
        raise NotFoundError(HOTSPOT_NOT_FOUND.format(hotspot_id=hotspot_id))
    return hotspot


class HotspotService:
    """The hotspot catalog: folders of square slide snapshots, and the snapshot images themselves."""

    def __init__(
        self,
        hotspots: HotspotRepository,
        slides: SlideRepository,
        slide_service: SlideService,
        storage: ObjectStorage,
    ) -> None:
        self._hotspots = hotspots
        self._slides = slides
        self._slide_service = slide_service
        self._storage = storage

    def list_folders(self) -> list[HotspotFolderRead]:
        return [
            self._folder_read(folder, count) for folder, count in self._hotspots.list_folders_with_counts()
        ]

    def create_folder(self, name: str) -> HotspotFolderRead:
        self._require_free_name(name)
        folder = self._save_folder(HotspotFolder(name=name))
        return self._folder_read(folder, 0)

    def rename_folder(self, folder_id: uuid.UUID, name: str) -> HotspotFolderRead:
        folder = self._require_folder(folder_id)
        if name != folder.name:
            self._require_free_name(name)
            folder.name = name
            folder = self._save_folder(folder)
        return self._folder_read(folder, self._hotspots.count_in_folder(folder_id))

    def delete_folder(self, folder_id: uuid.UUID) -> None:
        folder = self._require_folder(folder_id)
        hotspot_ids = [hotspot.id for hotspot in self._hotspots.list_hotspots(folder_id)]
        self._hotspots.delete(folder)
        for hotspot_id in hotspot_ids:
            self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot_id))

    def list_hotspots(self, folder_id: uuid.UUID | None) -> list[HotspotRead]:
        return [HotspotRead.model_validate(item) for item in self._hotspots.list_hotspots(folder_id)]

    def get_hotspot(self, hotspot_id: uuid.UUID) -> HotspotRead:
        return HotspotRead.model_validate(require_hotspot(self._hotspots, hotspot_id))

    def create_hotspot(self, data: HotspotCreate) -> HotspotRead:
        slide = require_slide(self._slides, data.slide_id, ready=True)
        self._require_folder(data.folder_id)
        image = encode_captured(
            self._slide_service.read_square(slide.id, data.x, data.y, data.size, HOTSPOT_IMAGE_MAX_SIZE)
        )
        hotspot = Hotspot(
            id=uuid.uuid4(),
            folder_id=data.folder_id,
            name=data.name,
            slide_id=slide.id,
            slide_name=slide.name,
            x=data.x,
            y=data.y,
            size=data.size,
            mpp=slide.mpp_x * data.size / image.width if slide.mpp_x else None,
        )
        return self._store(hotspot, image)

    def upload_image(self, folder_id: uuid.UUID, filename: str, data: bytes) -> HotspotRead:
        """A hotspot from an uploaded PNG/JPEG, named after the file."""
        self._require_folder(folder_id)
        image = decode_upload(data, filename)
        name = PurePath(filename).stem.strip()[:NAME_MAX_LENGTH] or UPLOADED_IMAGE_DEFAULT_NAME
        return self._store(Hotspot(id=uuid.uuid4(), folder_id=folder_id, name=name), image)

    def update_hotspot(self, hotspot_id: uuid.UUID, data: HotspotUpdate) -> HotspotRead:
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        if data.folder_id is not None:
            self._require_folder(data.folder_id)
            hotspot.folder_id = data.folder_id
        if data.name is not None:
            hotspot.name = data.name
        return HotspotRead.model_validate(self._hotspots.save(hotspot))

    def delete_hotspot(self, hotspot_id: uuid.UUID) -> None:
        self._hotspots.delete(require_hotspot(self._hotspots, hotspot_id))
        self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot_id))

    def get_image(self, hotspot_id: uuid.UUID) -> tuple[bytes, str]:
        """The image bytes and their media type."""
        hotspot = require_hotspot(self._hotspots, hotspot_id)
        key = HOTSPOT_IMAGE_KEY.format(hotspot_id=hotspot_id, extension=EXTENSIONS[hotspot.image_type])
        return self._storage.get_bytes(key), MEDIA_TYPES[hotspot.image_type]

    def get_thumbnail(self, hotspot_id: uuid.UUID) -> bytes:
        require_hotspot(self._hotspots, hotspot_id)
        return self._storage.get_bytes(HOTSPOT_THUMBNAIL_KEY.format(hotspot_id=hotspot_id))

    def _store(self, hotspot: Hotspot, image: EncodedImage) -> HotspotRead:
        """Upload the images, then save the row (dropping the images again if that fails)."""
        hotspot.image_width, hotspot.image_height, hotspot.image_type = image.width, image.height, image.image_type
        image_key = HOTSPOT_IMAGE_KEY.format(hotspot_id=hotspot.id, extension=EXTENSIONS[image.image_type])
        self._storage.put_bytes(image_key, image.data, MEDIA_TYPES[image.image_type])
        self._storage.put_bytes(HOTSPOT_THUMBNAIL_KEY.format(hotspot_id=hotspot.id), image.thumbnail, MEDIA_TYPE_JPEG)
        try:
            return HotspotRead.model_validate(self._hotspots.save(hotspot))
        except Exception:
            self._storage.delete_prefix(HOTSPOT_OBJECTS_PREFIX.format(hotspot_id=hotspot.id))
            raise

    def _folder_read(self, folder: HotspotFolder, hotspot_count: int) -> HotspotFolderRead:
        return HotspotFolderRead(
            id=folder.id, name=folder.name, hotspot_count=hotspot_count, created_at=folder.created_at
        )

    def _require_folder(self, folder_id: uuid.UUID) -> HotspotFolder:
        folder = self._hotspots.get_folder(folder_id)
        if folder is None:
            raise NotFoundError(FOLDER_NOT_FOUND.format(folder_id=folder_id))
        return folder

    def _require_free_name(self, name: str) -> None:
        if self._hotspots.get_folder_by_name(name) is not None:
            raise ConflictError(FOLDER_NAME_TAKEN.format(name=name))

    def _save_folder(self, folder: HotspotFolder) -> HotspotFolder:
        # The unique constraint still guards against a concurrent request taking the same name.
        try:
            return self._hotspots.save(folder)
        except IntegrityError as exc:
            raise ConflictError(FOLDER_NAME_TAKEN.format(name=folder.name)) from exc
