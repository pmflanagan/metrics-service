"""
Unit tests for apps/tasks/utils.py.
Targets 11.81% → ~93% coverage.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone


# ---------------------------------------------------------------------------
# create_task_result
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_create_task_result_minimal():
    from apps.tasks.utils import create_task_result

    result = create_task_result("success")
    assert result["status"] == "success"
    assert "timestamp" in result
    assert "error" not in result


@pytest.mark.unit
def test_create_task_result_with_data():
    from apps.tasks.utils import create_task_result

    result = create_task_result("success", {"count": 5})
    assert result["count"] == 5
    assert result["status"] == "success"


@pytest.mark.unit
def test_create_task_result_with_error():
    from apps.tasks.utils import create_task_result

    result = create_task_result("error", error="something broke")
    assert result["status"] == "error"
    assert result["error"] == "something broke"


@pytest.mark.unit
def test_create_task_result_with_data_and_error():
    from apps.tasks.utils import create_task_result

    result = create_task_result("error", {"detail": "x"}, error="boom")
    assert result["status"] == "error"
    assert result["error"] == "boom"
    assert result["detail"] == "x"


# ---------------------------------------------------------------------------
# parse_datetime_string
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_parse_datetime_string_z_suffix():
    from apps.tasks.utils import parse_datetime_string

    result = parse_datetime_string("2024-01-01T00:00:00Z")
    assert result is not None
    assert result.tzinfo is not None
    assert result.year == 2024


@pytest.mark.unit
def test_parse_datetime_string_offset():
    from apps.tasks.utils import parse_datetime_string

    result = parse_datetime_string("2024-06-15T12:30:00+05:00")
    assert result is not None
    assert result.tzinfo is not None


@pytest.mark.unit
def test_parse_datetime_string_naive_gets_utc():
    from apps.tasks.utils import parse_datetime_string

    result = parse_datetime_string("2024-01-01T10:00:00")
    assert result is not None
    assert result.tzinfo is not None
    assert result.tzinfo == UTC


@pytest.mark.unit
def test_parse_datetime_string_invalid_returns_none():
    from apps.tasks.utils import parse_datetime_string

    assert parse_datetime_string("not-a-date") is None
    assert parse_datetime_string("2024-13-99") is None


@pytest.mark.unit
def test_parse_datetime_string_none_returns_none():
    from apps.tasks.utils import parse_datetime_string

    assert parse_datetime_string(None) is None


@pytest.mark.unit
def test_parse_datetime_string_empty_returns_none():
    from apps.tasks.utils import parse_datetime_string

    assert parse_datetime_string("") is None


# ---------------------------------------------------------------------------
# generate_salt
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_generate_salt_is_uuid4():
    from apps.tasks.utils import generate_salt

    salt = generate_salt()
    assert isinstance(salt, str)
    assert len(salt) == 36
    # Verify it parses as a valid UUID
    parsed = uuid.UUID(salt)
    assert str(parsed) == salt


@pytest.mark.unit
def test_generate_salt_is_unique():
    from apps.tasks.utils import generate_salt

    assert generate_salt() != generate_salt()


# ---------------------------------------------------------------------------
# _serialize_args
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_serialize_args_datetime_to_iso():
    from apps.tasks.utils import _serialize_args

    dt = datetime(2024, 1, 1, 12, 0, 0)
    result = _serialize_args({"ts": dt, "x": 1})
    assert isinstance(result["ts"], str)
    assert "2024" in result["ts"]
    assert result["x"] == 1


@pytest.mark.unit
def test_serialize_args_non_datetime_unchanged():
    from apps.tasks.utils import _serialize_args

    result = _serialize_args({"name": "hello", "count": 42, "flag": True})
    assert result["name"] == "hello"
    assert result["count"] == 42
    assert result["flag"] is True


@pytest.mark.unit
def test_serialize_args_none_returns_empty():
    from apps.tasks.utils import _serialize_args

    assert _serialize_args(None) == {}


@pytest.mark.unit
def test_serialize_args_empty_dict():
    from apps.tasks.utils import _serialize_args

    assert _serialize_args({}) == {}


# ---------------------------------------------------------------------------
# log_task_execution
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_log_task_execution_info():
    from unittest.mock import patch

    from apps.tasks.utils import log_task_execution

    with patch("apps.tasks.utils.logger") as mock_logger:
        log_task_execution("my_task", "start", "doing stuff")

    mock_logger.info.assert_called_once()
    assert "my_task" in mock_logger.info.call_args[0][0]


@pytest.mark.unit
def test_log_task_execution_error_level():
    from unittest.mock import patch

    from apps.tasks.utils import log_task_execution

    with patch("apps.tasks.utils.logger") as mock_logger:
        log_task_execution("my_task", "error", "failed", level="error")

    mock_logger.error.assert_called_once()
    assert "my_task" in mock_logger.error.call_args[0][0]


@pytest.mark.unit
def test_log_task_execution_no_details():
    from unittest.mock import patch

    from apps.tasks.utils import log_task_execution

    with patch("apps.tasks.utils.logger") as mock_logger:
        log_task_execution("my_task", "complete")

    mock_logger.info.assert_called_once()
    assert "my_task" in mock_logger.info.call_args[0][0]


# ---------------------------------------------------------------------------
# ensure_django_setup
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_ensure_django_setup_skips_when_configured():
    from apps.tasks.utils import ensure_django_setup

    with patch("django.setup") as mock_setup:
        ensure_django_setup()
        mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# handle_task_error
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_updates_task_status(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import handle_task_error

    task = Task.objects.create(name="fail_task", function_name="hello_world", task_data={}, created_by=user)
    result = handle_task_error(task_instance=task, error_message="test failure")

    task.refresh_from_db()
    assert task.status == "failed"
    assert task.error_message == "test failure"
    assert result["status"] == "error"


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_increments_attempts_when_pending(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import handle_task_error

    task = Task.objects.create(
        name="fail_task", function_name="hello_world", task_data={}, created_by=user, status="pending", attempts=0
    )
    handle_task_error(task_instance=task, error_message="boom")
    task.refresh_from_db()
    assert task.attempts == 1


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_no_task_instance():
    from apps.tasks.utils import handle_task_error

    result = handle_task_error(error_message="generic error")
    assert result["status"] == "error"
    assert result["error"] == "generic error"


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_with_exception(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import handle_task_error

    task = Task.objects.create(name="t", function_name="hello_world", task_data={}, created_by=user)
    exc = ValueError("something went wrong")
    result = handle_task_error(task_instance=task, exception=exc)
    assert result["status"] == "error"
    assert "something went wrong" in result["error"]


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_by_task_id(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import handle_task_error

    task = Task.objects.create(name="t2", function_name="hello_world", task_data={}, created_by=user)
    result = handle_task_error(task_id=task.id, error_message="by id")
    assert result["status"] == "error"
    task.refresh_from_db()
    assert task.status == "failed"


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_logs_warning_when_retriable(user):
    """handle_task_error logs WARNING (not ERROR) when the task still has retry attempts remaining."""
    from unittest.mock import patch

    from apps.tasks.models import Task
    from apps.tasks.utils import handle_task_error

    # attempts=0, max_attempts=3 → still retriable
    task = Task.objects.create(
        name="retriable_task",
        function_name="hello_world",
        task_data={},
        created_by=user,
        status="pending",
        attempts=0,
        max_attempts=3,
    )
    with patch("apps.tasks.utils.logger") as mock_logger:
        result = handle_task_error(task_instance=task, error_message="transient failure")

    assert result["status"] == "error"
    mock_logger.warning.assert_called_once_with("transient failure")
    # The main error message must NOT be logged at ERROR level
    error_calls = [str(call) for call in mock_logger.error.call_args_list]
    assert not any("transient failure" in c for c in error_calls)


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_logs_error_when_exhausted(user):
    """handle_task_error logs ERROR when the task has exhausted all retry attempts."""
    from unittest.mock import patch

    from apps.tasks.models import Task
    from apps.tasks.utils import handle_task_error

    # attempts=2, max_attempts=3 → after increment will equal max_attempts (exhausted)
    task = Task.objects.create(
        name="exhausted_task",
        function_name="hello_world",
        task_data={},
        created_by=user,
        status="pending",
        attempts=2,
        max_attempts=3,
    )
    with patch("apps.tasks.utils.logger") as mock_logger:
        result = handle_task_error(task_instance=task, error_message="final failure")

    assert result["status"] == "error"
    mock_logger.error.assert_any_call("final failure")
    # warning must NOT be called with the main error message
    warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
    assert not any("final failure" in c for c in warning_calls)


@pytest.mark.unit
@pytest.mark.django_db
def test_handle_task_error_logs_error_when_no_task_instance():
    """handle_task_error defaults to ERROR when there is no task context (safety fallback)."""
    from unittest.mock import patch

    from apps.tasks.utils import handle_task_error

    with patch("apps.tasks.utils.logger") as mock_logger:
        result = handle_task_error(error_message="no context error")

    assert result["status"] == "error"
    mock_logger.error.assert_any_call("no context error")


# ---------------------------------------------------------------------------
# update_task_status
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_update_task_status_running_sets_started_at(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import update_task_status

    task = Task.objects.create(name="t", function_name="hello_world", task_data={}, created_by=user, status="pending")
    update_task_status(task, status="running")
    task.refresh_from_db()
    assert task.status == "running"
    assert task.started_at is not None
    assert task.attempts == 1


@pytest.mark.unit
@pytest.mark.django_db
def test_update_task_status_completed_clears_error(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import update_task_status

    task = Task.objects.create(
        name="t", function_name="hello_world", task_data={}, created_by=user, status="running", error_message="old"
    )
    update_task_status(task, status="completed")
    task.refresh_from_db()
    assert task.status == "completed"
    assert task.error_message == ""
    assert task.completed_at is not None


@pytest.mark.unit
@pytest.mark.django_db
def test_update_task_status_failed_sets_completed_at(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import update_task_status

    task = Task.objects.create(name="t", function_name="hello_world", task_data={}, created_by=user, status="running")
    update_task_status(task, status="failed", error_message="fail reason")
    task.refresh_from_db()
    assert task.status == "failed"
    assert task.error_message == "fail reason"
    assert task.completed_at is not None


@pytest.mark.unit
@pytest.mark.django_db
def test_update_task_status_with_result_data(user):
    from apps.tasks.models import Task
    from apps.tasks.utils import update_task_status

    task = Task.objects.create(name="t", function_name="hello_world", task_data={}, created_by=user, status="running")
    update_task_status(task, status="completed", result_data={"count": 5})
    task.refresh_from_db()
    assert task.result_data == {"count": 5}


# ---------------------------------------------------------------------------
# run_with_lock
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_run_with_lock_calls_fn_when_acquired(mock_lock_acquired):
    from apps.tasks.utils import run_with_lock

    mock_fn = MagicMock(return_value={"status": "success"})
    with patch("django.db.close_old_connections"), patch("django.db.connection"):
        result = run_with_lock("my_key", "my_task", mock_fn, execution_id=1)
    assert result["status"] == "success"
    mock_fn.assert_called_once_with(execution_id=1)


@pytest.mark.unit
def test_run_with_lock_returns_error_when_not_acquired(mock_lock_not_acquired):
    from apps.tasks.utils import run_with_lock

    mock_fn = MagicMock()
    with patch("django.db.close_old_connections"), patch("django.db.connection"):
        result = run_with_lock("my_key", "my_task", mock_fn)
    assert result["status"] == "error"
    assert "lock" in result["error"].lower() or "another" in result["error"].lower()
    mock_fn.assert_not_called()


# ---------------------------------------------------------------------------
# generic_collect_metrics
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_generic_collect_metrics_unknown_type():
    from apps.tasks.utils import generic_collect_metrics

    registry = {"valid_type": {}}
    result = generic_collect_metrics(
        collector_type="bad_type",
        collector_registry=registry,
        collection_mode="hourly",
        timestamp=timezone.now(),
        db_connection=MagicMock(),
    )
    assert result["status"] == "error"
    assert "bad_type" in result["error"]


@pytest.mark.unit
@pytest.mark.django_db
def test_generic_collect_metrics_success_creates_record():
    from apps.tasks.models import HourlyMetricsCollection
    from apps.tasks.utils import generic_collect_metrics

    mock_collector = MagicMock()
    mock_collector.gather.return_value = {"rows": [1, 2, 3]}
    collector_func = MagicMock(return_value=mock_collector)
    rollup_processor = MagicMock(return_value=MagicMock(prepare=MagicMock(return_value={"processed": True})))

    registry = {"test_type": {"collector_func": collector_func, "rollup_processor": rollup_processor}}
    ts = timezone.now().replace(minute=0, second=0, microsecond=0)

    result = generic_collect_metrics(
        collector_type="test_type",
        collector_registry=registry,
        collection_mode="hourly",
        timestamp=ts,
        db_connection=MagicMock(),
    )
    assert result["status"] == "success"
    assert HourlyMetricsCollection.objects.filter(collector_type="test_type").exists()


@pytest.mark.unit
@pytest.mark.django_db
def test_generic_collect_metrics_no_rollup_uses_raw_data():
    from apps.tasks.models import HourlyMetricsCollection
    from apps.tasks.utils import generic_collect_metrics

    mock_collector = MagicMock()
    raw = {"key": "value"}
    mock_collector.gather.return_value = raw
    collector_func = MagicMock(return_value=mock_collector)

    registry = {"no_rollup_type": {"collector_func": collector_func, "rollup_processor": None}}
    ts = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    result = generic_collect_metrics(
        collector_type="no_rollup_type",
        collector_registry=registry,
        collection_mode="hourly",
        timestamp=ts,
        db_connection=MagicMock(),
    )
    assert result["status"] == "success"
    coll = HourlyMetricsCollection.objects.get(collector_type="no_rollup_type")
    assert coll.raw_data == raw


@pytest.mark.unit
@pytest.mark.django_db
def test_generic_collect_metrics_exception_creates_failed_record():
    from apps.tasks.models import HourlyMetricsCollection
    from apps.tasks.utils import generic_collect_metrics

    mock_collector = MagicMock()
    mock_collector.gather.side_effect = RuntimeError("db down")
    collector_func = MagicMock(return_value=mock_collector)

    registry = {"broken_type": {"collector_func": collector_func, "rollup_processor": None}}
    ts = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)

    result = generic_collect_metrics(
        collector_type="broken_type",
        collector_registry=registry,
        collection_mode="hourly",
        timestamp=ts,
        db_connection=MagicMock(),
    )
    assert result["status"] == "error"
    # Failed record is stored
    coll = HourlyMetricsCollection.objects.filter(collector_type="broken_type", status="failed").first()
    assert coll is not None


@pytest.mark.unit
@pytest.mark.django_db
def test_generic_collect_metrics_integrity_error_returns_success():
    from django.utils import timezone as tz

    from apps.tasks.models import HourlyMetricsCollection
    from apps.tasks.utils import generic_collect_metrics

    mock_collector = MagicMock()
    mock_collector.gather.return_value = {"rows": []}
    collector_func = MagicMock(return_value=mock_collector)

    registry = {"dupe_type": {"collector_func": collector_func, "rollup_processor": None}}
    ts = tz.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)

    # Create a record first to trigger the IntegrityError path via a second call
    HourlyMetricsCollection.objects.create(
        collector_type="dupe_type",
        collection_timestamp=ts,
        raw_data={},
        status="collected",
    )
    # Second call should hit the update_or_create path (update, not create)
    result = generic_collect_metrics(
        collector_type="dupe_type",
        collector_registry=registry,
        collection_mode="hourly",
        timestamp=ts,
        db_connection=MagicMock(),
    )
    assert result["status"] == "success"
