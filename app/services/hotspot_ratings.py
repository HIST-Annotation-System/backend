"""Per-annotator ratings of hotspots, the team verdict derived from them, and what each user may see of it.

Team verdict of a dimension (importance, accuracy): the average weight of the ratings that set it. It is shown as the
core tag whose weight is nearest to that average (a tie goes to the heavier tag: the more important / the one flagging
an error, so a split team never hides a problem); sorting by the dimension uses the exact average. The score is the sum
of the custom tags' weights and of each dimension's average rounded half up to a whole number (so 0.5 counts as 1).
"""

import math
from collections.abc import Iterable, Sequence
from statistics import fmean

from app.models.account import Account, Role
from app.models.hotspot import Hotspot
from app.models.tag import HotspotRating, Tag, TagDimension
from app.schemas.hotspots import HotspotRead, HotspotSort, HotspotStatus, RatingRead
from app.schemas.tags import TagRead

# They see every rating and the team of every hotspot.
RATING_MANAGER_ROLES = (Role.ADMIN, Role.ORG_ADMIN)


def round_half_up(value: float) -> int:
    return math.floor(value + 0.5)


def dimension_average(ratings: Iterable[HotspotRating], dimension: TagDimension) -> float | None:
    weights = [tag.weight for tag in (rating.tag(dimension) for rating in ratings) if tag is not None]
    return fmean(weights) if weights else None


def nearest_core_tag(core_tags: Sequence[Tag], dimension: TagDimension, average: float) -> Tag | None:
    candidates = [tag for tag in core_tags if tag.dimension == dimension]
    return min(candidates, key=lambda tag: (abs(tag.weight - average), -tag.weight), default=None)


def team_tags(hotspot: Hotspot, core_tags: Sequence[Tag]) -> list[Tag]:
    """The team verdict as core tags, in TagDimension order (a dimension nobody rated has none)."""
    tags = []
    for dimension in TagDimension:
        average = dimension_average(hotspot.ratings, dimension)
        tag = None if average is None else nearest_core_tag(core_tags, dimension, average)
        if tag is not None:
            tags.append(tag)
    return tags


def hotspot_status(hotspot: Hotspot) -> HotspotStatus:
    """From every rating, whoever asks: reannotated, or tagged once the ratings set both dimensions."""
    if hotspot.reannotated_at is not None:
        return HotspotStatus.REANNOTATED
    if all(dimension_average(hotspot.ratings, dimension) is not None for dimension in TagDimension):
        return HotspotStatus.TO_REANNOTATE
    return HotspotStatus.UNTAGGED


def _rating_read(rating: HotspotRating) -> RatingRead:
    profile = rating.account.profile
    full_name = " ".join(part for part in (profile.first_name, profile.last_name) if part) if profile else ""
    return RatingRead(
        account_id=rating.account_id,
        username=rating.account.username,
        full_name=full_name or None,
        importance=TagRead.model_validate(rating.importance_tag) if rating.importance_tag else None,
        accuracy=TagRead.model_validate(rating.accuracy_tag) if rating.accuracy_tag else None,
        updated_at=rating.updated_at,
    )


class HotspotReader:
    """Hotspots as `viewer` sees them; None for work done on nobody's behalf (sees everything)."""

    def __init__(self, core_tags: Sequence[Tag], viewer: Account | None) -> None:
        self._core_tags = [tag for tag in core_tags if tag.dimension is not None]
        self._viewer = viewer

    def sees_team(self, hotspot: Hotspot) -> bool:
        viewer = self._viewer
        return viewer is None or viewer.role in RATING_MANAGER_ROLES or hotspot.rating_of(viewer.id) is not None

    def rated_by_viewer(self, hotspot: Hotspot) -> bool:
        own = hotspot.rating_of(self._viewer.id) if self._viewer is not None else None
        return own is not None and own.importance_tag is not None and own.accuracy_tag is not None

    def visible_tags(self, hotspot: Hotspot) -> list[Tag]:
        verdict = team_tags(hotspot, self._core_tags) if self.sees_team(hotspot) else []
        return verdict + hotspot.custom_tags

    def score(self, hotspot: Hotspot) -> int | None:
        """None when the viewer may not see the team verdict yet (sorted last, like a missing value)."""
        if not self.sees_team(hotspot):
            return None
        averages = (dimension_average(hotspot.ratings, dimension) for dimension in TagDimension)
        verdict = sum(round_half_up(average) for average in averages if average is not None)
        return verdict + sum(tag.weight for tag in hotspot.custom_tags)

    def sort_value(self, hotspot: Hotspot, sort: HotspotSort) -> float | None:
        if sort == HotspotSort.RATING_COUNT:
            return len(hotspot.ratings)
        if sort == HotspotSort.SCORE:
            return self.score(hotspot)
        if not self.sees_team(hotspot):
            return None
        return dimension_average(hotspot.ratings, TagDimension(sort.value))

    def read(self, hotspot: Hotspot) -> HotspotRead:
        read = HotspotRead.model_validate(hotspot)
        sees_team = self.sees_team(hotspot)
        own = hotspot.rating_of(self._viewer.id) if self._viewer is not None else None
        latest = max(hotspot.ratings, key=lambda rating: rating.updated_at, default=None)
        visible_custom = sum(tag.weight for tag in hotspot.custom_tags)
        return read.model_copy(
            update={
                "tags": [TagRead.model_validate(tag) for tag in self.visible_tags(hotspot)],
                "score": self.score(hotspot) if sees_team else visible_custom,
                "assessed_by": latest.account.username if sees_team and latest is not None else None,
                "status": hotspot_status(hotspot),
                "rating_count": len(hotspot.ratings),
                "my_rating": _rating_read(own) if own is not None else None,
                "ratings": (
                    [_rating_read(rating) for rating in sorted(hotspot.ratings, key=lambda r: r.created_at)]
                    if sees_team
                    else None
                ),
            }
        )
