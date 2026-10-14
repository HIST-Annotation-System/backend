from fastapi import APIRouter, status

from app.dependencies.services import CellTypeServiceDep
from app.schemas.cell_type import CellTypeCreate, CellTypeRead, CellTypeUpdate

router = APIRouter()


@router.get("/", response_model=list[CellTypeRead])
def list_cell_types(service: CellTypeServiceDep) -> list[CellTypeRead]:
    """List all cell types ordered by name."""
    return service.list_cell_types()


@router.get("/{cell_type_id}", response_model=CellTypeRead)
def get_cell_type(cell_type_id: int, service: CellTypeServiceDep) -> CellTypeRead:
    return service.get_cell_type(cell_type_id)


@router.post("/", response_model=CellTypeRead, status_code=status.HTTP_201_CREATED)
def create_cell_type(data: CellTypeCreate, service: CellTypeServiceDep) -> CellTypeRead:
    return service.create_cell_type(data)


@router.put("/{cell_type_id}", response_model=CellTypeRead)
def update_cell_type(
    cell_type_id: int, data: CellTypeUpdate, service: CellTypeServiceDep
) -> CellTypeRead:
    return service.update_cell_type(cell_type_id, data)


@router.delete("/{cell_type_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cell_type(cell_type_id: int, service: CellTypeServiceDep) -> None:
    """Delete a cell type. Returns 409 if the cell type is used by any annotation."""
    service.delete_cell_type(cell_type_id)
