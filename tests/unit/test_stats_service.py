from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Role
from app.models.slide import Slide, SlideStatus
from app.models.tag import HotspotTag, TagDimension
from app.repositories.stats import StatsRepository, _as_date
from app.services.stats import ACTIVITY_DAYS, StatsService
from conftest import Factory

NOW = datetime(2026, 3, 10, 12, tzinfo=UTC)


@pytest.fixture
def service(session: Session) -> StatsService:
    return StatsService(StatsRepository(session))


def test_as_date_accepts_dates_and_iso_text() -> None:
    assert _as_date("2026-03-10") == date(2026, 3, 10)
    assert _as_date(date(2026, 3, 10)) == date(2026, 3, 10)


def test_empty_database(service: StatsService) -> None:
    stats = service.get_stats(NOW)
    assert stats.generated_at == NOW
    assert (stats.hotspots.untagged, stats.hotspots.to_reannotate, stats.hotspots.reannotated) == (0, 0, 0)
    assert stats.median_hours_to_reannotate is None
    assert stats.annotators == [] and stats.folders == []
    assert len(stats.activity) == ACTIVITY_DAYS
    assert stats.activity[-1].day == NOW.date()
    assert stats.activity[0].day == NOW.date() - timedelta(days=ACTIVITY_DAYS - 1)


def test_hotspot_progress_per_folder(service: StatsService, factory: Factory, session: Session) -> None:
    org = factory.organization()
    main, empty = factory.folder(org, "A main"), factory.folder(org, "B empty")
    importance = factory.tag("Important", 10, TagDimension.IMPORTANCE)
    accuracy = factory.tag("Right", 5, TagDimension.ACCURACY)
    factory.hotspot(main)  # untagged
    half = factory.hotspot(main)  # one verdict only: still untagged
    tagged = factory.hotspot(main)
    factory.hotspot(main, reannotated_at=NOW)
    session.add_all(
        [
            HotspotTag(hotspot_id=half.id, tag_id=importance.id),
            HotspotTag(hotspot_id=tagged.id, tag_id=importance.id),
            HotspotTag(hotspot_id=tagged.id, tag_id=accuracy.id),
        ]
    )
    session.flush()

    stats = service.get_stats(NOW)
    assert (stats.hotspots.untagged, stats.hotspots.to_reannotate, stats.hotspots.reannotated) == (2, 1, 1)
    assert stats.hotspot_folders == 2
    by_name = {folder.name: folder for folder in stats.folders}
    assert (by_name["A main"].untagged, by_name["A main"].to_reannotate, by_name["A main"].reannotated) == (2, 1, 1)
    assert (by_name["B empty"].untagged, by_name["B empty"].reannotated) == (0, 0)
    assert {tag.name: tag.hotspots for tag in stats.tags}["Important"] == 2
    assert empty.id in {folder.id for folder in stats.folders}


def test_median_hours_to_reannotate(service: StatsService, factory: Factory) -> None:
    folder = factory.folder(factory.organization())
    for hours in (1, 2, 10):
        factory.hotspot(folder, created_at=NOW - timedelta(hours=hours), reannotated_at=NOW)
    factory.hotspot(folder, created_at=NOW)  # not reannotated: left out
    assert service.get_stats(NOW).median_hours_to_reannotate == pytest.approx(2.0)


def test_accounts_and_slides(service: StatsService, factory: Factory, session: Session) -> None:
    org = factory.organization()
    factory.account(Role.ADMIN)
    factory.account(Role.ANNOTATOR, org, is_first_login=True)
    factory.account(Role.ANNOTATOR, org)
    factory.account(Role.ORG_ADMIN, org)  # neither admin nor annotator in the counts
    for status, size in ((SlideStatus.READY, 100), (SlideStatus.READY, 50), (SlideStatus.FAILED, None)):
        session.add(Slide(name="s", original_filename="s.svs", storage_key=f"k{size}{status}", status=status,
                          file_size_bytes=size, organization_id=org.id))
    session.flush()

    stats = service.get_stats(NOW)
    assert (stats.accounts.admins, stats.accounts.annotators, stats.accounts.pending_first_login) == (1, 2, 1)
    assert {item.status: item.count for item in stats.slides} == {SlideStatus.READY: 2, SlideStatus.FAILED: 1}
    assert stats.slides_total_bytes == 150


def test_idle_admins_are_left_out_of_the_annotator_load(service: StatsService, factory: Factory) -> None:
    org = factory.organization()
    factory.account(Role.ADMIN, username="idle-admin")
    factory.account(Role.ANNOTATOR, org, username="b-annotator")
    factory.account(Role.ANNOTATOR, org, username="a-annotator")
    names = [load.username for load in service.get_stats(NOW).annotators]
    assert names == ["a-annotator", "b-annotator"]


def test_activity_counts_hotspots_per_day(service: StatsService, factory: Factory) -> None:
    folder = factory.folder(factory.organization())
    factory.hotspot(folder, created_at=NOW)
    factory.hotspot(folder, created_at=NOW - timedelta(days=1))
    factory.hotspot(folder, created_at=NOW - timedelta(days=1), reannotated_at=NOW)
    factory.hotspot(folder, created_at=NOW - timedelta(days=ACTIVITY_DAYS + 5))  # outside the window
    activity = {day.day: day for day in service.get_stats(NOW).activity}
    assert activity[NOW.date()].hotspots_created == 1
    assert activity[NOW.date()].reannotated == 1
    assert activity[NOW.date() - timedelta(days=1)].hotspots_created == 2
    assert sum(day.hotspots_created for day in activity.values()) == 3
