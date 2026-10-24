from sqlalchemy.exc import IntegrityError

from app.exceptions.errors import ConflictError, NotFoundError
from app.models.cell_type import CellType
from app.repositories.cell_types import CellTypeRepository
from app.schemas.cell_type import CellTypeCreate, CellTypeRead, CellTypeUpdate

CELL_TYPE_NOT_FOUND = "Cell type {cell_type_id} does not exist."
CELL_TYPE_NAME_CONFLICT = "A cell type named '{name}' already exists."
CELL_TYPE_NAME_TAKEN_CODE = "cell_type_name_taken"
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
        return CellTypeRead.model_validate(self._save(CellType(name=data.name)))

    def update_cell_type(self, cell_type_id: int, data: CellTypeUpdate) -> CellTypeRead:
        cell_type = self._require(cell_type_id)
        # Changing only the letter case of its own name is allowed.
        self._require_unique_name(data.name, except_id=cell_type_id)
        cell_type.name = data.name
        return CellTypeRead.model_validate(self._save(cell_type))

    def delete_cell_type(self, cell_type_id: int) -> None:
        cell_type = self._require(cell_type_id)
        if self._cell_types.is_linked_to_annotations(cell_type_id):
            raise ConflictError(CELL_TYPE_IN_USE.format(cell_type_id=cell_type_id))
        self._cell_types.delete(cell_type)

    # ------------------------------------------------------------------

    def _require(self, cell_type_id: int) -> CellType:
        cell_type = self._cell_types.get(cell_type_id)
        if cell_type is None:
            raise NotFoundError(CELL_TYPE_NOT_FOUND.format(cell_type_id=cell_type_id))
        return cell_type

    def _require_unique_name(self, name: str, except_id: int | None = None) -> None:
        existing = self._cell_types.get_by_name(name)
        if existing is not None and existing.id != except_id:
            raise _name_taken(existing.name)

    def _save(self, cell_type: CellType) -> CellType:
        # The unique index still guards against a concurrent request taking the same name.
        try:
            return self._cell_types.save(cell_type)
        except IntegrityError as exc:
            raise _name_taken(cell_type.name) from exc


def _name_taken(name: str) -> ConflictError:
    return ConflictError(CELL_TYPE_NAME_CONFLICT.format(name=name), code=CELL_TYPE_NAME_TAKEN_CODE)
