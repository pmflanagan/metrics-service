"""Tests that model saves avoid redundant JSON serialization work.

Covers the ``update_fields``-aware ``save()`` overrides on
:class:`~apps.tasks.models.HourlyMetricsCollection` and
:class:`~apps.tasks.models.AnonymizedMetricsPayload`, plus the Segment sender's
targeted updates and ``daily_summary`` prefetching.
"""

import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import numpy as np
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.core.json_utils import to_jsonable
from apps.tasks.models import (
    AnonymizedMetricsPayload,
    DailyMetricsSummary,
    HourlyMetricsCollection,
)

pytestmark = [pytest.mark.unit, pytest.mark.django_db]


def _counting_to_jsonable(monkeypatch):
    """Patch the model module's ``to_jsonable`` with a counting wrapper."""
    calls = []

    def counting(value):
        calls.append(value)
        return to_jsonable(value)

    monkeypatch.setattr("apps.tasks.models.to_jsonable", counting)
    return calls


def _forbid_to_jsonable(monkeypatch):
    """Patch the model module's ``to_jsonable`` so any call fails the test."""

    def forbidden(_value):
        raise AssertionError("to_jsonable must not be called for this save")

    monkeypatch.setattr("apps.tasks.models.to_jsonable", forbidden)


def _update_statements(context):
    return [query["sql"] for query in context.captured_queries if query["sql"].lstrip().upper().startswith("UPDATE")]


def _create_collection(**overrides):
    defaults = {
        "collector_type": "job_host_summary_service",
        "collection_timestamp": timezone.now(),
        "raw_data": {"test": "data"},
        "collection_parameters": {"database": "awx"},
    }
    defaults.update(overrides)
    return HourlyMetricsCollection.objects.create(**defaults)


def _create_summary(**overrides):
    defaults = {
        "summary_date": date(2026, 11, 1),
        "status": "anonymized",
        "aggregated_metrics": {"total": 1},
    }
    defaults.update(overrides)
    return DailyMetricsSummary.objects.create(**defaults)


def _create_payload(**overrides):
    defaults = {
        "summary_date": date(2026, 11, 1),
        "anonymized_data": {"data": "test"},
        "status": "pending",
    }
    defaults.update(overrides)
    return AnonymizedMetricsPayload.objects.create(**defaults)


# ---------------------------------------------------------------------------
# create() normalizes exactly once
# ---------------------------------------------------------------------------
def test_hourly_collection_create_normalizes_once(monkeypatch):
    calls = _counting_to_jsonable(monkeypatch)
    raw_data = {"cpu": Decimal("1.5"), "count": np.int64(2), "missing": float("nan")}

    collection = _create_collection(raw_data=raw_data)

    assert len(calls) == 1
    collection.refresh_from_db()
    assert collection.raw_data == {"cpu": "1.5", "count": 2, "missing": None}
    assert collection.data_size_bytes == len(json.dumps(to_jsonable(raw_data)).encode("utf-8"))


def test_anonymized_payload_create_normalizes_once(monkeypatch):
    calls = _counting_to_jsonable(monkeypatch)
    anonymized_data = {"cpu": Decimal("1.5"), "count": np.int64(2), "missing": float("nan")}

    payload = _create_payload(anonymized_data=anonymized_data)

    assert len(calls) == 1
    payload.refresh_from_db()
    assert payload.anonymized_data == {"cpu": "1.5", "count": 2, "missing": None}
    assert payload.payload_size_bytes == len(json.dumps(to_jsonable(anonymized_data)).encode("utf-8"))


# ---------------------------------------------------------------------------
# Status-only saves skip JSON normalization and leave the JSONB column alone
# ---------------------------------------------------------------------------
def test_hourly_collection_status_only_save_skips_json_work(monkeypatch):
    collection = _create_collection()
    old_modified = timezone.now() - timedelta(hours=1)
    HourlyMetricsCollection.objects.filter(pk=collection.pk).update(modified=old_modified)
    collection.refresh_from_db()

    _forbid_to_jsonable(monkeypatch)

    collection.status = "processed"
    with CaptureQueriesContext(connection) as context:
        collection.save(update_fields=["status", "modified"])

    updates = _update_statements(context)
    assert updates
    assert all("raw_data" not in sql for sql in updates)

    collection.refresh_from_db()
    assert collection.status == "processed"
    assert collection.modified > old_modified


def test_anonymized_payload_status_only_save_skips_json_work(monkeypatch):
    payload = _create_payload()
    old_modified = timezone.now() - timedelta(hours=1)
    AnonymizedMetricsPayload.objects.filter(pk=payload.pk).update(modified=old_modified)
    payload.refresh_from_db()

    _forbid_to_jsonable(monkeypatch)

    payload.status = "sending"
    with CaptureQueriesContext(connection) as context:
        payload.save(update_fields=["status", "modified"])

    updates = _update_statements(context)
    assert updates
    assert all("anonymized_data" not in sql for sql in updates)

    payload.refresh_from_db()
    assert payload.status == "sending"
    assert payload.modified > old_modified


# ---------------------------------------------------------------------------
# Updating the JSON column recalculates the stored size
# ---------------------------------------------------------------------------
def test_hourly_collection_raw_data_update_recalculates_size():
    collection = _create_collection(raw_data={"old": 1})

    previous_size = collection.data_size_bytes
    collection.raw_data = {"cpu": Decimal("3.5"), "items": [1, 2, 3]}
    update_fields = ["raw_data", "modified"]

    collection.save(update_fields=update_fields)

    # The caller's list must not be mutated.
    assert update_fields == ["raw_data", "modified"]

    collection.refresh_from_db()
    assert collection.raw_data == {"cpu": "3.5", "items": [1, 2, 3]}
    assert collection.data_size_bytes == len(json.dumps(collection.raw_data).encode("utf-8"))
    assert collection.data_size_bytes != previous_size


def test_anonymized_payload_anonymized_data_update_recalculates_size():
    payload = _create_payload(anonymized_data={"old": 1})

    previous_size = payload.payload_size_bytes
    payload.anonymized_data = {"cpu": Decimal("3.5"), "items": [1, 2, 3]}
    update_fields = ["anonymized_data", "modified"]

    payload.save(update_fields=update_fields)

    assert update_fields == ["anonymized_data", "modified"]

    payload.refresh_from_db()
    assert payload.anonymized_data == {"cpu": "3.5", "items": [1, 2, 3]}
    assert payload.payload_size_bytes == len(json.dumps(payload.anonymized_data).encode("utf-8"))
    assert payload.payload_size_bytes != previous_size


# ---------------------------------------------------------------------------
# Sender handlers only touch the columns they need
# ---------------------------------------------------------------------------
def _assert_no_json_columns_in_updates(context):
    updates = _update_statements(context)
    assert updates
    for sql in updates:
        assert "anonymized_data" not in sql
        assert "aggregated_metrics" not in sql
    return updates


def test_handle_successful_send_does_not_rewrite_json_columns():
    from apps.tasks.collectors.send_anonymized_to_segment import _handle_successful_send

    summary = _create_summary()
    payload = _create_payload(daily_summary=summary, status="sending")
    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

    with CaptureQueriesContext(connection) as context:
        _handle_successful_send(payload, results)

    _assert_no_json_columns_in_updates(context)
    assert results["sent"] == 1

    payload.refresh_from_db()
    summary.refresh_from_db()
    assert payload.status == "sent"
    assert payload.sent_at is not None
    assert summary.status == "sent"


def test_process_single_payload_does_not_rewrite_json_columns():
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    summary = _create_summary()
    payload = _create_payload(daily_summary=summary, status="pending")
    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

    with (
        patch(
            "apps.tasks.collectors.send_anonymized_to_segment.send_to_segment",
            return_value={"status": "success"},
        ),
        CaptureQueriesContext(connection) as context,
    ):
        _process_single_payload(payload, results)

    _assert_no_json_columns_in_updates(context)
    assert results["sent"] == 1


@pytest.mark.parametrize("by_id", [True, False])
def test_get_payloads_to_send_prefetches_daily_summary(by_id):
    from apps.tasks.collectors.send_anonymized_to_segment import _get_payloads_to_send

    summary = _create_summary(summary_date=date(2026, 11, 2))
    payload = _create_payload(summary_date=date(2026, 11, 2), daily_summary=summary, status="pending")
    stale_threshold = timezone.now() - timedelta(hours=1)

    payloads = list(
        _get_payloads_to_send(
            payload_id=payload.id if by_id else None,
            max_payloads=10,
            stale_threshold=stale_threshold,
        )
    )

    with CaptureQueriesContext(connection) as context:
        assert [p.daily_summary.id for p in payloads] == [summary.id]

    assert context.captured_queries == []
