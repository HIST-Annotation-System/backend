import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.models.account import Role
from app.models.slide import SlideStatus
from app.models.tag import TagDimension


class HotspotStatusCounts(BaseModel):
    """Hotspots per review stage (see HotspotStatus)."""

    untagged: int = 0
    to_reannotate: int = 0
    reannotated: int = 0


class FolderProgress(HotspotStatusCounts):
    id: uuid.UUID
    name: str


class AnnotatorLoad(BaseModel):
    account_id: int
    username: str
    full_name: str | None
    role: Role
    # Hotspots not marked reannotated yet that hold at least one of the account's annotations.
    in_progress: int
    # Hotspots the account marked reannotated (and that still are).
    finished: int
    annotations: int
    annotations_last_7_days: int
    # Importance/accuracy verdicts and custom tags the account put on hotspots.
    verdicts: int
    last_active_at: datetime | None


class CellTypeUsage(BaseModel):
    id: int
    name: str
    annotations: int


class TagUsage(BaseModel):
    id: int
    name: str
    dimension: TagDimension | None
    hotspots: int


class DailyActivity(BaseModel):
    day: date
    annotations: int = 0
    reannotated: int = 0
    hotspots_created: int = 0


class SlideStatusCount(BaseModel):
    status: SlideStatus
    count: int


class AccountCounts(BaseModel):
    admins: int
    annotators: int
    # Accounts whose owner has not logged in and set a password yet.
    pending_first_login: int


class StatsRead(BaseModel):
    generated_at: datetime
    hotspots: HotspotStatusCounts
    hotspot_folders: int
    annotations: int
    # Annotations drawn before authors were recorded, or by deleted accounts.
    unattributed_annotations: int
    # Median time from a hotspot's creation to it being marked reannotated; None until one is.
    median_hours_to_reannotate: float | None
    slides: list[SlideStatusCount]
    slides_total_bytes: int
    accounts: AccountCounts
    annotators: list[AnnotatorLoad]
    cell_types: list[CellTypeUsage]
    tags: list[TagUsage]
    folders: list[FolderProgress]
    # One entry per day of the window, oldest first (days without activity included).
    activity: list[DailyActivity]
