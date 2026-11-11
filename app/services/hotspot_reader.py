"""How a hotspot is read: its review status, and the reannotation summary as the viewer sees it."""

from app.models.account import Account, Role
from app.models.hotspot import Hotspot
from app.models.tag import TagDimension
from app.schemas.hotspots import HotspotRead, HotspotStatus
from app.services.reannotation_read import summary_of

# They see every annotator's reannotation work.
RATING_MANAGER_ROLES = (Role.ADMIN, Role.ORG_ADMIN)


def hotspot_status(hotspot: Hotspot) -> HotspotStatus:
    """Reannotated, or tagged once both dimensions have a tag (a hotspot holds at most one per dimension)."""
    if hotspot.reannotated_at is not None:
        return HotspotStatus.REANNOTATED
    if all(hotspot.dimension_tag(dimension) is not None for dimension in TagDimension):
        return HotspotStatus.TO_REANNOTATE
    return HotspotStatus.UNTAGGED


def read_hotspot(hotspot: Hotspot, viewer: Account | None) -> HotspotRead:
    """`viewer` is None for work done on nobody's behalf."""
    read = HotspotRead.model_validate(hotspot)
    return read.model_copy(update={"status": hotspot_status(hotspot), "reannotation": summary_of(hotspot, viewer)})
