"""Organizations, tags and cell types: the lists administrators maintain, and their invariants."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.exceptions.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.models import Account, CellType, HotspotAnnotation, Organization, Role
from app.models.hotspot import AnnotationKind
from app.models.tag import TagDimension
from app.repositories.cell_types import CellTypeRepository
from app.repositories.hotspots import HotspotRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.tags import TagRepository
from app.schemas.cell_type import CellTypeCreate, CellTypeUpdate, normalize_cell_type_name
from app.schemas.tags import HotspotTagsBulk, TagCreate, TagUpdate
from app.services.cell_types import CellTypeService
from app.services.organizations import ORGANIZATION_REQUIRED_CODE, OrganizationService
from app.services.tags import TagService, custom_weight_cap
from conftest import Factory


class TestOrganizationService:
    @pytest.fixture
    def service(self, session: Session) -> OrganizationService:
        return OrganizationService(OrganizationRepository(session))

    def test_create_and_list_with_member_counts(self, service: OrganizationService, factory: Factory) -> None:
        created = service.create_organization("Beta")
        alpha = factory.organization("Alpha")
        factory.account(Role.ANNOTATOR, alpha)
        factory.account(Role.ORG_ADMIN, alpha)
        assert created.member_count == 0
        assert [(o.name, o.member_count) for o in service.list_organizations()] == [("Alpha", 2), ("Beta", 0)]

    def test_name_must_be_free(self, service: OrganizationService) -> None:
        service.create_organization("Lab")
        with pytest.raises(ConflictError, match="already exists"):
            service.create_organization("Lab")

    def test_rename(self, service: OrganizationService) -> None:
        lab = service.create_organization("Lab")
        assert service.rename_organization(lab.id, "Lab 2").name == "Lab 2"

    def test_rename_to_the_same_name_is_a_no_op(self, service: OrganizationService) -> None:
        lab = service.create_organization("Lab")
        assert service.rename_organization(lab.id, "Lab").name == "Lab"

    def test_rename_to_a_taken_name(self, service: OrganizationService) -> None:
        service.create_organization("A")
        b = service.create_organization("B")
        with pytest.raises(ConflictError):
            service.rename_organization(b.id, "A")

    def test_delete_an_unused_organization(self, service: OrganizationService) -> None:
        lab = service.create_organization("Lab")
        service.delete_organization(lab.id)
        with pytest.raises(NotFoundError):
            service.require(lab.id)

    @pytest.mark.parametrize("what", ["account", "folder"])
    def test_organization_in_use_is_kept(self, service: OrganizationService, factory: Factory, what: str) -> None:
        lab = factory.organization("Lab")
        if what == "account":
            factory.account(Role.ANNOTATOR, lab)
        else:
            factory.folder(lab)
        with pytest.raises(ConflictError, match="still has"):
            service.delete_organization(lab.id)

    class TestTargetFor:
        @pytest.fixture
        def service(self, session: Session) -> OrganizationService:
            return OrganizationService(OrganizationRepository(session))

        def test_members_always_add_to_their_own_organization(
            self, service: OrganizationService, factory: Factory
        ) -> None:
            lab = factory.organization()
            member = factory.account(Role.ORG_ADMIN, lab)
            assert service.target_for(member, None) == lab.id
            assert service.target_for(member, lab.id) == lab.id

        def test_members_cannot_target_another_organization(
            self, service: OrganizationService, factory: Factory
        ) -> None:
            member = factory.account(Role.ANNOTATOR, factory.organization())
            other = factory.organization()
            with pytest.raises(NotFoundError):
                service.target_for(member, other.id)

        def test_member_without_organization(self, service: OrganizationService, factory: Factory) -> None:
            orphan = factory.account(Role.ANNOTATOR, None)
            with pytest.raises(NotFoundError):
                service.target_for(orphan, None)

        def test_admin_names_an_existing_organization(self, service: OrganizationService, factory: Factory) -> None:
            admin = factory.account(Role.ADMIN)
            lab = factory.organization()
            factory.organization()
            assert service.target_for(admin, lab.id) == lab.id
            with pytest.raises(NotFoundError):
                service.target_for(admin, 9999)

        def test_admin_gets_the_only_organization(self, service: OrganizationService, factory: Factory) -> None:
            admin = factory.account(Role.ADMIN)
            lab = factory.organization()
            assert service.target_for(admin, None) == lab.id

        @pytest.mark.parametrize("count", [0, 2])
        def test_admin_must_choose_unless_there_is_exactly_one(
            self, service: OrganizationService, factory: Factory, count: int
        ) -> None:
            admin = factory.account(Role.ADMIN)
            for _ in range(count):
                factory.organization()
            with pytest.raises(InvalidInputError) as error:
                service.target_for(admin, None)
            assert error.value.code == ORGANIZATION_REQUIRED_CODE


class TestCellTypes:
    @pytest.fixture
    def service(self, session: Session) -> CellTypeService:
        return CellTypeService(CellTypeRepository(session))

    @pytest.mark.parametrize(("raw", "name"), [("  CD8 ", "CD8"), ("CD8   T\tcell", "CD8 T cell")])
    def test_names_are_normalized(self, raw: str, name: str) -> None:
        assert normalize_cell_type_name(raw) == name
        assert CellTypeCreate(name=raw).name == name

    @pytest.mark.parametrize("raw", ["", "   ", "x" * 129])
    def test_empty_or_too_long_names_are_rejected(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            CellTypeCreate(name=raw)

    def test_create_list_get(self, service: CellTypeService) -> None:
        b = service.create_cell_type(CellTypeCreate(name="B"))
        service.create_cell_type(CellTypeCreate(name="A"))
        assert [t.name for t in service.list_cell_types()] == ["A", "B"]
        assert service.get_cell_type(b.id).name == "B"

    def test_unknown(self, service: CellTypeService) -> None:
        with pytest.raises(NotFoundError):
            service.get_cell_type(42)

    def test_names_are_unique_ignoring_case(self, service: CellTypeService) -> None:
        service.create_cell_type(CellTypeCreate(name="Lymphocyte"))
        with pytest.raises(ConflictError) as error:
            service.create_cell_type(CellTypeCreate(name="LYMPHOCYTE"))
        assert error.value.code == "cell_type_name_taken"

    def test_renaming_only_the_letter_case_is_allowed(self, service: CellTypeService) -> None:
        cd8 = service.create_cell_type(CellTypeCreate(name="cd8"))
        assert service.update_cell_type(cd8.id, CellTypeUpdate(name="CD8")).name == "CD8"

    def test_renaming_onto_another_name_is_refused(self, service: CellTypeService) -> None:
        service.create_cell_type(CellTypeCreate(name="A"))
        b = service.create_cell_type(CellTypeCreate(name="B"))
        with pytest.raises(ConflictError):
            service.update_cell_type(b.id, CellTypeUpdate(name="a"))

    def test_unclassified_is_reserved(self, service: CellTypeService) -> None:
        reserved = service.create_cell_type(CellTypeCreate(name="Unclassified"))
        with pytest.raises(ConflictError, match="used by the model"):
            service.update_cell_type(reserved.id, CellTypeUpdate(name="Other"))
        with pytest.raises(ConflictError, match="used by the model"):
            service.delete_cell_type(reserved.id)

    def test_delete_unused(self, service: CellTypeService) -> None:
        a = service.create_cell_type(CellTypeCreate(name="A"))
        service.delete_cell_type(a.id)
        assert service.list_cell_types() == []

    def test_type_used_by_annotations_cannot_be_deleted(
        self, service: CellTypeService, session: Session, factory: Factory
    ) -> None:
        a = service.create_cell_type(CellTypeCreate(name="A"))
        hotspot = factory.hotspot(factory.folder(factory.organization()))
        ring = [[0, 0], [1, 0], [1, 1], [0, 0]]
        session.add(
            HotspotAnnotation(
                id=uuid.uuid4(),
                hotspot_id=hotspot.id,
                kind=AnnotationKind.CELL,
                cell_type_id=a.id,
                geometry={"type": "Polygon", "coordinates": [ring]},
            )
        )
        session.flush()
        with pytest.raises(ConflictError, match="linked to existing annotations"):
            service.delete_cell_type(a.id)
        assert session.get(CellType, a.id) is not None


class TestTags:
    @pytest.fixture
    def core(self, factory: Factory) -> dict[str, int]:
        tags = {
            "high": factory.tag("High importance", 30, TagDimension.IMPORTANCE),
            "low": factory.tag("Low importance", 10, TagDimension.IMPORTANCE),
            "none": factory.tag("No importance", 0, TagDimension.IMPORTANCE),
            "wrong": factory.tag("Wrong", 20, TagDimension.ACCURACY),
            "right": factory.tag("Right", 15, TagDimension.ACCURACY),
        }
        return {key: tag.id for key, tag in tags.items()}

    @pytest.fixture
    def service(self, session: Session) -> TagService:
        return TagService(TagRepository(session), HotspotRepository(session))

    def test_custom_weight_cap(self) -> None:
        assert custom_weight_cap([30, 0, 10]) == 10
        assert custom_weight_cap([0, 0]) is None
        assert custom_weight_cap([]) is None

    def test_schemas(self) -> None:
        assert TagCreate(name="  Mitosis ").name == "Mitosis"
        assert TagCreate(name="x").weight == 5
        with pytest.raises(ValidationError):
            TagCreate(name="x", weight=-1)
        with pytest.raises(ValidationError, match="name and/or weight"):
            TagUpdate()
        with pytest.raises(ValidationError, match="add and/or remove"):
            HotspotTagsBulk(hotspot_ids=[uuid.uuid4()])
        with pytest.raises(ValidationError):
            HotspotTagsBulk(hotspot_ids=[], add=[1])

    def test_create_custom_tag_below_the_cap(self, service: TagService, core: dict[str, int], factory: Factory) -> None:
        author = factory.account(Role.ADMIN)
        assert service.create_tag(TagCreate(name="Mitosis", weight=9), author).weight == 9
        with pytest.raises(InvalidInputError, match="less than 10"):
            service.create_tag(TagCreate(name="Necrosis", weight=10), author)

    def test_no_cap_without_weighted_core_tags(self, service: TagService, factory: Factory) -> None:
        assert service.create_tag(TagCreate(name="Big", weight=999), factory.account(Role.ADMIN)).weight == 999

    def test_duplicate_name(self, service: TagService, core: dict[str, int], factory: Factory) -> None:
        with pytest.raises(ConflictError):
            service.create_tag(TagCreate(name="Wrong", weight=1), factory.account(Role.ADMIN))

    def test_core_tags_can_only_be_reweighted(
        self, service: TagService, core: dict[str, int], factory: Factory
    ) -> None:
        admin = factory.account(Role.ADMIN)
        with pytest.raises(ConflictError, match="core tag"):
            service.update_tag(core["wrong"], TagUpdate(name="Incorrect"), admin)
        with pytest.raises(ConflictError, match="core tag"):
            service.delete_tag(core["wrong"], admin)
        assert service.update_tag(core["wrong"], TagUpdate(name="Wrong", weight=25), admin).weight == 25

    def test_lowering_a_core_tag_under_a_custom_tag_is_refused(
        self, service: TagService, core: dict[str, int], factory: Factory
    ) -> None:
        admin = factory.account(Role.ADMIN)
        service.create_tag(TagCreate(name="Mitosis", weight=8), admin)
        with pytest.raises(ConflictError, match="Mitosis"):
            service.update_tag(core["low"], TagUpdate(weight=8), admin)
        assert service.update_tag(core["low"], TagUpdate(weight=9), admin).weight == 9

    def test_custom_tag_rename_and_reweight(self, service: TagService, core: dict[str, int], factory: Factory) -> None:
        admin = factory.account(Role.ADMIN)
        tag = service.create_tag(TagCreate(name="A", weight=1), admin)
        updated = service.update_tag(tag.id, TagUpdate(name="B", weight=2), admin)
        assert (updated.name, updated.weight) == ("B", 2)
        with pytest.raises(InvalidInputError):
            service.update_tag(tag.id, TagUpdate(weight=50), admin)
        service.delete_tag(tag.id, admin)
        with pytest.raises(NotFoundError):
            service.update_tag(tag.id, TagUpdate(weight=1), admin)

    def test_core_tag_replaces_the_one_of_its_dimension(
        self, service: TagService, core: dict[str, int], factory: Factory
    ) -> None:
        user = factory.account(Role.ADMIN)
        hotspot = factory.hotspot(factory.folder(factory.organization()))
        custom = service.create_tag(TagCreate(name="Mitosis", weight=3), user)
        service.tag_hotspots([hotspot.id], add=[core["low"], core["right"], custom.id], remove=[], user=user)
        assert hotspot.score == 10 + 15 + 3

        service.tag_hotspots([hotspot.id], add=[core["high"]], remove=[], user=user)
        assert {tag.name for tag in hotspot.tags} == {"High importance", "Right", "Mitosis"}
        assert hotspot.score == 30 + 15 + 3

    def test_remove_then_add(self, service: TagService, core: dict[str, int], factory: Factory) -> None:
        user = factory.account(Role.ADMIN)
        hotspot = factory.hotspot(factory.folder(factory.organization()))
        service.tag_hotspots([hotspot.id], add=[core["wrong"]], remove=[], user=user)
        service.tag_hotspots([hotspot.id], add=[], remove=[core["wrong"]], user=user)
        assert hotspot.tags == []
        # A tag both removed and added stays.
        service.tag_hotspots([hotspot.id], add=[core["wrong"]], remove=[core["wrong"]], user=user)
        assert [tag.name for tag in hotspot.tags] == ["Wrong"]

    def test_two_tags_of_one_dimension_at_once(
        self, service: TagService, core: dict[str, int], factory: Factory
    ) -> None:
        hotspot = factory.hotspot(factory.folder(factory.organization()))
        with pytest.raises(InvalidInputError, match="one importance tag"):
            service.tag_hotspots([hotspot.id], add=[core["low"], core["high"]], remove=[], user=factory.account())

    def test_unknown_tags_and_hotspots(self, service: TagService, core: dict[str, int], factory: Factory) -> None:
        hotspot = factory.hotspot(factory.folder(factory.organization()))
        with pytest.raises(NotFoundError, match="Tags do not exist: 998, 999"):
            service.tag_hotspots([hotspot.id], add=[999, 998], remove=[], user=factory.account())
        with pytest.raises(NotFoundError, match="Hotspots do not exist"):
            service.tag_hotspots([uuid.uuid4()], add=[core["low"]], remove=[], user=factory.account())

    def test_changed_verdict_sends_a_manually_reannotated_hotspot_back(
        self, service: TagService, core: dict[str, int], factory: Factory
    ) -> None:
        user = factory.account(Role.ADMIN)
        folder = factory.folder(factory.organization())
        legacy = factory.hotspot(folder, layered=False, reannotated_at=datetime.now(UTC), reannotated_by_id=user.id)
        layered = factory.hotspot(folder, layered=True, reannotated_at=datetime.now(UTC))
        custom = service.create_tag(TagCreate(name="Mitosis", weight=1), user)

        service.tag_hotspots([legacy.id, layered.id], add=[custom.id], remove=[], user=user)
        assert legacy.reannotated_at is not None  # a custom tag is not a verdict

        service.tag_hotspots([legacy.id, layered.id], add=[core["wrong"]], remove=[], user=user)
        assert (legacy.reannotated_at, legacy.reannotated_by_id) == (None, None)
        assert layered.reannotated_at is not None

    def test_hotspots_of_other_organizations_are_out_of_scope(
        self, session: Session, core: dict[str, int], factory: Factory
    ) -> None:
        mine, theirs = factory.organization(), factory.organization()
        hotspot = factory.hotspot(factory.folder(theirs))
        scoped = TagService(TagRepository(session), HotspotRepository(session, mine.id))
        with pytest.raises(NotFoundError):
            scoped.tag_hotspots([hotspot.id], add=[core["low"]], remove=[], user=factory.account())


class TestOrganizationTags:
    """Global tags (the core ones and the system admin's) are seen by everyone; others belong to an organization."""

    @pytest.fixture
    def orgs(self, factory: Factory) -> tuple[int, int]:
        factory.tag("Low importance", 10, TagDimension.IMPORTANCE)
        return factory.organization().id, factory.organization().id

    def scoped(self, session: Session, organization_id: int | None) -> TagService:
        return TagService(TagRepository(session, organization_id), HotspotRepository(session, organization_id))

    def member(self, factory: Factory, session: Session, role: Role, organization_id: int) -> Account:
        organization = session.get(Organization, organization_id)
        return factory.account(role, organization)

    def test_admin_tags_are_global_and_member_tags_belong_to_their_organization(
        self, session: Session, factory: Factory, orgs: tuple[int, int]
    ) -> None:
        mine, theirs = orgs
        admin = factory.account(Role.ADMIN)
        global_tag = self.scoped(session, None).create_tag(TagCreate(name="Global", weight=1), admin)
        annotator = self.member(factory, session, Role.ANNOTATOR, mine)
        own = self.scoped(session, mine).create_tag(TagCreate(name="Ours", weight=1), annotator)
        assert (global_tag.organization_id, own.organization_id) == (None, mine)

        assert {tag.name for tag in self.scoped(session, mine).list_tags()} == {"Low importance", "Global", "Ours"}
        assert {tag.name for tag in self.scoped(session, theirs).list_tags()} == {"Low importance", "Global"}
        assert {tag.name for tag in self.scoped(session, None).list_tags()} == {"Low importance", "Global", "Ours"}

    def test_names_are_unique_within_what_people_see(
        self, session: Session, factory: Factory, orgs: tuple[int, int]
    ) -> None:
        mine, theirs = orgs
        admin = factory.account(Role.ADMIN)
        self.scoped(session, mine).create_tag(
            TagCreate(name="Mitosis", weight=1), self.member(factory, session, Role.ANNOTATOR, mine)
        )
        # Another organization may use the same name...
        self.scoped(session, theirs).create_tag(
            TagCreate(name="Mitosis", weight=1), self.member(factory, session, Role.ANNOTATOR, theirs)
        )
        # ...but not repeat a global one, and a global tag can't take a name an organization already uses.
        with pytest.raises(ConflictError):
            self.scoped(session, mine).create_tag(
                TagCreate(name="Low importance", weight=1), self.member(factory, session, Role.ANNOTATOR, mine)
            )
        with pytest.raises(ConflictError):
            self.scoped(session, None).create_tag(TagCreate(name="Mitosis", weight=1), admin)

    def test_who_may_change_a_tag(self, session: Session, factory: Factory, orgs: tuple[int, int]) -> None:
        mine, theirs = orgs
        admin = factory.account(Role.ADMIN)
        global_tag = self.scoped(session, None).create_tag(TagCreate(name="Global", weight=1), admin)
        org_admin = self.member(factory, session, Role.ORG_ADMIN, mine)
        own = self.scoped(session, mine).create_tag(TagCreate(name="Ours", weight=1), org_admin)
        foreign = self.scoped(session, theirs).create_tag(
            TagCreate(name="Theirs", weight=1), self.member(factory, session, Role.ORG_ADMIN, theirs)
        )
        service = self.scoped(session, mine)

        assert service.update_tag(own.id, TagUpdate(weight=2), org_admin).weight == 2
        with pytest.raises(ForbiddenError):
            service.update_tag(global_tag.id, TagUpdate(weight=2), org_admin)
        with pytest.raises(ForbiddenError):
            service.delete_tag(global_tag.id, org_admin)
        # Another organization's tag doesn't exist as far as this one can tell.
        with pytest.raises(NotFoundError):
            service.delete_tag(foreign.id, org_admin)
        service.delete_tag(own.id, org_admin)

        # The system admin changes anyone's.
        self.scoped(session, None).update_tag(foreign.id, TagUpdate(weight=3), admin)
        self.scoped(session, None).delete_tag(global_tag.id, admin)

    def test_a_tag_goes_only_on_hotspots_of_its_organization(
        self, session: Session, factory: Factory, orgs: tuple[int, int]
    ) -> None:
        mine, theirs = orgs
        admin = factory.account(Role.ADMIN)
        own = self.scoped(session, mine).create_tag(
            TagCreate(name="Ours", weight=1), self.member(factory, session, Role.ANNOTATOR, mine)
        )
        foreign_hotspot = factory.hotspot(factory.folder(session.get(Organization, theirs)))
        # Members don't even see the other organization's tag...
        with pytest.raises(NotFoundError):
            self.scoped(session, theirs).tag_hotspots([foreign_hotspot.id], add=[own.id], remove=[], user=admin)
        # ...and the system admin, who sees every tag, can't mix them up either.
        with pytest.raises(InvalidInputError, match="another organization"):
            self.scoped(session, None).tag_hotspots([foreign_hotspot.id], add=[own.id], remove=[], user=admin)
        own_hotspot = factory.hotspot(factory.folder(session.get(Organization, mine)))
        self.scoped(session, None).tag_hotspots([own_hotspot.id], add=[own.id], remove=[], user=admin)
        assert [tag.name for tag in own_hotspot.tags] == ["Ours"]
