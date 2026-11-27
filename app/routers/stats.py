from fastapi import APIRouter

from app.dependencies.auth import ManagerDep
from app.dependencies.services import StatsServiceDep
from app.schemas.stats import StatsRead

router = APIRouter()


@router.get("/", response_model=StatsRead)
def get_stats(service: StatsServiceDep, _manager: ManagerDep) -> StatsRead:
    """Review progress, annotator load and recent activity: in the organization of an organization's admin, and for
    the system admin across the whole catalog or in the organization given as `organization_id`."""
    return service.get_stats()
