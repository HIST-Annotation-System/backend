from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.models.cell_type import CELL_TYPE_NAME_MAX_LENGTH

CELL_TYPE_NAME_REQUIRED = "Name must not be empty."
CELL_TYPE_NAME_TOO_LONG = f"Name must be at most {CELL_TYPE_NAME_MAX_LENGTH} characters."


def normalize_cell_type_name(value: str) -> str:
    """Trim and collapse inner whitespace, so "CD8  T" and "CD8 T" can't live side by side."""
    name = " ".join(value.split())
    if not name:
        raise ValueError(CELL_TYPE_NAME_REQUIRED)
    if len(name) > CELL_TYPE_NAME_MAX_LENGTH:
        raise ValueError(CELL_TYPE_NAME_TOO_LONG)
    return name


CellTypeName = Annotated[str, AfterValidator(normalize_cell_type_name)]


class CellTypeCreate(BaseModel):
    name: CellTypeName


class CellTypeUpdate(BaseModel):
    name: CellTypeName


class CellTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
