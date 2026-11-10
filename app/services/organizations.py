from sqlalchemy.exc import IntegrityError

from app.exceptions.errors import ConflictError, InvalidInputError, NotFoundError
from app.models.account import Account, Role
from app.models.organization import Organization
from app.repositories.organizations import OrganizationRepository
from app.schemas.organizations import OrganizationRead

ORGANIZATION_NOT_FOUND = "Organization {organization_id} does not exist."
ORGANIZATION_NAME_TAKEN = "An organization named '{name}' already exists."
ORGANIZATION_IN_USE = "Organization '{name}' still has accounts, slides or hotspot folders."
ORGANIZATION_REQUIRED = "Choose the organization (organization_id): there is more than one."
ORGANIZATION_REQUIRED_CODE = "organization_required"


class OrganizationService:
    def __init__(self, organizations: OrganizationRepository) -> None:
        self._organizations = organizations

    def list_organizations(self) -> list[OrganizationRead]:
        return [
            self._read(organization, count)
            for organization, count in self._organizations.list_with_member_counts()
        ]

    def create_organization(self, name: str) -> OrganizationRead:
        self._require_free_name(name)
        return self._read(self._save(Organization(name=name)), 0)

    def rename_organization(self, organization_id: int, name: str) -> OrganizationRead:
        organization = self.require(organization_id)
        if name != organization.name:
            self._require_free_name(name)
            organization.name = name
            organization = self._save(organization)
        return self._read(organization, self._organizations.count_members(organization_id))

    def delete_organization(self, organization_id: int) -> None:
        organization = self.require(organization_id)
        if self._organizations.is_in_use(organization_id):
            raise ConflictError(ORGANIZATION_IN_USE.format(name=organization.name))
        self._organizations.delete(organization)

    def require(self, organization_id: int) -> Organization:
        organization = self._organizations.get(organization_id)
        if organization is None:
            raise NotFoundError(ORGANIZATION_NOT_FOUND.format(organization_id=organization_id))
        return organization

    def target_for(self, user: Account, requested_id: int | None) -> int:
        """The organization a new slide, folder or account of `user` goes to.

        Members always add to their own organization. The system admin names one, or gets the only one there is.
        """
        if user.role != Role.ADMIN:
            if user.organization_id is None or requested_id not in (None, user.organization_id):
                raise NotFoundError(ORGANIZATION_NOT_FOUND.format(organization_id=requested_id))
            return user.organization_id
        if requested_id is not None:
            return self.require(requested_id).id
        ids = self._organizations.list_ids()
        if len(ids) != 1:
            raise InvalidInputError(ORGANIZATION_REQUIRED, ORGANIZATION_REQUIRED_CODE)
        return ids[0]

    def _require_free_name(self, name: str) -> None:
        if self._organizations.get_by_name(name) is not None:
            raise ConflictError(ORGANIZATION_NAME_TAKEN.format(name=name))

    def _save(self, organization: Organization) -> Organization:
        # The unique constraint still guards against a concurrent request taking the same name.
        try:
            return self._organizations.save(organization)
        except IntegrityError as exc:
            raise ConflictError(ORGANIZATION_NAME_TAKEN.format(name=organization.name)) from exc

    @staticmethod
    def _read(organization: Organization, member_count: int) -> OrganizationRead:
        return OrganizationRead(
            id=organization.id, name=organization.name, created_at=organization.created_at, member_count=member_count
        )
