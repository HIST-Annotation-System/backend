from fastapi import APIRouter

from app.dependencies.auth import AdminDep
from app.dependencies.services import StatsServiceDep
from app.schemas.stats import StatsRead

router = APIRouter()


@router.get("/", response_model=StatsRead)
def get_stats(service: StatsServiceDep, _admin: AdminDep) -> StatsRead:
    """Review progress, annotator load and recent activity across the whole catalog."""
    return service.get_stats()
