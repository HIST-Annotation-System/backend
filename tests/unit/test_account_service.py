"""The account permission rules: who may create, edit and delete which accounts."""

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.exceptions.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from app.models import Account, Role
from app.models.category import Category
from app.repositories.accounts import AccountRepository, RefreshTokenRepository
from app.repositories.profiles import ProfileRepository
from app.schemas.user import AccountCreate, ProfileSetup, ProfileUpdate
from app.services.accounts import AccountService
from app.services.auth import AuthService
from conftest import Factory


class World:
    def __init__(self, session: Session, settings: Settings, factory: Factory) -> None:
        self.session = session
        self.factory = factory
        self.auth = AuthService(settings, AccountRepository(session), RefreshTokenRepository(session))
        self.service = AccountService(AccountRepository(session), ProfileRepository(session), self.auth)
        self.org = factory.organization("Org A")
        self.other_org = factory.organization("Org B")
        self.admin = factory.account(Role.ADMIN, username="root")
        self.org_admin = factory.account(Role.ORG_ADMIN, self.org, username="boss")
        self.annotator = factory.account(Role.ANNOTATOR, self.org, username="anna")
        self.outsider = factory.account(Role.ANNOTATOR, self.other_org, username="olaf")


@pytest.fixture
def world(session: Session, settings: Settings, factory: Factory) -> World:
    return World(session, settings, factory)


class TestCreateAccount:
    def test_org_admin_creates_an_annotator_in_their_organization(self, world: World) -> None:
        account = world.service.create_account(
            world.org_admin, AccountCreate(username="new", password="pw", can_upload=True), world.org.id
        )
        assert account.organization_id == world.org.id
        assert account.is_first_login
        assert world.auth.verify_password("pw", account.hashed_password)
        world.session.refresh(account)
        assert account.profile.can_upload is True

    def test_org_admin_may_create_another_org_admin(self, world: World) -> None:
        account = world.service.create_account(
            world.org_admin, AccountCreate(username="new", password="pw", role=Role.ORG_ADMIN), world.org.id
        )
        assert account.role == Role.ORG_ADMIN

    def test_org_admin_may_not_create_a_system_admin(self, world: World) -> None:
        with pytest.raises(ForbiddenError, match="ADMIN"):
            world.service.create_account(
                world.org_admin, AccountCreate(username="new", password="pw", role=Role.ADMIN), world.org.id
            )

    def test_system_admin_has_no_organization(self, world: World) -> None:
        account = world.service.create_account(
            world.admin, AccountCreate(username="root2", password="pw", role=Role.ADMIN), world.org.id
        )
        assert account.organization_id is None

    def test_member_needs_an_organization(self, world: World) -> None:
        with pytest.raises(InvalidInputError, match="need an organization"):
            world.service.create_account(world.admin, AccountCreate(username="x", password="pw"), None)

    def test_username_must_be_free(self, world: World) -> None:
        with pytest.raises(ConflictError, match="already registered"):
            world.service.create_account(world.admin, AccountCreate(username="anna", password="pw"), world.org.id)


class TestSetupProfile:
    def test_first_login_sets_password_names_and_categories(self, world: World) -> None:
        category = Category(name="Breast")
        world.session.add(category)
        account = world.factory.account(Role.ANNOTATOR, world.org, is_first_login=True)
        world.service.setup_profile(
            account.id, ProfileSetup(new_password="new-pw", first_name="Ada", last_name="L", category_ids=[category.id])
        )
        assert not account.is_first_login
        assert world.auth.verify_password("new-pw", account.hashed_password)
        assert (account.profile.first_name, account.profile.last_name) == ("Ada", "L")
        assert [c.name for c in account.profile.categories] == ["Breast"]

    def test_creates_a_missing_profile(self, world: World) -> None:
        account = world.factory.account(Role.ANNOTATOR, world.org, is_first_login=True, with_profile=False)
        world.service.setup_profile(account.id, ProfileSetup(new_password="pw", first_name="A", last_name="B"))
        assert account.profile.first_name == "A"
        assert account.profile.can_upload is False

    def test_only_once(self, world: World) -> None:
        with pytest.raises(ConflictError, match="already setup"):
            world.service.setup_profile(
                world.annotator.id, ProfileSetup(new_password="pw", first_name="A", last_name="B")
            )

    def test_unknown_account(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            world.service.setup_profile(9999, ProfileSetup(new_password="pw", first_name="A", last_name="B"))


class TestUpdateProfile:
    def test_annotator_edits_their_own_names_and_password(self, world: World) -> None:
        world.service.update_profile(
            world.annotator, world.annotator.id, ProfileUpdate(first_name="Anna", password="secret")
        )
        assert world.annotator.profile.first_name == "Anna"
        assert world.auth.verify_password("secret", world.annotator.hashed_password)

    def test_annotator_cannot_grant_themselves_uploads(self, world: World) -> None:
        world.service.update_profile(world.annotator, world.annotator.id, ProfileUpdate(can_upload=True))
        assert world.annotator.profile.can_upload is False

    def test_annotator_cannot_change_their_own_role(self, world: World) -> None:
        world.service.update_profile(world.annotator, world.annotator.id, ProfileUpdate(role=Role.ADMIN))
        assert world.annotator.role == Role.ANNOTATOR

    def test_annotator_cannot_edit_someone_else(self, world: World) -> None:
        other = world.factory.account(Role.ANNOTATOR, world.org)
        with pytest.raises(ForbiddenError):
            world.service.update_profile(world.annotator, other.id, ProfileUpdate(first_name="X"))

    def test_org_admin_manages_members_of_their_organization(self, world: World) -> None:
        world.service.update_profile(
            world.org_admin, world.annotator.id, ProfileUpdate(can_upload=True, role=Role.ORG_ADMIN)
        )
        assert world.annotator.profile.can_upload is True
        assert world.annotator.role == Role.ORG_ADMIN

    def test_other_organizations_accounts_are_not_found_for_an_org_admin(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            world.service.update_profile(world.org_admin, world.outsider.id, ProfileUpdate(first_name="X"))

    def test_org_admin_cannot_edit_a_fellow_org_admin(self, world: World) -> None:
        fellow = world.factory.account(Role.ORG_ADMIN, world.org)
        with pytest.raises(ForbiddenError, match="another admin"):
            world.service.update_profile(world.org_admin, fellow.id, ProfileUpdate(first_name="X"))

    def test_system_admin_cannot_edit_another_system_admin(self, world: World) -> None:
        other = world.factory.account(Role.ADMIN)
        with pytest.raises(ForbiddenError, match="another admin"):
            world.service.update_profile(world.admin, other.id, ProfileUpdate(first_name="X"))

    def test_admin_edits_their_own_names(self, world: World) -> None:
        world.service.update_profile(world.admin, world.admin.id, ProfileUpdate(first_name="Root"))
        assert world.admin.profile.first_name == "Root"

    @pytest.mark.parametrize("actor", ["admin", "org_admin"])
    def test_nobody_changes_their_own_role(self, world: World, actor: str) -> None:
        account: Account = getattr(world, actor)
        with pytest.raises(ForbiddenError, match="your own role"):
            world.service.update_profile(account, account.id, ProfileUpdate(role=Role.ANNOTATOR))

    def test_restating_the_current_role_is_not_a_change(self, world: World) -> None:
        world.service.update_profile(world.admin, world.admin.id, ProfileUpdate(role=Role.ADMIN))

    def test_org_admin_cannot_promote_to_system_admin(self, world: World) -> None:
        with pytest.raises(ForbiddenError, match="ADMIN"):
            world.service.update_profile(world.org_admin, world.annotator.id, ProfileUpdate(role=Role.ADMIN))

    def test_org_admin_cannot_move_an_account_to_another_organization(self, world: World) -> None:
        with pytest.raises(ForbiddenError, match="another organization"):
            world.service.update_profile(
                world.org_admin, world.annotator.id, ProfileUpdate(organization_id=world.other_org.id)
            )

    def test_system_admin_moves_an_account(self, world: World) -> None:
        world.service.update_profile(world.admin, world.annotator.id, ProfileUpdate(organization_id=world.other_org.id))
        assert world.annotator.organization_id == world.other_org.id

    def test_promoting_to_system_admin_clears_the_organization(self, world: World) -> None:
        world.service.update_profile(world.admin, world.annotator.id, ProfileUpdate(role=Role.ADMIN))
        assert world.annotator.role == Role.ADMIN
        assert world.annotator.organization_id is None

    def test_system_admin_cannot_demote_another_system_admin(self, world: World) -> None:
        other = world.factory.account(Role.ADMIN)
        with pytest.raises(ForbiddenError, match="another admin"):
            world.service.update_profile(world.admin, other.id, ProfileUpdate(role=Role.ANNOTATOR))

    def test_removing_the_organization_of_a_member_is_refused(self, world: World) -> None:
        with pytest.raises(InvalidInputError, match="need an organization"):
            world.service.update_profile(world.admin, world.annotator.id, ProfileUpdate(organization_id=None))

    def test_unknown_account(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            world.service.update_profile(world.admin, 9999, ProfileUpdate(first_name="X"))


class TestDeleteAccount:
    def test_org_admin_deletes_a_member(self, world: World) -> None:
        world.service.delete_account(world.org_admin, world.annotator.id)
        assert AccountRepository(world.session).get_by_id(world.annotator.id) is None

    def test_org_admin_cannot_see_other_organizations(self, world: World) -> None:
        with pytest.raises(NotFoundError):
            world.service.delete_account(world.org_admin, world.outsider.id)

    def test_admins_of_the_same_rank_cannot_delete_each_other(self, world: World) -> None:
        with pytest.raises(ForbiddenError, match="another admin"):
            world.service.delete_account(world.admin, world.factory.account(Role.ADMIN).id)
        with pytest.raises(ForbiddenError, match="another admin"):
            world.service.delete_account(world.org_admin, world.factory.account(Role.ORG_ADMIN, world.org).id)

    def test_system_admin_deletes_an_org_admin(self, world: World) -> None:
        world.service.delete_account(world.admin, world.org_admin.id)
        assert AccountRepository(world.session).get_by_id(world.org_admin.id) is None


def test_list_accounts_is_scoped_to_an_organization(world: World) -> None:
    names = [account.username for account in world.service.list_accounts(world.org.id)]
    assert names == ["anna", "boss"]
    assert len(world.service.list_accounts(None)) == 4
