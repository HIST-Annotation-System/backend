from fastapi import APIRouter, Depends, status

from app.dependencies.auth import require_admin
from app.dependencies.services import OrganizationServiceDep
from app.schemas.organizations import OrganizationRead, OrganizationWrite

# Organizations are managed by the system admin only.
router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/", response_model=list[OrganizationRead])
def list_organizations(service: OrganizationServiceDep) -> list[OrganizationRead]:
    return service.list_organizations()


@router.post("/", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create_organization(data: OrganizationWrite, service: OrganizationServiceDep) -> OrganizationRead:
    return service.create_organization(data.name)


@router.patch("/{organization_id}", response_model=OrganizationRead)
def rename_organization(
    organization_id: int, data: OrganizationWrite, service: OrganizationServiceDep
) -> OrganizationRead:
    return service.rename_organization(organization_id, data.name)


@router.delete("/{organization_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_organization(organization_id: int, service: OrganizationServiceDep) -> None:
    """409 while any account, slide or hotspot folder still belongs to it."""
    service.delete_organization(organization_id)
