"""
Task scheduler using APScheduler.

This module provides a task scheduler that handles both task group definitions
and database tasks without database polling, using APScheduler for optimal performance.
"""

import logging
import threading
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from django.conf import settings as django_settings
from django.db import close_old_connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

STUCK_TASK_TIMEOUT_SECONDS: int = django_settings.TASK_TIMEOUT


def _inject_dispatch_timestamps(function_name: str, task_data: dict) -> dict:
    """
    Inject a fixed time-window timestamp into task_data at the moment a recurring
    task is dispatched by the scheduler.

    Time-sensitive collectors compute their target window from timezone.now() when
    no explicit timestamp is present. If the task is retried hours later, "now" has
    shifted and the retry collects the wrong window. By pinning the timestamp here —
    before the task is even submitted — every retry of the same execution copy
    operates on the originally intended window.

    Only sets the key when it is absent, so manually created tasks that already
    carry an explicit timestamp are left untouched.
    """
    task_data = task_data.copy()

    if function_name == "collect_hourly_metrics" and "hour_timestamp" not in task_data:
        now = timezone.now()
        task_data["hour_timestamp"] = (now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)).isoformat()

    elif function_name == "collect_snapshot_metrics" and "collection_timestamp" not in task_data:
        now = timezone.now()
        task_data["collection_timestamp"] = (
            now.replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=1)
        ).isoformat()

    elif function_name == "collect_daily_metrics" and "hour_timestamp" not in task_data:
        now = timezone.now()
        task_data["hour_timestamp"] = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    return task_data


class UnifiedTaskScheduler:
    """
    Task scheduler using APScheduler.

    This scheduler handles both task group definitions and database tasks
    without database polling, providing optimal performance for all task types.
    """

    def __init__(self, check_interval: int = 30):
        """Initialize the task scheduler."""
        self.scheduler = BackgroundScheduler()
        self.running = False
        self._lock = threading.Lock()
        self.check_interval = check_interval

        # Database task tracking
        self._db_task_jobs: dict[int, str] = {}  # task_id -> job_id mapping

    def start(self):
        """Start the cron scheduler."""
        with self._lock:
            if self.running:
                logger.warning("Scheduler is already running")
                return

            try:
                # Start the scheduler
                self.scheduler.start()
                self.running = True

                # Load database tasks into scheduler
                self._sync_database_tasks()

                # Add periodic task to check for new database tasks
                self.scheduler.add_job(
                    func=self._periodic_database_sync,
                    trigger="interval",
                    seconds=self.check_interval,
                    id="periodic_db_sync",
                    name="Periodic Database Task Sync",
                    replace_existing=True,
                    max_instances=1,  # Prevent overlapping executions
                )

                logger.info("Task scheduler started")
                logger.info(f"Loaded {len(self._db_task_jobs)} database tasks")
                logger.info(f"Periodic database sync will run every {self.check_interval} seconds")

            except Exception as e:
                logger.error(f"Failed to start cron scheduler: {str(e)}")
                raise

    def stop(self):
        """Stop the cron scheduler."""
        with self._lock:
            if not self.running:
                return

            try:
                self.scheduler.shutdown()
                self.running = False
                self._db_task_jobs.clear()
                logger.info("Task scheduler stopped")
            except Exception as e:
                logger.error(f"Error stopping cron scheduler: {str(e)}")

    def _task_feature_flag_enabled(self, task) -> bool:
        """Return False if the task carries a feature flag that is currently disabled."""
        feature_flag = task.task_data.get("_feature_flag") if task.task_data else None
        if not feature_flag:
            return True
        from .task_groups import get_feature_enabled_from_db

        return get_feature_enabled_from_db(feature_flag)

    def _sync_database_tasks(self):
        """Synchronize database tasks with the scheduler."""
        try:
            from .models import Task

            # Get all pending database tasks that are scheduled or recurring
            scheduled_tasks = Task.scheduled_tasks()
            recurring_tasks = Task.recurring_tasks()

            added_scheduled = 0
            added_recurring = 0

            # Add scheduled tasks whose feature flag is currently enabled
            for task in scheduled_tasks:
                if self._task_feature_flag_enabled(task):
                    self._add_database_scheduled_task(task)
                    added_scheduled += 1

            # Add recurring tasks whose feature flag is currently enabled
            for task in recurring_tasks:
                if self._task_feature_flag_enabled(task):
                    self._add_database_recurring_task(task)
                    added_recurring += 1

            logger.info(f"Synchronized {added_scheduled} scheduled and {added_recurring} recurring database tasks")

        except Exception as e:
            logger.error(f"Error synchronizing database tasks: {e}")

    def _periodic_database_sync(self):
        """Periodically check for new database tasks and add them to the scheduler."""
        close_old_connections()
        try:
            from .models import Task

            # Get all pending database tasks (immediate, scheduled, or recurring)
            immediate_tasks = Task.immediate_tasks()
            scheduled_tasks = Task.scheduled_tasks()
            recurring_tasks = Task.recurring_tasks()

            new_immediate = 0
            new_scheduled = 0
            new_recurring = 0

            # Handle immediate tasks - execute them right away
            for task in immediate_tasks:
                if task.id not in self._db_task_jobs and task.is_ready_to_run():
                    if not self._task_feature_flag_enabled(task):
                        continue
                    logger.info(f"Found new immediate task: {task.name} (ID: {task.id}) - executing now")
                    # Track immediate task to prevent duplicate submissions
                    self._db_task_jobs[task.id] = f"db_immediate_{task.id}"
                    self._execute_database_task(task.id)
                    new_immediate += 1

            # Check for new scheduled tasks
            for task in scheduled_tasks:
                if task.id not in self._db_task_jobs:
                    if not self._task_feature_flag_enabled(task):
                        continue
                    logger.info(f"Found new scheduled task: {task.name} (ID: {task.id})")
                    self._add_database_scheduled_task(task)
                    new_scheduled += 1

            # Check for new recurring tasks
            for task in recurring_tasks:
                if task.id not in self._db_task_jobs:
                    if not self._task_feature_flag_enabled(task):
                        continue
                    logger.info(f"Found new recurring task: {task.name} (ID: {task.id})")
                    self._add_database_recurring_task(task)
                    new_recurring += 1

            if new_immediate > 0 or new_scheduled > 0 or new_recurring > 0:
                logger.info(
                    f"Periodic sync: {new_immediate} immediate, {new_scheduled} scheduled, {new_recurring} recurring tasks"
                )

            # Detect tasks stuck in running beyond their timeout
            from .models import TaskExecution

            now = timezone.now()
            stuck_to_fail = Task.objects.filter(
                status="running", started_at__lt=now - timedelta(seconds=STUCK_TASK_TIMEOUT_SECONDS)
            )
            if stuck_to_fail:
                ids = [t.id for t in stuck_to_fail]
                error_msg = "Task timed out — worker died before completion"
                with transaction.atomic():
                    TaskExecution.objects.filter(task__id__in=ids, status="running").update(
                        status="failed", error_message=error_msg, completed_at=now
                    )
                    Task.objects.filter(id__in=ids, status="running").update(
                        status="failed", error_message=error_msg, completed_at=now
                    )
                logger.warning(f"Failed {len(ids)} stuck task(s): {ids}")

        except Exception as e:
            logger.error(f"Error in periodic database sync: {e}")

    def _add_database_scheduled_task(self, task):
        """Add a one-time scheduled database task to the scheduler."""
        if task.id in self._db_task_jobs:
            return  # Already scheduled

        try:
            job_id = f"db_task_{task.id}"

            # Check if the scheduled time is in the past
            now = timezone.now()
            if task.scheduled_time <= now:
                # Execute immediately if past due
                logger.info(
                    f"Task {task.name} (ID: {task.id}) is past due (scheduled: {task.scheduled_time}, now: {now}), executing immediately"
                )
                self._execute_database_task(task.id)
                return

            # Create date trigger for the scheduled time
            trigger = DateTrigger(run_date=task.scheduled_time)

            # Add job to scheduler
            self.scheduler.add_job(
                func=self._execute_database_task,
                trigger=trigger,
                args=[task.id],
                id=job_id,
                name=f"DB Task: {task.name}",
                replace_existing=True,
                max_instances=1,
            )

            self._db_task_jobs[task.id] = job_id
            logger.info(f"Added scheduled database task: {task.name} (ID: {task.id}) at {task.scheduled_time}")

        except Exception as e:
            logger.error(f"Failed to add scheduled database task {task.id}: {e}")

    def _add_database_recurring_task(self, task):
        """Add a recurring database task to the scheduler."""
        if task.id in self._db_task_jobs:
            return  # Already scheduled

        try:
            job_id = f"db_recurring_{task.id}"

            # Create cron trigger from expression
            trigger = CronTrigger.from_crontab(task.cron_expression)

            # Add job to scheduler
            self.scheduler.add_job(
                func=self._execute_database_task,
                trigger=trigger,
                args=[task.id],
                id=job_id,
                name=f"DB Recurring: {task.name}",
                replace_existing=True,
                max_instances=1,
            )

            self._db_task_jobs[task.id] = job_id
            logger.info(f"Added recurring database task: {task.name} (ID: {task.id}) with cron: {task.cron_expression}")

        except Exception as e:
            logger.error(f"Failed to add recurring database task {task.id}: {e}")

    def _execute_database_task(self, task_id: int):
        """Execute a database task by submitting it to dispatcherd."""
        close_old_connections()
        try:
            from .models import Task

            # Get the task (don't filter by status for recurring tasks)
            try:
                task = Task.objects.get(id=task_id)
            except Task.DoesNotExist:
                logger.warning(f"Database task {task_id} not found")
                self._remove_database_task(task_id)
                return

            if task.status in ("cancelled", "completed"):
                logger.warning(f"Task '{task.name}' has status '{task.status}' and will not be executed")
                self._remove_database_task(task_id)
                return

            # Check feature flag at runtime so toggling takes effect without restart
            feature_flag = task.task_data.get("_feature_flag") if task.task_data else None
            if feature_flag:
                from .task_groups import get_feature_enabled_from_db

                if not get_feature_enabled_from_db(feature_flag):
                    logger.debug(f"Skipping task '{task.name}': feature flag '{feature_flag}' is disabled")
                    if not task.cron_expression:
                        self._remove_database_task(task_id)
                    return

            # Handle recurring tasks by creating a new execution record
            if task.cron_expression:
                # Create a new task record for this execution
                execution_task = Task.objects.create(
                    name=f"{task.name} (Execution {timezone.now().strftime('%Y-%m-%d %H:%M:%S')})",
                    function_name=task.function_name,
                    task_data=_inject_dispatch_timestamps(task.function_name, task.task_data or {}),
                    scheduled_time=None,  # Execute immediately
                    cron_expression=None,  # This is not a recurring task
                    max_attempts=task.max_attempts,
                    created_by=task.created_by,
                    is_system_task=task.is_system_task,
                )

                logger.info(
                    f"Created execution record for recurring task: {task.name} → {execution_task.name} (ID: {execution_task.id})"
                )

                # Submit the execution task (not the original recurring task)
                from .tasks_system import submit_task_to_dispatcher

                submit_task_to_dispatcher(execution_task)

                # Keep the original recurring task unchanged (it stays as template)
                logger.info(f"Recurring task {task.name} (ID: {task_id}) remains as template for future executions")
                return

            # Check if task is ready to run
            if task.status not in ["pending"]:
                logger.warning(f"Task {task_id} is not in pending status (current: {task.status})")
                self._remove_database_task(task_id)
                return

            logger.info(f"Executing database task: {task.name} (ID: {task_id})")

            # Import submit function here to avoid circular imports
            from .tasks_system import submit_task_to_dispatcher

            # Submit to dispatcherd
            submit_task_to_dispatcher(task)

            # Remove from tracking after submission (since it's not recurring)
            self._remove_database_task(task_id)

        except Exception as e:
            logger.error(f"Failed to execute database task {task_id}: {e}")

    def _remove_database_task(self, task_id: int):
        """Remove a database task from the scheduler."""
        if task_id in self._db_task_jobs:
            job_id = self._db_task_jobs[task_id]
            try:
                self.scheduler.remove_job(job_id)
            except Exception as e:
                logger.debug(f"Job {job_id} not found in scheduler: {e}")
            del self._db_task_jobs[task_id]


# Global scheduler instance
_scheduler_instance: UnifiedTaskScheduler | None = None


def get_scheduler() -> UnifiedTaskScheduler:
    """Get the global scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = UnifiedTaskScheduler()
    return _scheduler_instance


def start_scheduler():
    """Start the global scheduler."""
    scheduler = get_scheduler()
    scheduler.start()
    return scheduler


def stop_scheduler():
    """Stop the global scheduler."""
    global _scheduler_instance
    if _scheduler_instance:
        _scheduler_instance.stop()
        _scheduler_instance = None
