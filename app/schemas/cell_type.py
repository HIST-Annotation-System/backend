from pydantic import BaseModel, ConfigDict, field_validator

CELL_TYPE_NAME_REQUIRED = "Name must not be empty."


class CellTypeCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(CELL_TYPE_NAME_REQUIRED)
        return value.strip()


class CellTypeUpdate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(CELL_TYPE_NAME_REQUIRED)
        return value.strip()


class CellTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
