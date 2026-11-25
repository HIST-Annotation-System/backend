from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.dependencies.auth import CurrentUserDep, RequireRole
from app.dependencies.services import CellTypeServiceDep
from app.models.account import Role
from app.schemas.cell_type import CellTypeCreate, CellTypeRead, CellTypeUpdate

router = APIRouter()

RequireAdmin = Annotated[CurrentUserDep, Depends(RequireRole(Role.ADMIN))]


@router.get("/", response_model=list[CellTypeRead])
def list_cell_types(service: CellTypeServiceDep, _user: CurrentUserDep) -> list[CellTypeRead]:
    """List all cell types ordered by name."""
    return service.list_cell_types()


@router.get("/{cell_type_id}", response_model=CellTypeRead)
def get_cell_type(cell_type_id: int, service: CellTypeServiceDep, _user: CurrentUserDep) -> CellTypeRead:
    return service.get_cell_type(cell_type_id)


@router.post("/", response_model=CellTypeRead, status_code=status.HTTP_201_CREATED)
def create_cell_type(data: CellTypeCreate, service: CellTypeServiceDep, _admin: RequireAdmin) -> CellTypeRead:
    """409 if a cell type with the same name (ignoring letter case and extra spaces) already exists."""
    return service.create_cell_type(data)


@router.put("/{cell_type_id}", response_model=CellTypeRead)
def update_cell_type(
    cell_type_id: int, data: CellTypeUpdate, service: CellTypeServiceDep, _admin: RequireAdmin
) -> CellTypeRead:
    """409 if another cell type already has the name (ignoring letter case and extra spaces)."""
    return service.update_cell_type(cell_type_id, data)


@router.delete("/{cell_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cell_type(cell_type_id: int, service: CellTypeServiceDep, _admin: RequireAdmin) -> None:
    """Delete a cell type. Returns 409 if the cell type is used by any annotation."""
    service.delete_cell_type(cell_type_id)
