import statistics
import uuid
from datetime import UTC, datetime, timedelta

from app.models.account import Role
from app.models.hotspot import AnnotationKind, Hotspot, HotspotAnnotation
from app.repositories.stats import StatsRepository
from app.schemas.hotspots import HotspotStatus
from app.schemas.stats import (
    AccountCounts,
    AnnotatorLoad,
    CellTypeUsage,
    DailyActivity,
    FolderProgress,
    HotspotStatusCounts,
    SlideStatusCount,
    StatsRead,
    TagUsage,
)

# Days shown in the activity chart, today included.
ACTIVITY_DAYS = 30
RECENT_ANNOTATIONS_WINDOW = timedelta(days=7)
SECONDS_PER_HOUR = 3600


class StatsService:
    """Admin observability: where hotspots are in the review, and who is annotating what."""

    def __init__(self, stats: StatsRepository) -> None:
        self._stats = stats

    def get_stats(self, now: datetime | None = None) -> StatsRead:
        now = now or datetime.now(UTC)
        folders = self._folders()
        totals = HotspotStatusCounts(
            untagged=sum(folder.untagged for folder in folders),
            to_reannotate=sum(folder.to_reannotate for folder in folders),
            reannotated=sum(folder.reannotated for folder in folders),
        )
        slides = self._stats.slides_by_status()
        accounts = self._stats.accounts()
        return StatsRead(
            generated_at=now,
            hotspots=totals,
            hotspot_folders=len(folders),
            annotations=self._stats.count_annotations(),
            unattributed_annotations=self._stats.count_annotations(unattributed_only=True),
            median_hours_to_reannotate=self._median_hours_to_reannotate(),
            slides=[SlideStatusCount(status=status, count=count) for status, count, _ in slides],
            slides_total_bytes=sum(size or 0 for _, _, size in slides),
            accounts=AccountCounts(
                admins=sum(1 for account in accounts if account.role == Role.ADMIN),
                annotators=sum(1 for account in accounts if account.role == Role.ANNOTATOR),
                pending_first_login=sum(1 for account in accounts if account.is_first_login),
            ),
            annotators=self._annotators(now),
            cell_types=[
                CellTypeUsage(id=cell_type_id, name=name, annotations=count)
                for cell_type_id, name, count in self._stats.cell_type_usage()
            ],
            tags=[
                TagUsage(id=tag_id, name=name, dimension=dimension, hotspots=count)
                for tag_id, name, dimension, count in self._stats.tag_usage()
            ],
            folders=folders,
            activity=self._activity(now),
        )

    def _folders(self) -> list[FolderProgress]:
        folders: dict[uuid.UUID, FolderProgress] = {}
        for folder_id, name, status, count in self._stats.hotspot_status_by_folder():
            folder = folders.setdefault(folder_id, FolderProgress(id=folder_id, name=name))
            if status is not None:
                setattr(folder, HotspotStatus(status).value, count)
        return list(folders.values())

    def _annotators(self, now: datetime) -> list[AnnotatorLoad]:
        """Every annotator, plus admins who annotated or reviewed anything; the busiest first."""
        in_progress = self._stats.in_progress_by_account()
        finished = self._stats.finished_by_account()
        verdicts = self._stats.verdicts_by_account()
        last_reannotated = self._stats.last_reannotated_by_account()
        annotations = {
            account_id: (total, recent, latest)
            for account_id, total, recent, latest in self._stats.annotations_by_account(
                now - RECENT_ANNOTATIONS_WINDOW
            )
        }
        loads = []
        for account in self._stats.accounts():
            total, recent, latest_annotation = annotations.get(account.id, (0, 0, None))
            activity = [time for time in (latest_annotation, last_reannotated.get(account.id)) if time is not None]
            load = AnnotatorLoad(
                account_id=account.id,
                username=account.username,
                full_name=" ".join(part for part in (account.first_name, account.last_name) if part) or None,
                role=account.role,
                in_progress=in_progress.get(account.id, 0),
                finished=finished.get(account.id, 0),
                annotations=total,
                annotations_last_7_days=recent,
                verdicts=verdicts.get(account.id, 0),
                last_active_at=max(activity, default=None),
            )
            if account.role == Role.ANNOTATOR or load.annotations or load.finished or load.verdicts:
                loads.append(load)
        return sorted(loads, key=lambda load: (-load.in_progress, -load.finished, load.username))

    def _median_hours_to_reannotate(self) -> float | None:
        durations = [
            (reannotated_at - created_at).total_seconds() / SECONDS_PER_HOUR
            for created_at, reannotated_at in self._stats.reannotation_times()
            if reannotated_at is not None
        ]
        return statistics.median(durations) if durations else None

    def _activity(self, now: datetime) -> list[DailyActivity]:
        today = now.date()
        first_day = today - timedelta(days=ACTIVITY_DAYS - 1)
        since = datetime.combine(first_day, datetime.min.time(), tzinfo=UTC)
        annotations = self._stats.daily_counts(
            HotspotAnnotation.created_at, since, HotspotAnnotation.kind == AnnotationKind.CELL
        )
        reannotated = self._stats.daily_counts(Hotspot.reannotated_at, since)
        created = self._stats.daily_counts(Hotspot.created_at, since)
        days = [first_day + timedelta(days=offset) for offset in range(ACTIVITY_DAYS)]
        return [
            DailyActivity(
                day=day,
                annotations=annotations.get(day, 0),
                reannotated=reannotated.get(day, 0),
                hotspots_created=created.get(day, 0),
            )
            for day in days
        ]
