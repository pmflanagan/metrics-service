"""
Unit tests for apps/tasks/collectors/send_anonymized_to_segment.py

Tests cover:
- SEGMENT_TEST_MODE=True appends '_Test' suffix to the Segment event name
- SEGMENT_TEST_MODE=False (default) leaves the event name unchanged
- Helper functions: _get_payloads_to_send, _handle_successful_send,
  _handle_failed_send, and the main send_anonymized_to_segment task
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.tasks.collectors.send_anonymized_to_segment import (
    _handle_failed_send,
    _handle_successful_send,
    _handle_unavailable_send,
    _process_single_payload,
    send_anonymized_to_segment,
    send_to_segment,
)

# ---------------------------------------------------------------------------
# SEGMENT_TEST_MODE behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSegmentTestMode:
    """Tests for the SEGMENT_TEST_MODE event-name suffix feature."""

    def _make_payload(self, event_name: str = "Controller Metrics Daily Rollup") -> MagicMock:
        """Return a minimal mock AnonymizedMetricsPayload."""
        payload = MagicMock()
        payload.id = 1
        payload.status = "pending"
        payload.retry_count = 0
        payload.segment_user_id = "test-user-id"
        payload.segment_event_name = event_name
        payload.anonymized_data = {"test": "data"}
        payload.daily_summary = None
        payload.can_retry.return_value = True
        return payload

    @patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment")
    def test_test_mode_enabled_appends_test_suffix(self, mock_send_to_segment, settings):
        """When SEGMENT_TEST_MODE=True, '_Test' is appended to the event name."""
        settings.SEGMENT_TEST_MODE = True
        mock_send_to_segment.return_value = {"status": "success", "data": {}}

        payload = self._make_payload("Controller Metrics Daily Rollup")
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _process_single_payload(payload, results)

        mock_send_to_segment.assert_called_once()
        _, call_kwargs = mock_send_to_segment.call_args
        assert call_kwargs["event_name"] == "Controller Metrics Daily Rollup_Test"
        assert results["sent"] == 1

    @patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment")
    def test_test_mode_disabled_keeps_original_event_name(self, mock_send_to_segment, settings):
        """When SEGMENT_TEST_MODE=False, the event name is sent unchanged."""
        settings.SEGMENT_TEST_MODE = False
        mock_send_to_segment.return_value = {"status": "success", "data": {}}

        payload = self._make_payload("Controller Metrics Daily Rollup")
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _process_single_payload(payload, results)

        mock_send_to_segment.assert_called_once()
        _, call_kwargs = mock_send_to_segment.call_args
        assert call_kwargs["event_name"] == "Controller Metrics Daily Rollup"
        assert results["sent"] == 1

    @patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment")
    def test_test_mode_absent_keeps_original_event_name(self, mock_send_to_segment, settings):
        """When SEGMENT_TEST_MODE is not set at all, the event name is unchanged."""
        # Remove the attribute if it exists so getattr falls back to the default
        if hasattr(settings, "SEGMENT_TEST_MODE"):
            delattr(settings, "SEGMENT_TEST_MODE")
        mock_send_to_segment.return_value = {"status": "success", "data": {}}

        payload = self._make_payload("My Event")
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _process_single_payload(payload, results)

        _, call_kwargs = mock_send_to_segment.call_args
        assert call_kwargs["event_name"] == "My Event"


# ---------------------------------------------------------------------------
# _handle_successful_send
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleSuccessfulSend:
    """Tests for _handle_successful_send helper."""

    def test_marks_payload_sent_and_increments_counter(self):
        payload = MagicMock()
        payload.daily_summary = None
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _handle_successful_send(payload, results)

        assert payload.status == "sent"
        payload.save.assert_called_once()
        assert results["sent"] == 1

    def test_updates_daily_summary_status(self):
        payload = MagicMock()
        payload.daily_summary = MagicMock()
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _handle_successful_send(payload, results)

        assert payload.daily_summary.status == "sent"
        payload.daily_summary.save.assert_called_once()

    def test_summary_save_failure_does_not_raise(self):
        """A failure updating the daily summary must not propagate."""
        payload = MagicMock()
        payload.daily_summary = MagicMock()
        payload.daily_summary.save.side_effect = Exception("DB error")
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _handle_successful_send(payload, results)

        assert results["sent"] == 1


# ---------------------------------------------------------------------------
# _handle_failed_send
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleFailedSend:
    """Tests for _handle_failed_send helper."""

    def test_sets_retry_status_and_increments_retry_count(self):
        payload = MagicMock()
        payload.retry_count = 1
        payload.max_retries = 3
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        segment_result = {"status": "error", "error": "timeout"}
        _handle_failed_send(payload, segment_result, results)

        assert payload.status == "retry"
        assert payload.retry_count == 2
        assert "timeout" in payload.error_message
        payload.save.assert_called_once()
        assert results["failed"] == 1


# ---------------------------------------------------------------------------
# _handle_unavailable_send
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleUnavailableSend:
    """Test the _handle_unavailable_send function."""

    def test_sets_unavailable_status_and_increments_skipped(self):
        payload = MagicMock()
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        segment_result = {"status": "unavailable", "error": "segment_not_available"}
        _handle_unavailable_send(payload, segment_result, results)

        assert payload.status == "unavailable"
        assert "segment_not_available" in payload.error_message
        payload.save.assert_called_once()
        assert results["skipped"] == 1
        assert results["failed"] == 0


# ---------------------------------------------------------------------------
# send_anonymized_to_segment (top-level task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.django_db
class TestSendAnonymizedToSegmentTask:
    """Tests for the send_anonymized_to_segment task function."""

    @pytest.fixture(autouse=True)
    def enable_anonymized_data_collection(self, settings):
        settings.FEATURE = {"ANONYMIZED_DATA_COLLECTION": True}

    @patch("apps.tasks.collectors.send_anonymized_to_segment._get_payloads_to_send")
    def test_returns_success_with_empty_payload_list(self, mock_get_payloads):
        """No payloads → success result with all-zero counters."""
        mock_get_payloads.return_value = []

        result = send_anonymized_to_segment()

        assert result["status"] == "success"
        assert result["results"] == {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}
        assert result["total_processed"] == 0

    @patch("apps.tasks.collectors.send_anonymized_to_segment._get_payloads_to_send")
    def test_returns_error_on_unexpected_exception(self, mock_get_payloads):
        """An unexpected exception must return an error result, not raise."""
        mock_get_payloads.side_effect = RuntimeError("boom")

        result = send_anonymized_to_segment()

        assert result["status"] == "error"
        assert "boom" in result["error"]

    @patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment")
    @patch("apps.tasks.collectors.send_anonymized_to_segment._get_payloads_to_send")
    def test_processes_each_payload(self, mock_get_payloads, mock_send_to_segment, settings):
        """All payloads in the list are processed and sent."""
        settings.SEGMENT_TEST_MODE = False
        mock_send_to_segment.return_value = {"status": "success", "data": {}}

        def make_payload(pid: int) -> MagicMock:
            p = MagicMock()
            p.id = pid
            p.status = "pending"
            p.retry_count = 0
            p.segment_user_id = f"user-{pid}"
            p.segment_event_name = "My Event"
            p.anonymized_data = {}
            p.daily_summary = None
            p.can_retry.return_value = True
            return p

        mock_get_payloads.return_value = [make_payload(1), make_payload(2)]

        result = send_anonymized_to_segment()

        assert result["status"] == "success"
        assert result["results"]["sent"] == 2
        assert result["total_processed"] == 2


# ---------------------------------------------------------------------------
# send_to_segment structured results
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendToSegmentDataSize:
    """Tests for skipping payload re-serialization when the size is pre-computed."""

    @patch("apps.tasks.collectors.send_anonymized_to_segment.json_size_bytes")
    @patch("metrics_utility.library.storage.segment.StorageSegment")
    @patch("metrics_utility.library.storage.segment.SEGMENT_AVAILABLE", True)
    def test_supplied_data_size_skips_serialization(self, mock_storage_class, mock_json_size, settings):
        """A caller-provided size is used as-is and the payload is never serialized."""
        settings.SEGMENT_WRITE_KEY = "test-key"
        mock_storage_class.return_value.put.return_value = [{"chunk": 1}]

        result = send_to_segment("user-123", "test_event", {"data": "test"}, data_size_bytes=42)

        mock_json_size.assert_not_called()
        assert result["status"] == "success"
        assert result["data_size_bytes"] == 42

    @patch("apps.tasks.collectors.send_anonymized_to_segment.json_size_bytes", return_value=7)
    @patch("metrics_utility.library.storage.segment.StorageSegment")
    @patch("metrics_utility.library.storage.segment.SEGMENT_AVAILABLE", True)
    def test_omitted_data_size_serializes_payload(self, mock_storage_class, mock_json_size, settings):
        """Without a pre-computed size the payload is measured as before."""
        settings.SEGMENT_WRITE_KEY = "test-key"
        mock_storage_class.return_value.put.return_value = [{"chunk": 1}]

        result = send_to_segment("user-123", "test_event", {"data": "test"})

        mock_json_size.assert_called_once_with({"data": "test"})
        assert result["data_size_bytes"] == 7

    @patch("apps.tasks.collectors.send_anonymized_to_segment.send_to_segment")
    def test_process_single_payload_passes_precomputed_size(self, mock_send_to_segment, settings):
        """_process_single_payload forwards the stored payload size to send_to_segment."""
        settings.SEGMENT_TEST_MODE = False
        mock_send_to_segment.return_value = {"status": "success", "data": {}}

        payload = MagicMock()
        payload.id = 1
        payload.status = "pending"
        payload.segment_user_id = "test-user-id"
        payload.segment_event_name = "My Event"
        payload.anonymized_data = {"test": "data"}
        payload.payload_size_bytes = 1234
        payload.daily_summary = None
        payload.can_retry.return_value = True
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        _process_single_payload(payload, results)

        _, call_kwargs = mock_send_to_segment.call_args
        assert call_kwargs["data_size_bytes"] == 1234
        assert results["sent"] == 1


@pytest.mark.unit
class TestSendToSegmentStructured:
    """Tests for send_to_segment structured error handling."""

    def test_missing_write_key_returns_auth_error(self, settings):
        """When SEGMENT_WRITE_KEY is missing, returns auth error."""
        settings.SEGMENT_WRITE_KEY = None

        result = send_to_segment("user-123", "test_event", {"data": "test"})

        assert result["status"] == "unavailable"
        assert "SEGMENT_WRITE_KEY not configured" in result["error"]

    @patch("metrics_utility.library.storage.segment.StorageSegment")
    def test_network_error_categorized_correctly(self, mock_storage_class, settings):
        """When network error occurs, categorizes as network_error."""
        settings.SEGMENT_WRITE_KEY = "test-key"
        mock_storage_instance = MagicMock()
        mock_storage_class.return_value = mock_storage_instance
        mock_storage_instance.put.side_effect = Exception("timeout occurred")

        result = send_to_segment("user-123", "test_event", {"data": "test"})

        assert result["status"] == "error"
        assert "timeout occurred" in result["error"]

    @patch("apps.tasks.collectors.send_anonymized_to_segment.logger")
    def test_logs_error_when_retries_exhausted(self, mock_logger):
        """Logs ERROR with structured fields when max retries reached."""
        # In an above test, we already test what happens when max retries hasn't been reached
        payload = MagicMock()
        payload.id = 123
        payload.summary_date = "2024-01-01"
        payload.retry_count = 2
        payload.max_retries = 3
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        segment_result = {"status": "error", "error": "Invalid key"}
        _handle_failed_send(payload, segment_result, results)

        mock_logger.error.assert_called_once()
        log_call = mock_logger.error.call_args[0][0]
        assert "Segment send failed, not retrying" in log_call

    def test_import_error_returns_unavailable(self, settings):
        """When metrics_utility import fails, returns unavailable status."""
        settings.SEGMENT_WRITE_KEY = "test-key"

        with patch("builtins.__import__", side_effect=ImportError("No module named 'metrics_utility'")):
            result = send_to_segment("user-123", "test_event", {"data": "test"})

        assert result["status"] == "unavailable"
        assert "segment_not_available" in result["error"]

    @patch("metrics_utility.library.storage.segment.SEGMENT_AVAILABLE", False)
    def test_segment_not_available_returns_unavailable(self, settings):
        """When SEGMENT_AVAILABLE is False, returns unavailable status."""
        settings.SEGMENT_WRITE_KEY = "test-key"

        result = send_to_segment("user-123", "test_event", {"data": "test"})

        assert result["status"] == "unavailable"
        assert "segment_not_available" in result["error"]

    @patch("metrics_utility.library.storage.segment.StorageSegment")
    @patch("metrics_utility.library.storage.segment.SEGMENT_AVAILABLE", True)
    def test_successful_send_logs_and_returns_success(self, mock_storage_class, settings):
        """When send is successful, logs success and returns chunks info."""
        settings.SEGMENT_WRITE_KEY = "test-key"
        mock_storage_instance = MagicMock()
        mock_storage_class.return_value = mock_storage_instance
        mock_storage_instance.put.return_value = [{"chunk": 1}, {"chunk": 2}]

        result = send_to_segment("user-123", "test_event", {"data": "test"})

        assert result["status"] == "success"
        assert result["chunks_sent"] == 2
        assert "data_size_bytes" in result

    @patch("apps.tasks.models.AnonymizedMetricsPayload")
    def test_get_payloads_includes_unavailable_status(self, mock_model):
        """Test that _get_payloads_to_send includes 'unavailable' status payloads."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.tasks.collectors.send_anonymized_to_segment import _get_payloads_to_send

        stale_threshold = timezone.now() - timedelta(minutes=10)

        # Call without payload_id to test the general query path
        _get_payloads_to_send(payload_id=None, max_payloads=5, stale_threshold=stale_threshold)

        mock_model.objects.filter.assert_called_once()
        filter_call_args = mock_model.objects.filter.call_args[0]
        # Check that the Q object includes unavailable in the status list
        assert any("unavailable" in str(arg) for arg in filter_call_args)

    @patch("apps.tasks.collectors.send_anonymized_to_segment.logger")
    def test_handle_failed_send_structured_logging(self, mock_logger):
        """Test enhanced structured logging for failed sends."""
        from apps.tasks.collectors.send_anonymized_to_segment import _handle_failed_send

        payload = MagicMock()
        payload.id = 123
        payload.summary_date = "2024-01-01"
        payload.retry_count = 1
        payload.max_retries = 5
        results = {"sent": 0, "failed": 0, "skipped": 0, "recovered": 0}

        segment_result = {"status": "error", "error": "Network timeout"}
        _handle_failed_send(payload, segment_result, results)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "extra" in call_args[1]
        extra_data = call_args[1]["extra"]
        assert extra_data["payload_id"] == 123
        assert extra_data["summary_date"] == "2024-01-01"
        assert extra_data["retry_count"] == 2
        assert extra_data["error"] == "Network timeout"
