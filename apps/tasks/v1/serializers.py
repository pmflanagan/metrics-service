"""
Task-related serializers for the API v1 endpoints.

This module provides serializers for task management functionality
converted from the manage_tasks.py command to REST API endpoints.
"""

import json

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.tasks.models import (
    Task,
    TaskExecution,
)

from .base_serializers import BaseModelSerializer, StatusFieldMixin

User = get_user_model()


class TaskSerializer(BaseModelSerializer, StatusFieldMixin):
    """
    Main serializer for Task model with comprehensive functionality.

    This serializer provides task creation, scheduling, and monitoring
    capabilities for the dashboard interface and includes all fields
    needed for task management.
    """

    # Use PrimaryKeyRelatedField instead of HyperlinkedRelatedField since user views were removed
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    executions_count = serializers.SerializerMethodField()
    duration = serializers.SerializerMethodField()
    can_retry = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    can_modify = serializers.SerializerMethodField()
    is_ready_to_run = serializers.SerializerMethodField()
    next_run_time = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "url",
            "name",
            "function_name",
            "task_data",
            "scheduled_time",
            "cron_expression",
            "is_system_task",
            "status",
            "attempts",
            "max_attempts",
            "result_data",
            "started_at",
            "completed_at",
            "error_message",
            "created_by",
            "created_by_username",
            "executions_count",
            "duration",
            "can_retry",
            "can_delete",
            "can_modify",
            "is_ready_to_run",
            "next_run_time",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "url",
            "created_by_username",
            "executions_count",
            "duration",
            "can_retry",
            "can_delete",
            "can_modify",
            "is_ready_to_run",
            "next_run_time",
            "is_system_task",
            "status",
            "attempts",
            "result_data",
            "started_at",
            "completed_at",
            "error_message",
            "created",
            "modified",
        ]
        extra_kwargs = {
            "url": {"view_name": "tasks:v1:task-detail"},
            "scheduled_time": {"help_text": "ISO 8601 format datetime when task should run"},
            "task_data": {"help_text": "JSON data to pass to the task function"},
        }

    def get_executions_count(self, obj) -> int:
        """Get count of task executions.

        TaskViewSet annotates ``_executions_count`` on its querysets to avoid one
        COUNT query per task. Standalone serializer usage falls back to a live count.
        """
        annotated_count = getattr(obj, "_executions_count", None)
        if annotated_count is not None:
            return annotated_count
        return obj.executions.count()

    def get_can_retry(self, obj) -> bool:
        """Check if task can be retried."""
        return obj.can_retry()

    def get_can_delete(self, obj) -> bool:
        """Check if task can be deleted."""
        return obj.can_delete()

    def get_can_modify(self, obj) -> bool:
        """Check if task can be modified."""
        return obj.can_modify()

    def get_is_ready_to_run(self, obj) -> bool:
        """Check if task is ready to run."""
        return obj.is_ready_to_run()

    def get_next_run_time(self, obj) -> str:
        """Get next run time for recurring tasks."""
        return obj.get_next_run_time()


class TaskCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating tasks via the API.

    This serializer handles task creation similar to the manage_tasks.py create
    command with validation and proper data handling.
    """

    user = serializers.CharField(required=False, help_text="Username of task creator (optional)")

    class Meta:
        model = Task
        fields = [
            "name",
            "function_name",
            "task_data",
            "scheduled_time",
            "cron_expression",
            "max_attempts",
            "user",
        ]
        extra_kwargs = {
            "scheduled_time": {
                "required": False,
                "help_text": "ISO 8601 format datetime. Leave empty for immediate execution.",
            },
            "task_data": {"required": False, "help_text": "JSON object with task parameters"},
            "cron_expression": {"required": False, "help_text": "Cron expression for recurring tasks"},
            "max_attempts": {"default": 3},
        }

    def validate_function_name(self, value):
        """Validate that the function name exists in available task functions."""
        from apps.tasks.tasks import TASK_FUNCTIONS

        if value not in TASK_FUNCTIONS:
            available_functions = list(TASK_FUNCTIONS.keys())
            raise serializers.ValidationError(f"Invalid function name. Available functions: {available_functions}")
        return value

    def validate_cron_expression(self, value):
        """Validate cron expression format and normalize empty strings to None."""
        # Normalize empty strings to None to ensure consistent NULL values in database
        # This ensures cron_expression__isnull filters work correctly for cleanup
        if not value:
            return None

        try:
            from croniter import croniter

            croniter(value)
        except (ValueError, TypeError) as e:
            raise serializers.ValidationError(f"Invalid cron expression: {e}") from e
        return value

    def validate_task_data(self, value):
        """Validate task data is proper JSON if provided as string."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as e:
                raise serializers.ValidationError(f"Invalid JSON data: {e}") from e
        return value

    def validate_user(self, value):
        """Validate user exists if provided."""
        if value:
            try:
                return User.objects.get(username=value)
            except User.DoesNotExist as e:
                raise serializers.ValidationError(f"User '{value}' not found") from e
        return None

    def create(self, validated_data):
        """Create task with proper user assignment."""
        user = validated_data.pop("user", None)

        # Use provided user or request user
        if user:
            validated_data["created_by"] = user
        elif (
            hasattr(self.context.get("request"), "user")
            and self.context["request"].user
            and self.context["request"].user.is_authenticated
        ):
            validated_data["created_by"] = self.context["request"].user

        return super().create(validated_data)


class TaskListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for task list endpoints.

    This serializer provides essential task information for list views,
    similar to the manage_tasks.py list command output.
    """

    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Task
        fields = [
            "id",
            "name",
            "function_name",
            "status",
            "status_display",
            "created",
            "scheduled_time",
            "created_by_username",
        ]


class TaskCleanupSerializer(serializers.Serializer):
    """
    Serializer for task cleanup operations.

    This serializer handles the cleanup functionality from manage_tasks.py.
    """

    days = serializers.IntegerField(default=30, min_value=1, help_text="Number of days to keep completed tasks")
    dry_run = serializers.BooleanField(default=False, help_text="Show what would be deleted without actually deleting")


class TaskExecutionSerializer(BaseModelSerializer):
    """
    Serializer for TaskExecution model for execution tracking and monitoring.
    """

    task_name = serializers.CharField(source="task.name", read_only=True)
    task_function = serializers.CharField(source="task.function_name", read_only=True)
    duration = serializers.SerializerMethodField()

    class Meta:
        model = TaskExecution
        fields = [
            "id",
            "url",
            "task",
            "status",
            "started_at",
            "completed_at",
            "worker_id",
            "result_data",
            "error_message",
            "execution_time_seconds",
            "task_name",
            "task_function",
            "duration",
            "created",
            "modified",
        ]
        read_only_fields = [
            "id",
            "url",
            "task_name",
            "task_function",
            "duration",
            "started_at",
            "completed_at",
            "execution_time_seconds",
            "created",
            "modified",
        ]
        extra_kwargs = {
            "url": {"view_name": "tasks:v1:taskexecution-detail"},
            "task": {"view_name": "tasks:v1:task-detail"},
        }

    def get_duration(self, obj) -> float | None:
        """Get execution duration in seconds."""
        if obj.started_at and obj.completed_at:
            return (obj.completed_at - obj.started_at).total_seconds()
        return None
