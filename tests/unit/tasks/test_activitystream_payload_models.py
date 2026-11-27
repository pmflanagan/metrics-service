"""ActivityStream opt-out for machine-generated metrics payload models.

These payload tables are written and purged by background collectors, so auditing
their rows would add a per-row ActivityStream ``Entry`` write without any user-facing
value. Retention/collection should therefore never create entries.
"""

import pytest
from django.utils import timezone

from apps.analytics.models import AnalyticsPayload
from apps.tasks.models import (
    AnonymizedMetricsPayload,
    DailyMetricsSummary,
    HourlyMetricsCollection,
)

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def _entry_count() -> int:
    from ansible_base.activitystream.models import Entry

    return Entry.objects.count()


def _create_hourly() -> HourlyMetricsCollection:
    return HourlyMetricsCollection.objects.create(
        collector_type="unified_jobs",
        collection_timestamp=timezone.now(),
        raw_data={"jobs": 1},
    )


def _update_hourly(row: HourlyMetricsCollection) -> None:
    row.status = "processed"
    row.save()


def _create_daily() -> DailyMetricsSummary:
    return DailyMetricsSummary.objects.create(
        summary_date=timezone.now().date(),
        aggregated_metrics={"jobs": 1},
    )


def _update_daily(row: DailyMetricsSummary) -> None:
    row.status = "aggregated"
    row.save()


def _create_payload() -> AnonymizedMetricsPayload:
    return AnonymizedMetricsPayload.objects.create(
        summary_date=timezone.now().date(),
        anonymized_data={"jobs": 1},
    )


def _update_payload(row: AnonymizedMetricsPayload) -> None:
    row.status = "sent"
    row.save()


def _create_analytics() -> AnalyticsPayload:
    now = timezone.now()
    return AnalyticsPayload.objects.create(
        collector="controller.unified_jobs_dashboard",
        started_at=now,
        finished_at=now,
        payload={"jobs": 1},
    )


def _update_analytics(row: AnalyticsPayload) -> None:
    row.payload = {"jobs": 2}
    row.save()


@pytest.mark.parametrize(
    ("create", "update"),
    [
        (_create_hourly, _update_hourly),
        (_create_daily, _update_daily),
        (_create_payload, _update_payload),
        (_create_analytics, _update_analytics),
    ],
    ids=["HourlyMetricsCollection", "DailyMetricsSummary", "AnonymizedMetricsPayload", "AnalyticsPayload"],
)
def test_payload_model_writes_create_no_activitystream_entries(create, update):
    """Create, update, and delete of machine-generated payload rows write no entries."""
    before = _entry_count()

    row = create()
    assert _entry_count() == before

    update(row)
    assert _entry_count() == before

    row.delete()
    assert _entry_count() == before


def test_payload_models_are_not_auditable():
    """Payload models must not inherit AuditableModel; user/task models still do."""
    from ansible_base.activitystream.models import AuditableModel

    from apps.tasks.models import Task, TaskExecution

    for model in (HourlyMetricsCollection, DailyMetricsSummary, AnonymizedMetricsPayload, AnalyticsPayload):
        assert not issubclass(model, AuditableModel), f"{model.__name__} should not be auditable"

    for model in (Task, TaskExecution):
        assert issubclass(model, AuditableModel), f"{model.__name__} should remain auditable"
