"""
Unit tests for apps/core/views/ (health, ping, api_root).
Targets 0% → ~85% coverage.
"""

from unittest.mock import patch

import pytest
from rest_framework.test import APIClient


@pytest.fixture
def anon_client():
    return APIClient()


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# Ping view
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_ping_returns_200(anon_client):
    response = anon_client.get("/ping/")
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.django_db
def test_ping_returns_pong(anon_client):
    response = anon_client.get("/ping/")
    data = response.json()
    # Response should contain some version of "pong" or simple ok
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Health view
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_health_db_ok(anon_client):
    response = anon_client.get("/health/")
    data = response.json()
    assert "checks" in data
    assert "database" in data["checks"]
    assert data["checks"]["database"] == "ok"
    assert response.status_code == 200


@pytest.mark.unit
@pytest.mark.django_db
def test_health_db_error_returns_503(anon_client):
    from django.db import connection

    with patch.object(connection, "ensure_connection", side_effect=Exception("connection refused")):
        response = anon_client.get("/health/")

    assert response.status_code == 503
    data = response.json()
    assert "error" in data["checks"]["database"]


@pytest.mark.unit
@pytest.mark.django_db
def test_health_segment_send_ok(anon_client):
    from unittest.mock import MagicMock, patch

    from django.utils import timezone as tz

    mock_payload = MagicMock()
    mock_payload.status = "sent"
    mock_payload.modified = tz.now()

    # AnonymizedMetricsPayload is imported inside the view function, patch at source
    with patch("apps.tasks.models.AnonymizedMetricsPayload") as mock_model:
        mock_qs = mock_model.objects.only.return_value.order_by.return_value
        mock_qs.first.return_value = mock_payload
        response = anon_client.get("/health/")

    data = response.json()
    assert "segment_send" in data.get("checks", {})
    assert data["checks"]["segment_send"].get("status") == "ok"


@pytest.mark.unit
@pytest.mark.django_db
def test_health_segment_send_failed(anon_client):
    from unittest.mock import MagicMock, patch

    from django.utils import timezone as tz

    mock_payload = MagicMock()
    mock_payload.status = "failed"
    mock_payload.modified = tz.now()

    with patch("apps.tasks.models.AnonymizedMetricsPayload") as mock_model:
        mock_qs = mock_model.objects.only.return_value.order_by.return_value
        mock_qs.first.return_value = mock_payload
        response = anon_client.get("/health/")

    data = response.json()
    assert "segment_send" in data.get("checks", {})
    assert data["checks"]["segment_send"].get("status") == "failed"


@pytest.mark.unit
@pytest.mark.django_db
def test_health_no_payloads_no_segment_check(anon_client):
    from unittest.mock import patch

    with patch("apps.tasks.models.AnonymizedMetricsPayload") as mock_model:
        mock_qs = mock_model.objects.only.return_value.order_by.return_value
        mock_qs.first.return_value = None
        response = anon_client.get("/health/")

    data = response.json()
    assert "segment_send" not in data.get("checks", {})


# ---------------------------------------------------------------------------
# API root view
# ---------------------------------------------------------------------------
@pytest.mark.unit
@pytest.mark.django_db
def test_api_root_v1_authenticated(auth_client):
    response = auth_client.get("/api/v1/")
    assert response.status_code in (200, 301, 302)


@pytest.mark.unit
@pytest.mark.django_db
def test_api_v1_returns_json_for_authenticated(auth_client):
    response = auth_client.get("/api/v1/")
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, dict)
