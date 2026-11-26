"""
Unit tests for health check and metrics endpoints.
"""

from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient


@pytest.mark.unit
class TestHealthEndpoint:
    """Test cases for /health endpoint."""

    def test_health_check_returns_200_when_database_is_healthy(self, client, db):
        """Test that health endpoint returns 200 when database is accessible."""
        response = client.get("/health/")
        assert response.status_code == status.HTTP_200_OK

    def test_health_check_returns_correct_json_structure(self, client, db):
        """Test that health endpoint returns correct JSON structure when healthy."""
        response = client.get("/health/")
        json_response = response.json()
        assert json_response["status"] == "good"
        assert json_response["checks"]["database"] == "ok"

    def test_health_check_returns_503_when_database_fails(self, client, db):
        """Test that health endpoint returns 503 when database connection fails."""

        with patch("django.db.connection.ensure_connection", side_effect=Exception("DB Error")):
            response = client.get("/health/")
            assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_health_check_works_without_authentication(self, client, db):
        """Test that health endpoint doesn't require authentication."""
        response = client.get("/health/")
        json_response = response.json()
        assert json_response["status"] == "good"
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    @patch("apps.core.views.health.close_old_connections")
    def test_health_check_fails_when_segment_payloads_failed(self, mock_close, client):
        """Test that health endpoint reports segment check failed when single failed segment payload exist."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.tasks.models import AnonymizedMetricsPayload

        now = timezone.now()
        summary_date = now.date() - timedelta(days=1)
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date, anonymized_data={"test": "data"}, status="failed", retry_count=3
        )

        response = client.get("/health/")
        json_response = response.json()
        assert json_response["status"] == "good"
        assert json_response["checks"]["segment_send"]["status"] == "failed"
        assert "last_failure_at" in json_response["checks"]["segment_send"]
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    @patch("apps.core.views.health.close_old_connections")
    def test_health_check_fails_when_multiple_segment_payloads_failed(self, mock_close, client):
        """Test that health endpoint reports segment check failed status when many failed segment payloads exist."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.tasks.models import AnonymizedMetricsPayload

        now = timezone.now()
        summary_date = now.date() - timedelta(days=1)
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date - timedelta(days=3),
            anonymized_data={"test": "data"},
            status="sent",
            retry_count=3,
        )
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date - timedelta(days=2),
            anonymized_data={"test": "data"},
            status="sent",
            retry_count=3,
        )
        # Last to be 'modified' so it should be the one involved in the health check
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date, anonymized_data={"test": "data"}, status="failed", retry_count=3
        )

        response = client.get("/health/")
        json_response = response.json()
        assert json_response["status"] == "good"
        assert json_response["checks"]["segment_send"]["status"] == "failed"
        assert "last_failure_at" in json_response["checks"]["segment_send"]
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    @patch("apps.core.views.health.close_old_connections")
    def test_health_check_passes_when_multiple_segment_payloads_send(self, mock_close, client):
        """Test that health endpoint reports segment ok when many failed segment payloads exist."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.tasks.models import AnonymizedMetricsPayload

        now = timezone.now()
        summary_date = now.date() - timedelta(days=1)
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date - timedelta(days=3),
            anonymized_data={"test": "data"},
            status="failed",
            retry_count=3,
        )
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date - timedelta(days=2),
            anonymized_data={"test": "data"},
            status="failed",
            retry_count=3,
        )
        # Last to be 'modified' so it should be the one involved in the health check
        AnonymizedMetricsPayload.objects.create(
            summary_date=summary_date, anonymized_data={"test": "data"}, status="sent", retry_count=3
        )

        response = client.get("/health/")
        json_response = response.json()
        assert json_response["status"] == "good"
        assert json_response["checks"]["segment_send"]["status"] == "ok"
        assert "last_success_at" in json_response["checks"]["segment_send"]
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.django_db
    @patch("apps.core.views.health.close_old_connections")
    def test_health_check_segment_query_does_not_fetch_anonymized_data(self, mock_close, client):
        """Test that the segment send check selects only lightweight payload columns."""
        from datetime import timedelta

        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.utils import timezone

        from apps.tasks.models import AnonymizedMetricsPayload

        AnonymizedMetricsPayload.objects.create(
            summary_date=timezone.now().date() - timedelta(days=1),
            anonymized_data={"test": "data"},
            status="sent",
        )

        with CaptureQueriesContext(connection) as captured:
            response = client.get("/health/")

        payload_queries = [q["sql"] for q in captured.captured_queries if "tasks_anonymizedmetricspayload" in q["sql"]]
        assert payload_queries, "Expected the health check to query the anonymized metrics payload table"
        for sql in payload_queries:
            assert "anonymized_data" not in sql, f"Health check must not select anonymized_data: {sql}"

        assert response.status_code == status.HTTP_200_OK
        json_response = response.json()
        assert json_response["status"] == "good"
        assert json_response["checks"]["segment_send"]["status"] == "ok"
        assert json_response["checks"]["segment_send"]["last_success_at"]


@pytest.mark.unit
@pytest.mark.django_db
class TestMetricsEndpoint:
    """Test cases for /api/v1/metrics endpoint."""

    def test_metrics_endpoint_requires_authentication(self):
        """Unauthenticated requests must be denied."""
        response = APIClient().get("/api/v1/metrics")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_metrics_endpoint_denies_unprivileged_user(self):
        """Authenticated non-admin users must be denied."""
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(username="regular", password="pw")  # noqa: S106
        with patch("ansible_base.rbac.api.permissions.has_super_permission", return_value=False):
            client = APIClient()
            client.force_authenticate(user=user)
            response = client.get("/api/v1/metrics")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_metrics_endpoint_allows_system_admin(self, admin_client):
        """System admin (is_superuser) must receive Prometheus output."""
        response = admin_client.get("/api/v1/metrics")
        assert response.status_code == status.HTTP_200_OK

    def test_metrics_endpoint_allows_system_auditor(self):
        """System auditor (has_super_permission view) must receive Prometheus output."""
        from django.contrib.auth import get_user_model

        auditor = get_user_model().objects.create_user(username="auditor", password="pw")  # noqa: S106
        with patch("ansible_base.rbac.api.permissions.has_super_permission", return_value=True):
            client = APIClient()
            client.force_authenticate(user=auditor)
            response = client.get("/api/v1/metrics")
        assert response.status_code == status.HTTP_200_OK

    def test_metrics_endpoint_returns_prometheus_format(self, admin_client):
        """Response content type must be text/plain."""
        response = admin_client.get("/api/v1/metrics")
        assert response.status_code == status.HTTP_200_OK
        assert "text/plain" in response["Content-Type"]

    def test_metrics_endpoint_contains_django_metrics(self, admin_client):
        """Response body must contain Django metric names."""
        response = admin_client.get("/api/v1/metrics")
        assert "django_" in response.content.decode()

    def test_metrics_endpoint_trailing_slash_also_works(self, admin_client):
        """GET /api/v1/metrics/ (with trailing slash) must return the same Prometheus output."""
        response = admin_client.get("/api/v1/metrics/")
        assert response.status_code == status.HTTP_200_OK
        assert "text/plain" in response["Content-Type"]

    def test_metrics_legacy_paths_removed(self, client):
        """The old /metrics and /api/metrics redirect paths must no longer exist."""
        assert client.get("/metrics").status_code == status.HTTP_404_NOT_FOUND
        assert client.get("/api/metrics").status_code == status.HTTP_404_NOT_FOUND
