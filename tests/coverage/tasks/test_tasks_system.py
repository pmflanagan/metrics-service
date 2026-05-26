"""
Unit tests for apps/tasks/tasks_system.py.
Targets 14.9% → ~90% coverage.
"""

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _claim_task
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_claim_task_succeeds_for_pending(user):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import _claim_task

    task = Task.objects.create(name="pending_task", function_name="hello_world", task_data={}, created_by=user)
    task_obj, execution = _claim_task(task.id)

    assert task_obj is not None
    assert execution is not None
    task.refresh_from_db()
    assert task.status == "running"


@pytest.mark.unit
@pytest.mark.django_db
def test_claim_task_returns_none_for_running(user):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import _claim_task

    task = Task.objects.create(
        name="running_task", function_name="hello_world", task_data={}, created_by=user, status="running"
    )
    task_obj, execution = _claim_task(task.id)

    assert task_obj is None
    assert execution is None


@pytest.mark.unit
@pytest.mark.django_db
def test_claim_task_returns_none_for_completed(user):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import _claim_task

    task = Task.objects.create(
        name="done_task", function_name="hello_world", task_data={}, created_by=user, status="completed"
    )
    task_obj, execution = _claim_task(task.id)
    assert task_obj is None


# ---------------------------------------------------------------------------
# execute_db_task
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_execute_db_task_no_task_id():
    from apps.tasks.tasks_system import execute_db_task

    result = execute_db_task()
    assert result["status"] == "error"
    assert "task_id" in result["error"]


@pytest.mark.unit
@pytest.mark.django_db
def test_execute_db_task_nonexistent_id():
    from apps.tasks.tasks_system import execute_db_task

    result = execute_db_task(task_id=999999)
    assert result["status"] == "error"


@pytest.mark.unit
@pytest.mark.django_db
def test_execute_db_task_success(user, mock_dispatcherd, mock_dispatcherd_config):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import execute_db_task

    task = Task.objects.create(name="exec_task", function_name="hello_world", task_data={}, created_by=user)

    mock_fn = MagicMock(return_value={"status": "success", "message": "ok"})
    with patch.dict("apps.tasks.tasks.TASK_FUNCTIONS", {"hello_world": mock_fn}):
        result = execute_db_task(task_id=task.id)

    assert result["status"] == "success"
    task.refresh_from_db()
    assert task.status == "completed"


@pytest.mark.unit
@pytest.mark.django_db
def test_execute_db_task_function_not_in_registry(user):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import execute_db_task

    task = Task.objects.create(
        name="missing_fn_task", function_name="nonexistent_function", task_data={}, created_by=user
    )

    with patch.dict("apps.tasks.tasks.TASK_FUNCTIONS", {}, clear=True):
        result = execute_db_task(task_id=task.id)

    assert result["status"] == "error"
    task.refresh_from_db()
    assert task.status == "failed"


@pytest.mark.unit
@pytest.mark.django_db
def test_execute_db_task_function_returns_error(user):
    """When a task function returns error status, execute_db_task propagates the error."""
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import execute_db_task

    task = Task.objects.create(name="raise_task_3", function_name="hello_world", task_data={}, created_by=user)

    mock_fn = MagicMock(return_value={"status": "error", "error": "task error occurred"})
    with (
        patch.dict("apps.tasks.tasks.TASK_FUNCTIONS", {"hello_world": mock_fn}),
        patch("apps.tasks.tasks.TASK_LOCKS", set()),
    ):
        result = execute_db_task(task_id=task.id)

    # The task function was called and returned an error
    assert result["status"] == "error"
    mock_fn.assert_called_once()


@pytest.mark.unit
@pytest.mark.django_db
def test_execute_db_task_fails_when_already_claimed(user):
    """If the task is already claimed by another worker, returns error."""
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import execute_db_task

    task = Task.objects.create(
        name="already_claimed", function_name="hello_world", task_data={}, created_by=user, status="running"
    )
    result = execute_db_task(task_id=task.id)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# execute_claimed — auto-retry on failure
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_execute_claimed_task_failure_returns_error(user):
    """When a task function returns error, execute_claimed propagates error status."""
    from apps.tasks.models import Task, TaskExecution
    from apps.tasks.tasks_system import execute_claimed

    task = Task.objects.create(
        name="t_fail2", function_name="hello_world", task_data={}, created_by=user, status="running", attempts=1
    )
    execution = TaskExecution.objects.create(task=task, status="running", worker_id="test-1")

    mock_fn = MagicMock(return_value={"status": "error", "error": "failed"})
    with (
        patch.dict("apps.tasks.tasks.TASK_FUNCTIONS", {"hello_world": mock_fn}),
        patch("apps.tasks.tasks.TASK_LOCKS", set()),
    ):
        result = execute_claimed(task, execution)

    assert result["status"] == "error"
    mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# submit_task_to_dispatcher
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_submit_task_to_dispatcher_calls_submit(user, mock_dispatcherd, mock_dispatcherd_config):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import submit_task_to_dispatcher

    task = Task.objects.create(name="dispatch_task", function_name="hello_world", task_data={}, created_by=user)

    submit_task_to_dispatcher(task)
    mock_dispatcherd.assert_called_once()


@pytest.mark.unit
@pytest.mark.django_db
def test_submit_task_skips_if_pending_execution_exists(user, mock_dispatcherd, mock_dispatcherd_config):
    from apps.tasks.models import Task, TaskExecution
    from apps.tasks.tasks_system import submit_task_to_dispatcher

    task = Task.objects.create(name="skip_task", function_name="hello_world", task_data={}, created_by=user)
    TaskExecution.objects.create(task=task, status="pending")

    submit_task_to_dispatcher(task)
    mock_dispatcherd.assert_not_called()


@pytest.mark.unit
@pytest.mark.django_db
def test_submit_task_handles_submit_error(user, mock_dispatcherd_config):
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import submit_task_to_dispatcher

    task = Task.objects.create(name="err_task", function_name="hello_world", task_data={}, created_by=user)

    with patch("dispatcherd.publish.submit_task", side_effect=RuntimeError("broker down")):
        submit_task_to_dispatcher(task)

    task.refresh_from_db()
    assert task.status == "failed"
    assert "broker down" in task.error_message


@pytest.mark.unit
@pytest.mark.django_db
def test_submit_task_dispatcher_error_logs_warning_when_retriable(user, mock_dispatcherd_config):
    """submit_task_to_dispatcher logs WARNING when task still has retry attempts remaining."""
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import submit_task_to_dispatcher

    # attempts=0, max_attempts=3 → can_retry() returns True after status set to "failed"
    task = Task.objects.create(
        name="retry_dispatch_task",
        function_name="hello_world",
        task_data={},
        created_by=user,
        attempts=0,
        max_attempts=3,
    )

    with (
        patch("dispatcherd.publish.submit_task", side_effect=RuntimeError("broker unavailable")),
        patch("apps.tasks.tasks_system.logger") as mock_logger,
    ):
        submit_task_to_dispatcher(task)

    task.refresh_from_db()
    assert task.status == "failed"
    mock_logger.warning.assert_called_once()
    warning_msg = mock_logger.warning.call_args[0][0]
    assert "broker unavailable" in warning_msg
    # Must NOT log at ERROR level for the dispatcher error
    error_calls = [str(call) for call in mock_logger.error.call_args_list]
    assert not any("broker unavailable" in c for c in error_calls)


@pytest.mark.unit
@pytest.mark.django_db
def test_submit_task_dispatcher_error_logs_error_when_exhausted(user, mock_dispatcherd_config):
    """submit_task_to_dispatcher logs ERROR when task has no retry attempts remaining."""
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import submit_task_to_dispatcher

    # attempts == max_attempts → can_retry() returns False
    task = Task.objects.create(
        name="exhausted_dispatch_task",
        function_name="hello_world",
        task_data={},
        created_by=user,
        attempts=3,
        max_attempts=3,
    )

    with (
        patch("dispatcherd.publish.submit_task", side_effect=RuntimeError("broker gone")),
        patch("apps.tasks.tasks_system.logger") as mock_logger,
    ):
        submit_task_to_dispatcher(task)

    task.refresh_from_db()
    assert task.status == "failed"
    mock_logger.error.assert_called_once()
    error_msg = mock_logger.error.call_args[0][0]
    assert "broker gone" in error_msg
    # Must NOT log at WARNING level for the dispatcher error
    warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
    assert not any("broker gone" in c for c in warning_calls)


# ---------------------------------------------------------------------------
# create_system_tasks
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_create_system_tasks_creates_all_enabled():
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import create_system_tasks

    Task.objects.filter(is_system_task=True).delete()
    results = create_system_tasks()

    assert results["created"] > 0
    assert results.get("error") is None
    # Verify at least the system tasks exist in DB
    assert Task.objects.filter(is_system_task=True, function_name="cleanup_old_tasks").exists()
    assert Task.objects.filter(is_system_task=True, function_name="hello_world").exists()


@pytest.mark.unit
@pytest.mark.django_db
def test_create_system_tasks_removes_existing_before_recreate():
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import create_system_tasks

    # Seed some system tasks first
    create_system_tasks()
    count_before = Task.objects.filter(is_system_task=True).count()

    results = create_system_tasks()
    assert results["removed"] == count_before


@pytest.mark.unit
@pytest.mark.django_db
def test_create_task_from_group_with_feature_flag():
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import _create_task_from_group

    results = {"created": 0, "removed": 0, "tasks": []}
    config = {
        "function": "hello_world",
        "cron": "0 * * * *",
        "feature_flag": "METRICS_COLLECTION",
        "args": {"some_arg": 1},
        "description": "test task",
    }
    _create_task_from_group("test_flagged_task", config, results, Task)

    assert results["created"] == 1
    task = Task.objects.get(name="test_flagged_task")
    assert task.task_data.get("_feature_flag") == "METRICS_COLLECTION"
    assert task.task_data.get("some_arg") == 1


@pytest.mark.unit
@pytest.mark.django_db
def test_create_task_from_group_without_feature_flag():
    from apps.tasks.models import Task
    from apps.tasks.tasks_system import _create_task_from_group

    results = {"created": 0, "removed": 0, "tasks": []}
    config = {"function": "hello_world", "cron": None, "args": {}, "description": "no flag task"}
    _create_task_from_group("test_no_flag_task", config, results, Task)

    task = Task.objects.get(name="test_no_flag_task")
    assert "_feature_flag" not in task.task_data
