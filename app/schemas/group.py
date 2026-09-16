from pydantic import BaseModel, ConfigDict


class GroupCreate(BaseModel):
    name: str

class AnnotationGroupResponse(BaseModel):
    id: int
    name: str
    
    model_config = ConfigDict(from_attributes=True)
