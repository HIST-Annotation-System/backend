import uuid
from collections.abc import Iterable, Sequence

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.account import Account
from app.models.hotspot import Hotspot
from app.models.tag import HotspotRating, HotspotTag, Tag, TagDimension
from app.repositories.hotspots import HotspotRepository
from app.repositories.tags import TagRepository
from app.schemas.tags import TagCreate, TagRead, TagUpdate
from app.services.hotspot_ratings import HotspotReader, team_tags

TAG_NOT_FOUND = "Tag {tag_id} does not exist."
TAGS_NOT_FOUND = "Tags do not exist: {ids}."
HOTSPOTS_NOT_FOUND = "Hotspots do not exist: {ids}."
TAG_NAME_TAKEN = "A tag named '{name}' already exists."
CORE_TAG_LOCKED = "'{name}' is a core tag: it can only be re-weighted."
CUSTOM_WEIGHT_TOO_HIGH = "Custom tags must weigh less than {cap}, the lightest non-zero core tag."
CORE_WEIGHT_CONFLICT = "Custom tags would outweigh the core tags; lower them below {cap} first: {names}."
DUPLICATE_DIMENSION = "A hotspot holds one {dimension} tag: add at most one at a time."


def custom_weight_cap(core_weights: Iterable[int]) -> int | None:
    """Custom tags must weigh less than this, so the core verdicts always dominate; None when no core tag
    weighs anything."""
    return min((weight for weight in core_weights if weight > 0), default=None)


class TagService:
    """The shared tag list, and the tags put on hotspots."""

    def __init__(self, tags: TagRepository, hotspots: HotspotRepository) -> None:
        self._tags = tags
        self._hotspots = hotspots

    def reader(self, viewer: Account) -> HotspotReader:
        return HotspotReader(self._tags.list_all(), viewer)

    def list_tags(self) -> list[TagRead]:
        return [TagRead.model_validate(tag) for tag in self._tags.list_all()]

    def create_tag(self, data: TagCreate, user: Account) -> TagRead:
        self._require_free_name(data.name)
        self._require_below_cap(data.weight)
        tag = self._tags.save(Tag(name=data.name, weight=data.weight, created_by_id=user.id))
        return TagRead.model_validate(tag)

    def update_tag(self, tag_id: int, data: TagUpdate) -> TagRead:
        tag = self._require(tag_id)
        if data.name is not None and data.name != tag.name:
            if tag.dimension is not None:
                raise ConflictError(CORE_TAG_LOCKED.format(name=tag.name))
            self._require_free_name(data.name)
            tag.name = data.name
        if data.weight is not None:
            if tag.dimension is None:
                self._require_below_cap(data.weight)
            else:
                self._require_core_weight_fits(tag, data.weight)
            tag.weight = data.weight
        return TagRead.model_validate(self._tags.save(tag))

    def delete_tag(self, tag_id: int) -> None:
        """Also takes the tag off every hotspot (ON DELETE CASCADE)."""
        tag = self._require(tag_id)
        if tag.dimension is not None:
            raise ConflictError(CORE_TAG_LOCKED.format(name=tag.name))
        self._tags.delete(tag)

    def tag_hotspots(
        self, hotspot_ids: Sequence[uuid.UUID], add: Sequence[int], remove: Sequence[int], user: Account
    ) -> list[Hotspot]:
        """Remove, then add tags. Custom tags are shared; a core tag is `user`'s rating of that dimension (adding
        one replaces their previous one, removing it clears it). A change of the team verdict sends a hotspot marked
        reannotated by hand back to the queue (one reannotated in layers keeps its verdict from the agreement)."""
        hotspots = self._require_hotspots(hotspot_ids)
        added = self._require_tags(add)
        self._require_tags(remove)
        dimensions = [tag.dimension for tag in added if tag.dimension is not None]
        for dimension in dimensions:
            if dimensions.count(dimension) > 1:
                raise InvalidInputError(DUPLICATE_DIMENSION.format(dimension=dimension))
        added_ids = {tag.id for tag in added}
        # A tag both removed and added is kept as it is.
        removed_ids = set(remove) - added_ids
        core_tags = [tag for tag in self._tags.list_all() if tag.dimension is not None]
        for hotspot in hotspots:
            verdict = team_tags(hotspot, core_tags)
            hotspot.tag_links = [link for link in hotspot.tag_links if link.tag_id not in removed_ids]
            present = {link.tag_id for link in hotspot.tag_links}
            hotspot.tag_links += [
                HotspotTag(tag=tag, added_by_id=user.id)
                for tag in added
                if tag.dimension is None and tag.id not in present
            ]
            self._rate(hotspot, user, [tag for tag in added if tag.dimension is not None], removed_ids)
            if team_tags(hotspot, core_tags) != verdict and not hotspot.layered:
                hotspot.reannotated_at, hotspot.reannotated_by_id = None, None
        self._hotspots.save_all(hotspots)
        return hotspots

    @staticmethod
    def _rate(hotspot: Hotspot, user: Account, verdicts: list[Tag], removed_ids: set[int]) -> None:
        """Sets `user`'s rating of the dimensions in `verdicts` and clears those of the removed tags; a rating
        left with neither dimension is dropped (the user leaves the hotspot's team)."""
        rating = hotspot.rating_of(user.id)
        if rating is None:
            if not verdicts:
                return
            rating = HotspotRating(account=user)
            hotspot.ratings.append(rating)
        for dimension in TagDimension:
            current = rating.tag(dimension)
            if current is not None and current.id in removed_ids:
                rating.set_tag(dimension, None)
        for tag in verdicts:
            if tag.dimension is not None:
                rating.set_tag(tag.dimension, tag)
        if rating.is_empty:
            hotspot.ratings.remove(rating)

    def _require(self, tag_id: int) -> Tag:
        tag = self._tags.get(tag_id)
        if tag is None:
            raise NotFoundError(TAG_NOT_FOUND.format(tag_id=tag_id))
        return tag

    def _require_tags(self, tag_ids: Sequence[int]) -> list[Tag]:
        tags = list(self._tags.list_by_ids(tag_ids)) if tag_ids else []
        missing = set(tag_ids) - {tag.id for tag in tags}
        if missing:
            raise NotFoundError(TAGS_NOT_FOUND.format(ids=", ".join(map(str, sorted(missing)))))
        return tags

    def _require_hotspots(self, hotspot_ids: Sequence[uuid.UUID]) -> list[Hotspot]:
        hotspots = list(self._hotspots.list_by_ids(hotspot_ids))
        missing = set(hotspot_ids) - {hotspot.id for hotspot in hotspots}
        if missing:
            raise NotFoundError(HOTSPOTS_NOT_FOUND.format(ids=", ".join(map(str, sorted(missing)))))
        return hotspots

    def _require_free_name(self, name: str) -> None:
        if self._tags.get_by_name(name) is not None:
            raise ConflictError(TAG_NAME_TAKEN.format(name=name))

    def _require_below_cap(self, weight: int) -> None:
        cap = custom_weight_cap(tag.weight for tag in self._tags.list_all() if tag.dimension is not None)
        if cap is not None and weight >= cap:
            raise InvalidInputError(CUSTOM_WEIGHT_TOO_HIGH.format(cap=cap))

    def _require_core_weight_fits(self, core_tag: Tag, weight: int) -> None:
        tags = self._tags.list_all()
        cap = custom_weight_cap(
            weight if tag is core_tag else tag.weight for tag in tags if tag.dimension is not None
        )
        too_heavy = [tag.name for tag in tags if tag.dimension is None and cap is not None and tag.weight >= cap]
        if too_heavy:
            raise ConflictError(CORE_WEIGHT_CONFLICT.format(cap=cap, names=", ".join(too_heavy)))
