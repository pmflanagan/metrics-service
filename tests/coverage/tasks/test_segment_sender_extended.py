"""
Extended unit tests for apps/tasks/collectors/send_anonymized_to_segment.py.

Targets the uncovered lines from the previous coverage run:
  - Lines 64-67:   _handle_successful_send — daily_summary update + exception path
  - Lines 133-134: _process_single_payload — stale "sending" recovery
  - Lines 138-142: _process_single_payload — retry limit exceeded (can_retry() False)
  - Line 168:      _process_single_payload — success branch
  - Lines 172-177: _process_single_payload — failure and unexpected-exception branches
  - Lines 194-196: send_to_segment — ImportError from metrics_utility
  - Line 199:      send_to_segment — SEGMENT_AVAILABLE=False
  - Lines 213-241: send_to_segment — full StorageSegment path (success + exception)
  - Lines 311-313: send_anonymized_to_segment — top-level exception handler
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def payload(db):
    """Create a fresh pending AnonymizedMetricsPayload for each test."""
    from apps.tasks.models import AnonymizedMetricsPayload

    AnonymizedMetricsPayload.objects.all().delete()
    return AnonymizedMetricsPayload.objects.create(
        summary_date=timezone.now().date() - timedelta(days=1),
        anonymized_data={"key": "value"},
        status="pending",
        segment_user_id="test-user-123",
        segment_event_name="daily_metrics_rollup",
    )


# ---------------------------------------------------------------------------
# _handle_successful_send — daily_summary branch (lines 63-67)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_successful_send_updates_daily_summary(db):
    """When daily_summary is set, its status should be set to 'sent'."""
    from apps.tasks.collectors.send_anonymized_to_segment import _handle_successful_send
    from apps.tasks.models import AnonymizedMetricsPayload, DailyMetricsSummary

    summary = DailyMetricsSummary.objects.create(summary_date=timezone.now().date() - timedelta(days=2))
    p = AnonymizedMetricsPayload.objects.create(
        summary_date=summary.summary_date,
        anonymized_data={"k": "v"},
        status="pending",
        daily_summary=summary,
    )

    results = {"sent": 0}
    _handle_successful_send(p, results)

    summary.refresh_from_db()
    assert summary.status == "sent"
    assert results["sent"] == 1


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_successful_send_daily_summary_exception_is_swallowed(db):
    """An exception raised when updating daily_summary must not propagate."""
    from apps.tasks.collectors.send_anonymized_to_segment import _handle_successful_send
    from apps.tasks.models import AnonymizedMetricsPayload, DailyMetricsSummary

    summary = DailyMetricsSummary.objects.create(summary_date=timezone.now().date() - timedelta(days=3))
    p = AnonymizedMetricsPayload.objects.create(
        summary_date=summary.summary_date,
        anonymized_data={"k": "v"},
        status="pending",
        daily_summary=summary,
    )

    results = {"sent": 0}
    # Patch DailyMetricsSummary.save so the second save (summary update) raises
    with patch.object(DailyMetricsSummary, "save", side_effect=Exception("DB gone")):
        # Should not raise — the except branch must swallow the error
        _handle_successful_send(p, results)

    assert results["sent"] == 1
    p.refresh_from_db()
    assert p.status == "sent"


# ---------------------------------------------------------------------------
# _handle_failed_send — max retries exceeded (lines 64-67 of _handle_failed_send)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_failed_send_max_retries_sets_failed(payload):
    """When retry_count reaches max_retries, status must become 'failed'."""
    from apps.tasks.collectors.send_anonymized_to_segment import _handle_failed_send

    payload.retry_count = payload.max_retries - 1  # one more will tip it over
    payload.save()

    results = {"failed": 0}
    _handle_failed_send(payload, {"error": "timeout"}, results)

    payload.refresh_from_db()
    assert payload.status == "failed"
    assert results["failed"] == 1


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_failed_send_below_max_retries_sets_retry(payload):
    """When retry_count is below max_retries, status must become 'retry'."""
    from apps.tasks.collectors.send_anonymized_to_segment import _handle_failed_send

    payload.retry_count = 0
    payload.save()

    results = {"failed": 0}
    _handle_failed_send(payload, {"error": "timeout"}, results)

    payload.refresh_from_db()
    assert payload.status == "retry"
    assert results["failed"] == 1


# ---------------------------------------------------------------------------
# _process_single_payload — stale recovery (lines 133-134)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_process_single_payload_recovers_stale_sending(payload):
    """Payload stuck in 'sending' should be recovered and processed."""
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    payload.status = "sending"
    payload.save()

    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

    with patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment") as mock_send:
        mock_send.return_value = {"status": "success"}
        _process_single_payload(payload, results)

    assert results["recovered"] == 1


# ---------------------------------------------------------------------------
# _process_single_payload — retry limit exceeded (lines 138-142)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_process_single_payload_skips_when_cannot_retry(payload):
    """A 'retry' payload that cannot retry must be marked failed and skipped."""
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    payload.status = "retry"
    payload.retry_count = payload.max_retries  # can_retry() returns False
    payload.save()

    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}
    _process_single_payload(payload, results)

    payload.refresh_from_db()
    assert payload.status == "failed"
    assert payload.error_message == "Max retries exceeded"
    assert results["skipped"] == 1


# ---------------------------------------------------------------------------
# _process_single_payload — success path (line 168)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_process_single_payload_success_path(payload):
    """When send_to_segment returns success the payload is marked sent."""
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

    with patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment") as mock_send:
        mock_send.return_value = {"status": "success"}
        _process_single_payload(payload, results)

    payload.refresh_from_db()
    assert payload.status == "sent"
    assert results["sent"] == 1


# ---------------------------------------------------------------------------
# _process_single_payload — failure path (lines 172-173)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_process_single_payload_failure_path(payload):
    """When send_to_segment returns a non-success/non-unavailable status, _handle_failed_send is called."""
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

    with patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment") as mock_send:
        mock_send.return_value = {"status": "error", "error": "connection refused"}
        _process_single_payload(payload, results)

    assert results["failed"] == 1


# ---------------------------------------------------------------------------
# _process_single_payload — unexpected exception path (lines 174-177)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_process_single_payload_exception_triggers_failed_send(payload):
    """An unexpected exception inside send_to_segment must call _handle_failed_send."""
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

    with patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment", side_effect=RuntimeError("boom")):
        _process_single_payload(payload, results)

    assert results["failed"] == 1


# ---------------------------------------------------------------------------
# _process_single_payload — SEGMENT_TEST_MODE (line 151)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
def test_process_single_payload_test_mode_appends_suffix(payload):
    """When SEGMENT_TEST_MODE is True the event name gets a _Test suffix."""
    from apps.tasks.collectors.send_anonymized_to_segment import _process_single_payload

    results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}
    captured = {}

    def fake_send(user_id, event_name, segment_data, segment_meta=None, **kwargs):
        captured["event_name"] = event_name
        return {"status": "success"}

    with (
        patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment", side_effect=fake_send),
        patch("apps.tasks.collectors.send_anonymized_to_segment.settings") as mock_settings,
    ):
        mock_settings.SEGMENT_TEST_MODE = True
        _process_single_payload(payload, results)

    assert captured.get("event_name", "").endswith("_Test")


# ---------------------------------------------------------------------------
# send_to_segment — ImportError (lines 194-196)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_send_to_segment_import_error_returns_unavailable():
    """If metrics_utility.library.storage.segment cannot be imported, return unavailable."""
    import builtins

    from apps.tasks.collectors.send_anonymized_to_segment import send_to_segment

    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "metrics_utility.library.storage.segment":
            raise ImportError("no module")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        result = send_to_segment(user_id="u1", event_name="ev", segment_data={})

    assert result["status"] == "unavailable"


# ---------------------------------------------------------------------------
# send_to_segment — SEGMENT_AVAILABLE=False (line 199)
# ---------------------------------------------------------------------------


def _make_segment_import(segment_available: bool, storage_segment):
    """
    Return a builtins.__import__ replacement that intercepts the
    metrics_utility.library.storage.segment import and injects a fake module.
    """
    import builtins
    import types

    original = builtins.__import__

    fake_mod = types.ModuleType("metrics_utility.library.storage.segment")
    fake_mod.SEGMENT_AVAILABLE = segment_available
    fake_mod.StorageSegment = storage_segment

    def _import(name, *args, **kwargs):
        if name == "metrics_utility.library.storage.segment":
            return fake_mod
        return original(name, *args, **kwargs)

    return _import


@pytest.mark.unit
def test_send_to_segment_segment_not_available_returns_unavailable():
    """When SEGMENT_AVAILABLE is False, return unavailable without sending."""
    from apps.tasks.collectors.send_anonymized_to_segment import send_to_segment

    mock_import = _make_segment_import(segment_available=False, storage_segment=None)
    with patch("builtins.__import__", side_effect=mock_import):
        result = send_to_segment(user_id="u1", event_name="ev", segment_data={})

    assert result["status"] == "unavailable"


# ---------------------------------------------------------------------------
# send_to_segment — full StorageSegment path (lines 213-237)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_send_to_segment_success_with_storage_segment():
    """Happy path: StorageSegment.put() succeeds and returns chunk list."""
    from django.test import override_settings

    from apps.tasks.collectors.send_anonymized_to_segment import send_to_segment

    mock_storage = MagicMock()
    mock_storage.put.return_value = ["chunk1", "chunk2"]
    mock_storage_cls = MagicMock(return_value=mock_storage)

    mock_import = _make_segment_import(segment_available=True, storage_segment=mock_storage_cls)
    with override_settings(SEGMENT_WRITE_KEY="test-write-key"), patch("builtins.__import__", side_effect=mock_import):
        result = send_to_segment(user_id="user1", event_name="test_event", segment_data={"a": 1})

    assert result["status"] == "success"
    assert result["chunks_sent"] == 2


@pytest.mark.unit
def test_send_to_segment_no_write_key_returns_unavailable():
    """When SEGMENT_WRITE_KEY is absent, return unavailable."""
    from django.test import override_settings

    from apps.tasks.collectors.send_anonymized_to_segment import send_to_segment

    mock_import = _make_segment_import(segment_available=True, storage_segment=MagicMock())
    with override_settings(SEGMENT_WRITE_KEY=None), patch("builtins.__import__", side_effect=mock_import):
        result = send_to_segment(user_id="u1", event_name="ev", segment_data={})

    assert result["status"] == "unavailable"


@pytest.mark.unit
def test_send_to_segment_storage_segment_exception_returns_error():
    """An exception inside StorageSegment.put() must be caught and return error."""
    from django.test import override_settings

    from apps.tasks.collectors.send_anonymized_to_segment import send_to_segment

    mock_storage = MagicMock()
    mock_storage.put.side_effect = Exception("network timeout")
    mock_storage_cls = MagicMock(return_value=mock_storage)

    mock_import = _make_segment_import(segment_available=True, storage_segment=mock_storage_cls)
    with override_settings(SEGMENT_WRITE_KEY="key"), patch("builtins.__import__", side_effect=mock_import):
        result = send_to_segment(user_id="u1", event_name="ev", segment_data={"x": 1})

    assert result["status"] == "error"


@pytest.mark.unit
def test_send_to_segment_none_chunks_defaults_to_one():
    """When storage.put() returns None, chunk_count should default to 1."""
    from django.test import override_settings

    from apps.tasks.collectors.send_anonymized_to_segment import send_to_segment

    mock_storage = MagicMock()
    mock_storage.put.return_value = None
    mock_storage_cls = MagicMock(return_value=mock_storage)

    mock_import = _make_segment_import(segment_available=True, storage_segment=mock_storage_cls)
    with override_settings(SEGMENT_WRITE_KEY="key"), patch("builtins.__import__", side_effect=mock_import):
        result = send_to_segment(user_id="u1", event_name="ev", segment_data={"x": 1})

    assert result["status"] == "success"
    assert result["chunks_sent"] == 1


# ---------------------------------------------------------------------------
# send_anonymized_to_segment — top-level exception handler (lines 311-313)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
@override_settings(FEATURE={"ANONYMIZED_DATA_COLLECTION": True})
def test_send_anonymized_to_segment_top_level_exception_returns_error(payload):
    """A catastrophic exception in the main function must return an error result."""
    from apps.tasks.collectors.send_anonymized_to_segment import send_anonymized_to_segment

    with patch(
        "apps.tasks.collectors.send_anonymized_to_segment._get_payloads_to_send",
        side_effect=Exception("catastrophic"),
    ):
        result = send_anonymized_to_segment()

    assert result["status"] == "error"
    assert "Send task failed" in result.get("error", "")
