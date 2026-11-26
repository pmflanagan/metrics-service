"""Query-efficiency regression tests for task and shared-resource viewsets.

These tests guard the low-risk N+1 fixes:

- ``TaskViewSet`` annotates ``executions_count`` for list-style actions so the
  serializer does not issue one COUNT query per task.
- ``TaskViewSet.cleanup`` dry-run preview uses ``.only(...)`` so the JSONB
  ``task_data``/``result_data`` columns are not fetched.
- ``OrganizationViewSet``/``TeamViewSet`` ``select_related`` their audit and
  parent foreign keys so list serialization does not issue per-row queries.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import IntegerField, Value
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from apps.core.models import Organization, Team
from apps.tasks.models import Task, TaskExecution
from apps.tasks.v1.serializers import TaskSerializer

TASK_LIST_URL = "/api/v1/tasks/"
TASK_PENDING_URL = "/api/v1/tasks/pending/"
TASK_RUNNING_URL = "/api/v1/tasks/running/"


def _client_for(user) -> APIClient:
    """Return an authenticated API client for the given user."""
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _create_task_with_executions(user, name: str, execution_count: int, status: str = "pending") -> Task:
    """Create a task plus the given number of execution records."""
    task = Task.objects.create(
        name=name,
        function_name="hello_world",
        task_data={},
        created_by=user,
        status=status,
    )
    TaskExecution.objects.bulk_create(
        [
            TaskExecution(task=task, status="completed", worker_id=f"worker-{task.id}-{i}")
            for i in range(execution_count)
        ]
    )
    return task


def _get_queries(client: APIClient, url: str) -> tuple[int, list[dict]]:
    """Perform a GET request and return (query count, response results)."""
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)

    assert response.status_code == 200, response.content
    payload = response.json()
    results = payload["results"] if isinstance(payload, dict) else payload
    return len(queries.captured_queries), results


@pytest.mark.unit
@pytest.mark.django_db
@pytest.mark.parametrize(
    ("url", "task_status", "descending"),
    [
        (TASK_LIST_URL, "pending", True),
        (TASK_PENDING_URL, "pending", False),
        (TASK_RUNNING_URL, "running", False),
    ],
)
def test_task_list_actions_query_count_is_constant(user, url, task_status, descending):
    """List/pending/running query counts must not grow with the number of tasks."""
    client = _client_for(user)

    first = _create_task_with_executions(user, "task-a", execution_count=2, status=task_status)
    second = _create_task_with_executions(user, "task-b", execution_count=3, status=task_status)
    small_query_count, small_results = _get_queries(client, url)

    assert len(small_results) == 2
    assert {item["id"]: item["executions_count"] for item in small_results} == {
        first.id: 2,
        second.id: 3,
    }
    # ``list`` is ordered by ``-id`` via OrderingFilter; ``pending``/``running``
    # keep ``Task.Meta.ordering`` (``["id"]``) after the aggregate annotation.
    expected_order = sorted((first.id, second.id), reverse=descending)
    assert [item["id"] for item in small_results] == expected_order

    expected_counts = {first.id: 2, second.id: 3}
    for index in range(8):
        task = _create_task_with_executions(
            user, f"bulk-task-{index}", execution_count=(index % 3) + 1, status=task_status
        )
        expected_counts[task.id] = (index % 3) + 1

    large_query_count, large_results = _get_queries(client, url)

    assert len(large_results) == 10
    assert {item["id"]: item["executions_count"] for item in large_results} == expected_counts
    assert [item["id"] for item in large_results] == sorted(expected_counts, reverse=descending)
    assert large_query_count == small_query_count


@pytest.mark.unit
@pytest.mark.django_db
def test_serializer_executions_count_without_annotation(user):
    """Standalone TaskSerializer still returns the accurate executions count."""
    task = _create_task_with_executions(user, "standalone-task", execution_count=3)

    # Re-fetch so no annotation or prefetch cache is present on the instance.
    fresh_task = Task.objects.get(pk=task.pk)
    assert not hasattr(fresh_task, "_executions_count")

    request = APIRequestFactory().get(TASK_LIST_URL)
    assert TaskSerializer(fresh_task, context={"request": request}).data["executions_count"] == 3


@pytest.mark.unit
@pytest.mark.django_db
def test_serializer_prefers_annotated_executions_count(user):
    """TaskSerializer uses the annotation when it is present on the instance."""
    _create_task_with_executions(user, "annotated-task", execution_count=2)

    annotated = Task.objects.annotate(_executions_count=Value(7, output_field=IntegerField())).get()

    request = APIRequestFactory().get(TASK_LIST_URL)
    assert TaskSerializer(annotated, context={"request": request}).data["executions_count"] == 7


@pytest.mark.unit
@pytest.mark.django_db
def test_cleanup_dry_run_preview_skips_payload_columns(user):
    """Cleanup dry-run preview fetches only the fields it exposes."""
    client = _client_for(user)

    old_completed_at = timezone.now() - timedelta(days=40)
    task = Task.objects.create(
        name="old-completed-task",
        function_name="hello_world",
        task_data={"payload": "not-needed"},
        result_data={"result": "also-not-needed"},
        created_by=user,
        status="completed",
    )
    Task.objects.filter(pk=task.pk).update(completed_at=old_completed_at)
    task.refresh_from_db()

    with CaptureQueriesContext(connection) as queries:
        response = client.post(reverse("tasks:v1:task-cleanup"), {"days": 30, "dry_run": True}, format="json")

    assert response.status_code == 200
    executed_sql = " ".join(entry["sql"] for entry in queries.captured_queries).lower()
    assert "task_data" not in executed_sql
    assert "result_data" not in executed_sql

    assert response.data["count"] == 1
    assert response.data["showing_preview_of"] == 1
    assert response.data["preview"] == [
        {
            "id": task.id,
            "name": "old-completed-task",
            "status": "completed",
            "completed_at": task.completed_at.isoformat(),
        }
    ]
    # The task must not have been deleted by a dry run.
    assert Task.objects.filter(pk=task.pk).exists()


@pytest.mark.unit
@pytest.mark.django_db
def test_organization_list_query_count_is_constant(user):
    """Organization list query count must not grow with the number of records."""
    client = _client_for(user)
    url = "/api/v1/organizations/"

    for index in range(2):
        Organization.objects.create(name=f"org-{index}", created_by=user, modified_by=user)
    small_query_count, small_results = _get_queries(client, url)
    assert len(small_results) == 2

    for index in range(2, 10):
        Organization.objects.create(name=f"org-{index}", created_by=user, modified_by=user)
    large_query_count, large_results = _get_queries(client, url)

    assert len(large_results) == 10
    assert large_query_count == small_query_count


@pytest.mark.unit
@pytest.mark.django_db
def test_team_list_query_count_is_constant(user):
    """Team list query count must not grow with the number of records."""
    client = _client_for(user)
    url = "/api/v1/teams/"
    organization = Organization.objects.create(name="teams-org", created_by=user, modified_by=user)

    for index in range(2):
        Team.objects.create(
            name=f"team-{index}",
            organization=organization,
            created_by=user,
            modified_by=user,
        )
    small_query_count, small_results = _get_queries(client, url)
    assert len(small_results) == 2

    for index in range(2, 10):
        Team.objects.create(
            name=f"team-{index}",
            organization=organization,
            created_by=user,
            modified_by=user,
        )
    large_query_count, large_results = _get_queries(client, url)

    assert len(large_results) == 10
    assert large_query_count == small_query_count
