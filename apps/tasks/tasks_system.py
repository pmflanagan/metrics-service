"""
System and maintenance background tasks for metrics_service.

This module provides system-level tasks including cleanup, maintenance,
communication, and testing tasks with proper error handling and status tracking.
"""

import logging
from typing import Any

from django.db import models, transaction

from .utils import (
    create_task_result,
    ensure_django_setup,
    handle_task_error,
    log_task_execution,
    run_with_lock,
    update_task_status,
)

logger = logging.getLogger(__name__)

ERROR_DJANGO_NOT_READY = "ERROR_DJANGO_NOT_READY"

RETRY_BASE_DELAY_SECONDS = 600  # 10 minutes - delay used for the first retry
RETRY_MAX_DELAY_SECONDS = 28800  # 8 hours - upper cap on any single retry delay


def compute_retry_delay(base_delay: int, attempts: int) -> int:
    """Seconds before next retry: min(base_delay * 2**max(0, attempts - 1), RETRY_MAX_DELAY_SECONDS)."""
    exponent = max(0, attempts - 1)
    return min(base_delay * (2**exponent), RETRY_MAX_DELAY_SECONDS)


try:
    from dispatcherd.publish import task
except ImportError:

    def task():
        def decorator(func):
            return func

        return decorator


def _claim_task(task_id):
    """Atomically claim a task for execution, returning (task, execution) or None if already claimed."""
    import os

    from django.utils import timezone

    from .models import Task, TaskExecution

    with transaction.atomic():
        claimed = (
            Task.ready_to_run()
            .filter(id=task_id)
            .update(
                status="running",
                started_at=timezone.now(),
                attempts=models.F("attempts") + 1,
            )
        )
        if not claimed:
            return None, None

        task = Task.objects.get(id=task_id)

        execution = TaskExecution.objects.create(
            task=task,
            status="running",
            worker_id=f"dispatcher-{os.getpid()}",
        )

    return task, execution


def execute_function(task, execution, task_function, locked):
    """Call task_function with execution_id and task_data, under an advisory lock if required."""
    try:
        if locked:
            return run_with_lock(
                task.function_name,  # lock_key
                task.name,
                task_function,
                execution_id=execution.id,
                **task.task_data,
            )
        else:
            return task_function(execution_id=execution.id, **task.task_data)

    except Exception as e:
        logger.exception(f"Task {task.function_name} raised: {e}")
        return create_task_result("error", error=f"Task execution failed: {e}")


def _get_base_delay(task) -> int:
    """Return a validated base retry delay for task, falling back to RETRY_BASE_DELAY_SECONDS."""
    raw = task.task_data.get("retry_delay_seconds", RETRY_BASE_DELAY_SECONDS)
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError(f"retry_delay_seconds must be positive, got {value}")
        return value
    except (TypeError, ValueError):
        logger.warning(
            f"Invalid retry_delay_seconds {raw!r} for task {task.name}, using default {RETRY_BASE_DELAY_SECONDS}s"
        )
        return RETRY_BASE_DELAY_SECONDS


def _schedule_retry(task) -> None:
    """Schedule a retry for a failed task if attempts remain."""
    if not task.can_retry():
        return
    task.refresh_from_db()
    if not task.can_retry():
        return
    base_delay = _get_base_delay(task)
    retry_delay = compute_retry_delay(base_delay, task.attempts)
    logger.info(f"Auto-retrying task {task.name} (attempt {task.attempts}/{task.max_attempts}) (delay {retry_delay}s)")
    task.retry(delay_seconds=retry_delay)


def execute_claimed(task, execution):
    """Execute a task that has already been atomically claimed, handling retries and status updates."""
    # Import TASK_FUNCTIONS here to avoid circular import
    # This import happens at runtime, not module load time
    from .tasks import TASK_FUNCTIONS, TASK_LOCKS

    # Validate task function exists
    if task.function_name not in TASK_FUNCTIONS:
        error_msg = f"Task function '{task.function_name}' not found in TASK_FUNCTIONS"
        return handle_task_error(task, execution, error_msg)

    log_task_execution(task.function_name, "start", f"Starting {task.function_name} task")
    log_task_execution(task.name, "running", f"Executing function: {task.function_name}")

    # Execute the actual task function, with advisory lock if required
    # Forwarding execution_id the task can link collections back to TaskExecution
    task_function = TASK_FUNCTIONS[task.function_name]
    locked = task.function_name in TASK_LOCKS
    result = execute_function(task, execution, task_function, locked)

    # Complete task execution
    status = "completed" if result.get("status") == "success" else "failed"
    error_message = result.get("error", "") if status == "failed" else ""

    update_task_status(task, execution, status=status, result_data=result, error_message=error_message)

    if status == "completed":
        log_task_execution(task.function_name, "complete", f"Task {task.function_name} completed successfully")
    else:
        error_msg = f"{task.function_name} task failed: {error_message}"
        log_task_execution(task.function_name, "error", error_msg, level="error")
    log_task_execution(task.name, "completed", f"Task execution finished with status: {status}")

    if status == "failed":
        _schedule_retry(task)

    return result


# This is the sole dispatcherd entry point — all DB tasks are routed through it.
@task(decorate=False)
def execute_db_task(**kwargs) -> dict[str, Any]:
    """
    Execute a database-defined task with comprehensive error handling and tracking.

    This function is the main entry point for executing tasks that are defined
    in the database. It handles the complete lifecycle of task execution including
    validation, execution, status tracking, and post-execution processing.

    Args:
        **kwargs: Task data containing:
            - task_id (int): ID of the task to execute (required)

    Returns:
        dict: Task result dictionary with execution status and results
    """
    ensure_django_setup()

    task_id = kwargs.get("task_id")
    if not task_id:
        return create_task_result("error", error="task_id is required")

    task = None
    execution = None

    try:
        from .models import Task

        task, execution = _claim_task(task_id)
        if task is None:
            if not Task.objects.filter(id=task_id).exists():
                return create_task_result("error", error=f"Task matching query does not exist: {task_id}")

            logger.warning(f"Task {task_id} already claimed by another worker, skipping")
            return create_task_result("error", error="Task already claimed by another worker")

        return execute_claimed(task, execution)

    except Exception as e:
        return handle_task_error(
            task, execution, task_id=task_id, execution_id=(execution.id if execution else None), exception=e
        )


def submit_task_to_dispatcher(task: Any) -> None:
    """
    Submit a task to the dispatcher for execution.

    Args:
        task: The task to submit
    """
    from .models import TaskExecution

    try:
        # Guard against duplicate submissions
        if TaskExecution.objects.filter(task=task, status__in=["pending", "running"]).exists():
            logger.warning(f"Task {task.name} (ID: {task.id}) already has a pending or running execution, skipping")
            return

        # Ensure dispatcherd is configured before attempting to submit tasks
        from .dispatcherd_config import ensure_dispatcherd_configured

        ensure_dispatcherd_configured()

        # Import dispatcherd submit function
        from dispatcherd.publish import submit_task

        # Determine the appropriate queue based on task type
        from .tasks import get_queue_for_function

        queue = get_queue_for_function(task.function_name)

        # Submit to dispatcherd using execute_db_task as the entry point
        # TaskExecution is created inside _claim_task to avoid orphaned records
        submit_task(execute_db_task, kwargs={"task_id": task.id}, queue=queue)

        logger.info(f"Submitted task {task.name} (ID: {task.id}) to dispatcher queue {queue}")

    except Exception as e:
        task.status = "failed"
        task.error_message = f"Failed to submit to dispatcher: {str(e)}"
        task.save(update_fields=["status", "error_message", "modified"])
        # Log at WARNING if the task can still be retried; ERROR only on final failure.
        # status must be set to "failed" before calling can_retry() because that method
        # requires status == "failed" to return True.
        if task.can_retry():
            logger.warning(f"Error submitting task to dispatcher: {str(e)}")
        else:
            logger.error(f"Error submitting task to dispatcher: {str(e)}")


# runs during `manage.py metrics_service init-system-tasks`
def create_system_tasks() -> dict[str, Any]:
    """
    Create system-defined tasks from task groups in the database.

    This function is intended to be called only from the init container
    (entrypoint-init.sh), before the application and scheduler start. At that
    point no tasks can be running, so unconditional deletion is safe.

    Removes all existing system tasks and recreates them from task group
    definitions, ensuring the database always matches the code.

    Returns:
        dict: Summary of tasks created and removed.
    """
    try:
        from .models import Task
        from .task_groups import get_all_tasks_for_init
    except ImportError:
        # Handle case where Django isn't fully set up yet
        return {"error": "ERROR_DJANGO_NOT_READY", "created": 0, "removed": 0}

    results = {"created": 0, "removed": 0, "tasks": []}

    # Remove all existing system tasks
    _, deletion_info = Task.objects.filter(is_system_task=True).delete()
    removed_count = deletion_info.get("tasks.Task", 0)
    results["removed"] = removed_count
    if removed_count > 0:
        results["tasks"].append(f"Removed {removed_count} existing system tasks")
        logger.info(f"Removed {removed_count} existing system tasks")

    # Get all task group definitions. Use get_all_tasks_for_init() (not get_all_enabled_tasks())
    # so that feature-flagged tasks (e.g. daily_anonymize, metrics collectors) are always
    # written to the DB with _feature_flag stored in task_data. The runtime check in
    # cron_scheduler._execute_database_task() then gates execution without requiring re-init
    # when the flag is toggled.
    task_groups = get_all_tasks_for_init()

    # Create fresh tasks from task groups
    for task_id, config in task_groups.items():
        try:
            _create_task_from_group(task_id, config, results, Task)
        except Exception as e:
            results["tasks"].append(f"Error with {task_id}: {str(e)}")
            logger.error(f"Failed to create task {task_id}: {e}")

    return results


def _create_task_from_group(task_id: str, config: dict[str, Any], results: dict[str, Any], task_model) -> None:
    """
    Create a system task from task group definition.

    Args:
        task_id: Unique identifier for the task
        config: Task configuration from task groups
        results: Results dict to update
        task_model: Task model class
    """
    task_data = config.get("args", {}).copy()
    if config.get("feature_flag"):
        task_data["_feature_flag"] = config["feature_flag"]

    kwargs = {
        "name": task_id,
        "description": config.get("description", ""),
        "function_name": config["function"],
        "task_data": task_data,
        "cron_expression": config.get("cron"),
        "is_system_task": True,
        "status": "pending",
    }
    if config.get("max_attempts") is not None:
        kwargs["max_attempts"] = config["max_attempts"]

    new_task = task_model.objects.create(**kwargs)
    results["created"] += 1
    results["tasks"].append(f"Created: {new_task.name}")
