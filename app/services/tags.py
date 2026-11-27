import uuid
from collections.abc import Iterable, Sequence

from app.exceptions.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.models.account import Account, Role
from app.models.hotspot import Hotspot
from app.models.tag import HotspotTag, Tag, TagDimension
from app.repositories.hotspots import HotspotRepository
from app.repositories.tags import TagRepository
from app.schemas.tags import TagCreate, TagRead, TagUpdate

TAG_NOT_FOUND = "Tag {tag_id} does not exist."
TAGS_NOT_FOUND = "Tags do not exist: {ids}."
HOTSPOTS_NOT_FOUND = "Hotspots do not exist: {ids}."
TAG_NAME_TAKEN = "A tag named '{name}' already exists."
CORE_TAG_LOCKED = "'{name}' is a core tag: it can only be re-weighted."
CUSTOM_WEIGHT_TOO_HIGH = "Custom tags must weigh less than {cap}, the lightest non-zero core tag."
CORE_WEIGHT_CONFLICT = "Custom tags would outweigh the core tags; lower them below {cap} first: {names}."
DUPLICATE_DIMENSION = "A hotspot holds one {dimension} tag: add at most one at a time."
NOT_YOUR_TAG = "Not enough privileges to change the tag '{name}'."
NO_ORGANIZATION = "Account has no organization."
FOREIGN_TAG = "Tag '{name}' belongs to another organization than hotspot '{hotspot}'."


def custom_weight_cap(core_weights: Iterable[int]) -> int | None:
    """Custom tags must weigh less than this, so the core verdicts always dominate; None when no core tag
    weighs anything."""
    return min((weight for weight in core_weights if weight > 0), default=None)


def _verdict(hotspot: Hotspot) -> tuple[Tag | None, ...]:
    return tuple(hotspot.dimension_tag(dimension) for dimension in TagDimension)


class TagService:
    """The tag list (global tags and an organization's own), and the tags put on hotspots.

    The repositories are scoped to the user's organization, so another organization's tags and hotspots look
    missing. Roles are checked here: who may change a tag depends on whose it is."""

    def __init__(self, tags: TagRepository, hotspots: HotspotRepository) -> None:
        self._tags = tags
        self._hotspots = hotspots

    def list_tags(self) -> list[TagRead]:
        return [TagRead.model_validate(tag) for tag in self._tags.list_all()]

    def create_tag(self, data: TagCreate, user: Account) -> TagRead:
        """The system admin's tags are global; anyone else's belong to their organization."""
        if user.role == Role.ADMIN:
            organization_id = None
        elif user.organization_id is None:
            raise ForbiddenError(NO_ORGANIZATION)
        else:
            organization_id = user.organization_id
        self._require_free_name(data.name, organization_id)
        self._require_below_cap(data.weight)
        tag = self._tags.save(
            Tag(name=data.name, weight=data.weight, organization_id=organization_id, created_by_id=user.id)
        )
        return TagRead.model_validate(tag)

    def update_tag(self, tag_id: int, data: TagUpdate, user: Account) -> TagRead:
        tag = self._require_changeable(tag_id, user)
        if data.name is not None and data.name != tag.name:
            if tag.dimension is not None:
                raise ConflictError(CORE_TAG_LOCKED.format(name=tag.name))
            self._require_free_name(data.name, tag.organization_id)
            tag.name = data.name
        if data.weight is not None:
            if tag.dimension is None:
                self._require_below_cap(data.weight)
            else:
                self._require_core_weight_fits(tag, data.weight)
            tag.weight = data.weight
        return TagRead.model_validate(self._tags.save(tag))

    def delete_tag(self, tag_id: int, user: Account) -> None:
        """Also takes the tag off every hotspot (ON DELETE CASCADE)."""
        tag = self._require_changeable(tag_id, user)
        if tag.dimension is not None:
            raise ConflictError(CORE_TAG_LOCKED.format(name=tag.name))
        self._tags.delete(tag)

    def tag_hotspots(
        self, hotspot_ids: Sequence[uuid.UUID], add: Sequence[int], remove: Sequence[int], user: Account
    ) -> list[Hotspot]:
        """Remove, then add tags. A core tag replaces the hotspot's tag of the same dimension; a changed verdict
        sends a hotspot marked reannotated by hand back to the queue (one reannotated in layers keeps its verdict
        from the agreement)."""
        hotspots = self._require_hotspots(hotspot_ids)
        added = self._require_tags(add)
        self._require_tags(remove)
        dimensions = [tag.dimension for tag in added if tag.dimension is not None]
        for dimension in dimensions:
            if dimensions.count(dimension) > 1:
                raise InvalidInputError(DUPLICATE_DIMENSION.format(dimension=dimension))
        # Only the system admin sees tags of several organizations, and must not mix them up either.
        organizations = self._hotspots.folder_organizations({hotspot.folder_id for hotspot in hotspots})
        for hotspot in hotspots:
            for tag in added:
                if tag.organization_id not in (None, organizations[hotspot.folder_id]):
                    raise InvalidInputError(FOREIGN_TAG.format(name=tag.name, hotspot=hotspot.name))
        added_ids = {tag.id for tag in added}
        # A tag both removed and added is kept as it is.
        removed_ids = set(remove) - added_ids
        for hotspot in hotspots:
            verdict = _verdict(hotspot)
            hotspot.tag_links = [
                link
                for link in hotspot.tag_links
                if link.tag_id not in removed_ids
                and (link.tag.dimension not in dimensions or link.tag_id in added_ids)
            ]
            present = {link.tag_id for link in hotspot.tag_links}
            hotspot.tag_links += [
                HotspotTag(tag=tag, added_by_id=user.id) for tag in added if tag.id not in present
            ]
            if _verdict(hotspot) != verdict and not hotspot.layered:
                hotspot.reannotated_at, hotspot.reannotated_by_id = None, None
        self._hotspots.save_all(hotspots)
        return hotspots

    def _require(self, tag_id: int) -> Tag:
        tag = self._tags.get(tag_id)
        if tag is None:
            raise NotFoundError(TAG_NOT_FOUND.format(tag_id=tag_id))
        return tag

    def _require_changeable(self, tag_id: int, user: Account) -> Tag:
        """The system admin changes any tag; an organization's admin only their organization's (global ones are
        shared by everyone, so they stay with the system admin)."""
        tag = self._require(tag_id)
        if user.role != Role.ADMIN and (tag.organization_id is None or tag.organization_id != user.organization_id):
            raise ForbiddenError(NOT_YOUR_TAG.format(name=tag.name))
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

    def _require_free_name(self, name: str, organization_id: int | None) -> None:
        if self._tags.name_taken(name, organization_id):
            raise ConflictError(TAG_NAME_TAKEN.format(name=name))

    def _require_below_cap(self, weight: int) -> None:
        cap = custom_weight_cap(
            tag.weight for tag in self._tags.list_every_organization() if tag.dimension is not None
        )
        if cap is not None and weight >= cap:
            raise InvalidInputError(CUSTOM_WEIGHT_TOO_HIGH.format(cap=cap))

    def _require_core_weight_fits(self, core_tag: Tag, weight: int) -> None:
        tags = self._tags.list_every_organization()
        cap = custom_weight_cap(
            weight if tag is core_tag else tag.weight for tag in tags if tag.dimension is not None
        )
        too_heavy = [tag.name for tag in tags if tag.dimension is None and cap is not None and tag.weight >= cap]
        if too_heavy:
            raise ConflictError(CORE_WEIGHT_CONFLICT.format(cap=cap, names=", ".join(too_heavy)))
