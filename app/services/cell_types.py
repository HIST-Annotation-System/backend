from app.exceptions.errors import ConflictError, NotFoundError
from app.repositories.cell_types import CellTypeRepository
from app.schemas.cell_type import CellTypeCreate, CellTypeRead, CellTypeUpdate

CELL_TYPE_NOT_FOUND = "Cell type {cell_type_id} does not exist."
CELL_TYPE_NAME_CONFLICT = "A cell type named '{name}' already exists."
CELL_TYPE_IN_USE = (
    "Cell type {cell_type_id} is linked to existing annotations and cannot be deleted."
)


class CellTypeService:
    def __init__(self, cell_types: CellTypeRepository) -> None:
        self._cell_types = cell_types

    def list_cell_types(self) -> list[CellTypeRead]:
        return [CellTypeRead.model_validate(ct) for ct in self._cell_types.list_all()]

    list_all = list_cell_types

    def get_cell_type(self, cell_type_id: int) -> CellTypeRead:
        return CellTypeRead.model_validate(self._require(cell_type_id))

    def create_cell_type(self, data: CellTypeCreate) -> CellTypeRead:
        self._require_unique_name(data.name)
        cell_type = self._cell_types.create(name=data.name)
        return CellTypeRead.model_validate(cell_type)

    def update_cell_type(self, cell_type_id: int, data: CellTypeUpdate) -> CellTypeRead:
        cell_type = self._require(cell_type_id)
        if data.name != cell_type.name:
            self._require_unique_name(data.name)
        cell_type.name = data.name
        return CellTypeRead.model_validate(self._cell_types.save(cell_type))

    def delete_cell_type(self, cell_type_id: int) -> None:
        cell_type = self._require(cell_type_id)
        if self._cell_types.is_linked_to_annotations(cell_type_id):
            raise ConflictError(CELL_TYPE_IN_USE.format(cell_type_id=cell_type_id))
        self._cell_types.delete(cell_type)

    # ------------------------------------------------------------------

    def _require(self, cell_type_id: int) -> object:
        cell_type = self._cell_types.get(cell_type_id)
        if cell_type is None:
            raise NotFoundError(CELL_TYPE_NOT_FOUND.format(cell_type_id=cell_type_id))
        return cell_type

    def _require_unique_name(self, name: str) -> None:
        if self._cell_types.get_by_name(name) is not None:
            raise ConflictError(CELL_TYPE_NAME_CONFLICT.format(name=name))
