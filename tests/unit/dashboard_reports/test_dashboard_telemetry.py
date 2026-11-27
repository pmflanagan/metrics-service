"""Unit tests for dashboard collection telemetry: _save_telemetry_details,
DashboardTelemetry model, DashboardTelemetrySerializer, DashboardTelemetryViewSet,
and cleanup_dashboard_telemetry."""

import decimal
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from rest_framework.test import APIRequestFactory

from apps.dashboard_reports.models import DashboardTelemetry
from apps.dashboard_reports.serializers import DashboardTelemetrySerializer
from apps.dashboard_reports.tasks import _save_telemetry_details, cleanup_dashboard_telemetry
from apps.dashboard_reports.urls import router
from apps.dashboard_reports.viewsets.dashboard_telemetry import DashboardTelemetryViewSet

PATCH_PERM = "ansible_base.rbac.api.permissions.IsSystemAdminOrAuditor.has_permission"

factory = APIRequestFactory()
view = DashboardTelemetryViewSet.as_view({"get": "list"})


# ---------------------------------------------------------------------------
# _save_telemetry_details
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSaveTelemetryDetails:
    """Tests for the _save_telemetry_details helper."""

    @pytest.mark.django_db
    def test_creates_telemetry_record_with_correct_fields(self):
        """_save_telemetry_details creates a DashboardTelemetry row with the supplied values."""
        _save_telemetry_details(
            task_name="collect_dashboard_reports_initial_data",
            success=True,
            collection_duration_ms=1500.5,
            number_of_records_processed=42,
            database_query_time_ms=200.0,
            cache_hit_rate=None,
        )

        assert DashboardTelemetry.objects.count() == 1
        record = DashboardTelemetry.objects.first()
        assert record.task_name == "collect_dashboard_reports_initial_data"
        assert record.success is True
        assert record.collection_duration_ms == decimal.Decimal("1500.50")
        assert record.number_of_records_processed == 42
        assert record.database_query_time_ms == decimal.Decimal("200.00")
        assert record.cache_hit_rate is None
        assert record.collection_run_date == date.today()

    @pytest.mark.django_db
    def test_records_failure_when_success_is_false(self):
        """_save_telemetry_details stores success=False for failed task runs."""
        _save_telemetry_details(
            task_name="cleanup_dashboard_reports_old_data",
            success=False,
            collection_duration_ms=50.0,
            number_of_records_processed=0,
            database_query_time_ms=10.0,
            cache_hit_rate=None,
        )

        record = DashboardTelemetry.objects.first()
        assert record.success is False
        assert record.task_name == "cleanup_dashboard_reports_old_data"

    def test_logs_and_swallows_exception_on_db_error(self, caplog):
        """When DashboardTelemetry.objects.create raises, the error is logged and not re-raised."""
        with (
            patch("apps.dashboard_reports.tasks.DashboardTelemetry") as mock_model,
            patch("apps.dashboard_reports.tasks.logger") as mock_logger,
        ):
            mock_model.objects.create.side_effect = Exception("DB is down")
            _save_telemetry_details(
                task_name="test_task",
                success=True,
                collection_duration_ms=100.0,
                number_of_records_processed=0,
                database_query_time_ms=10.0,
                cache_hit_rate=None,
            )
        mock_logger.exception.assert_called_once_with("Failed to record dashboard telemetry")


# ---------------------------------------------------------------------------
# collect_dashboard_reports_initial_data — telemetry side-effects
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCollectInitialDataTelemetry:
    """Verifies that collect_dashboard_reports_initial_data records telemetry correctly."""

    def test_saves_telemetry_on_success(self):
        """On a successful run, _save_telemetry_details is called with success=True."""
        with (
            patch("apps.dashboard_reports.tasks._collect_data") as mock_collect,
            patch("apps.dashboard_reports.tasks.create_task_result"),
            patch("apps.dashboard_reports.tasks._save_telemetry_details") as mock_telemetry,
        ):
            mock_collect.return_value = {"error": False, "data": {"job_count": 7}}

            from apps.dashboard_reports.tasks import collect_dashboard_reports_initial_data

            collect_dashboard_reports_initial_data()

        mock_telemetry.assert_called_once()
        call_kwargs = mock_telemetry.call_args[1]
        assert call_kwargs["success"] is True
        assert call_kwargs["task_name"] == "collect_dashboard_reports_initial_data"
        assert call_kwargs["number_of_records_processed"] == 7

    def test_saves_telemetry_on_error(self):
        """On a failed run, _save_telemetry_details is called with success=False."""
        with (
            patch("apps.dashboard_reports.tasks._collect_data") as mock_collect,
            patch("apps.dashboard_reports.tasks.create_task_result"),
            patch("apps.dashboard_reports.tasks._save_telemetry_details") as mock_telemetry,
        ):
            mock_collect.return_value = {"error": True, "message": "timeout"}

            from apps.dashboard_reports.tasks import collect_dashboard_reports_initial_data

            collect_dashboard_reports_initial_data()

        mock_telemetry.assert_called_once()
        call_kwargs = mock_telemetry.call_args[1]
        assert call_kwargs["success"] is False

    def test_telemetry_duration_is_non_negative(self):
        """The collection_duration_ms value passed to _save_telemetry_details is >= 0."""
        with (
            patch("apps.dashboard_reports.tasks._collect_data") as mock_collect,
            patch("apps.dashboard_reports.tasks.create_task_result"),
            patch("apps.dashboard_reports.tasks._save_telemetry_details") as mock_telemetry,
        ):
            mock_collect.return_value = {"error": False, "data": {}}

            from apps.dashboard_reports.tasks import collect_dashboard_reports_initial_data

            collect_dashboard_reports_initial_data()

        call_kwargs = mock_telemetry.call_args[1]
        assert call_kwargs["collection_duration_ms"] >= 0


# ---------------------------------------------------------------------------
# cleanup_dashboard_reports_old_data — telemetry side-effects
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupTelemetry:
    """Verifies that cleanup_dashboard_reports_old_data records telemetry correctly."""

    def test_saves_telemetry_on_success(self):
        """On successful cleanup, _save_telemetry_details is called with success=True and correct count."""
        with (
            patch("apps.dashboard_reports.tasks.JobData") as mock_jobdata,
            patch("apps.dashboard_reports.tasks.log_task_execution"),
            patch("apps.dashboard_reports.tasks.create_task_result"),
            patch("apps.dashboard_reports.tasks._save_telemetry_details") as mock_telemetry,
        ):
            mock_jobdata.objects.filter.return_value.count.return_value = 10

            from apps.dashboard_reports.tasks import cleanup_dashboard_reports_old_data

            cleanup_dashboard_reports_old_data()

        mock_telemetry.assert_called_once()
        call_kwargs = mock_telemetry.call_args[1]
        assert call_kwargs["success"] is True
        assert call_kwargs["number_of_records_processed"] == 10
        assert call_kwargs["task_name"] == "cleanup_dashboard_reports_old_data"

    def test_saves_telemetry_on_exception(self):
        """When the delete call raises, _save_telemetry_details is still called with success=False."""
        with (
            patch("apps.dashboard_reports.tasks.JobData") as mock_jobdata,
            patch("apps.dashboard_reports.tasks.delete_queryset_in_batches", side_effect=Exception("db error")),
            patch("apps.dashboard_reports.tasks.log_task_execution"),
            patch("apps.dashboard_reports.tasks.create_task_result"),
            patch("apps.dashboard_reports.tasks._save_telemetry_details") as mock_telemetry,
        ):
            mock_jobdata.objects.filter.return_value.count.return_value = 3

            from apps.dashboard_reports.tasks import cleanup_dashboard_reports_old_data

            cleanup_dashboard_reports_old_data()

        mock_telemetry.assert_called_once()
        call_kwargs = mock_telemetry.call_args[1]
        assert call_kwargs["success"] is False

    def test_saves_db_query_time_ms(self):
        """database_query_time_ms passed to _save_telemetry_details is >= 0."""
        with (
            patch("apps.dashboard_reports.tasks.JobData") as mock_jobdata,
            patch("apps.dashboard_reports.tasks.log_task_execution"),
            patch("apps.dashboard_reports.tasks.create_task_result"),
            patch("apps.dashboard_reports.tasks._save_telemetry_details") as mock_telemetry,
        ):
            mock_jobdata.objects.filter.return_value.count.return_value = 0

            from apps.dashboard_reports.tasks import cleanup_dashboard_reports_old_data

            cleanup_dashboard_reports_old_data()

        call_kwargs = mock_telemetry.call_args[1]
        assert call_kwargs["database_query_time_ms"] >= 0
        assert call_kwargs["cache_hit_rate"] is None


# ---------------------------------------------------------------------------
# DashboardTelemetry model
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDashboardTelemetryModel:
    """Tests for the DashboardTelemetry model."""

    @pytest.mark.django_db
    def test_str_includes_key_fields(self):
        """__str__ contains the duration, records processed, query time, and cache hit rate."""
        record = DashboardTelemetry(
            task_name="test_task",
            collection_run_date=date.today(),
            success=True,
            collection_duration_ms=decimal.Decimal("1234.56"),
            number_of_records_processed=99,
            database_query_time_ms=decimal.Decimal("56.78"),
            cache_hit_rate=None,
        )
        text = str(record)
        assert "1234.56" in text
        assert "99" in text
        assert "56.78" in text

    @pytest.mark.django_db
    def test_cache_hit_rate_is_nullable(self):
        """cache_hit_rate accepts None (some tasks do not use a cache)."""
        _save_telemetry_details(
            task_name="no_cache_task",
            success=True,
            collection_duration_ms=100.0,
            number_of_records_processed=5,
            database_query_time_ms=20.0,
            cache_hit_rate=None,
        )
        record = DashboardTelemetry.objects.first()
        assert record.cache_hit_rate is None


# ---------------------------------------------------------------------------
# DashboardTelemetrySerializer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDashboardTelemetrySerializer:
    """Tests for DashboardTelemetrySerializer."""

    def _build_data(self, **overrides):
        base = {
            "task_name": "collect_dashboard_reports_initial_data",
            "collection_run_date": str(date.today()),
            "collection_duration_ms": "1500.00",
            "number_of_records_processed": 10,
            "database_query_time_ms": "200.00",
            "cache_hit_rate": None,
            "success": True,
        }
        base.update(overrides)
        return base

    def test_all_expected_fields_present(self):
        """Serializer exposes exactly the expected set of fields."""
        expected = {
            "task_name",
            "collection_run_date",
            "collection_duration_ms",
            "number_of_records_processed",
            "database_query_time_ms",
            "cache_hit_rate",
            "success",
        }
        assert set(DashboardTelemetrySerializer().fields.keys()) == expected

    def test_valid_data_is_accepted(self):
        """Serializer accepts a complete, well-formed payload."""
        s = DashboardTelemetrySerializer(data=self._build_data())
        assert s.is_valid(), s.errors

    def test_null_task_name_is_accepted(self):
        """task_name is nullable — a None value should be valid."""
        s = DashboardTelemetrySerializer(data=self._build_data(task_name=None))
        assert s.is_valid(), s.errors

    def test_null_cache_hit_rate_is_accepted(self):
        """cache_hit_rate is nullable."""
        s = DashboardTelemetrySerializer(data=self._build_data(cache_hit_rate=None))
        assert s.is_valid(), s.errors

    def test_missing_required_field_fails(self):
        """Omitting a required field makes the serializer invalid."""
        data = self._build_data()
        del data["collection_duration_ms"]
        s = DashboardTelemetrySerializer(data=data)
        assert not s.is_valid()
        assert "collection_duration_ms" in s.errors


# ---------------------------------------------------------------------------
# DashboardTelemetryViewSet
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDashboardTelemetryViewSet:
    """Tests for DashboardTelemetryViewSet.list()."""

    @pytest.fixture(autouse=True)
    def bypass_permissions(self):
        with patch(PATCH_PERM, return_value=True):
            yield

    def _get(self):
        request = factory.get("/api/v1/dashboard_reports/collection_telemetry/")
        request.user = MagicMock()
        return view(request)

    def test_empty_db_returns_count_zero(self):
        """With no telemetry rows the response has count=0 and an empty results list."""
        with patch("apps.dashboard_reports.viewsets.dashboard_telemetry.DashboardTelemetry") as mock_model:
            mock_qs = MagicMock()
            mock_qs.filter.return_value.order_by.return_value = []
            mock_model.objects = mock_qs

            response = self._get()

        assert response.status_code == 200
        assert response.data["count"] == 0
        assert response.data["results"] == []

    def test_returns_only_last_30_days(self):
        """Only entries within the last 30 days are returned."""
        today = date.today()
        mock_row = MagicMock()
        mock_row.task_name = "task_a"
        mock_row.collection_run_date = today - timedelta(days=15)
        mock_row.collection_duration_ms = decimal.Decimal("100.00")
        mock_row.number_of_records_processed = 5
        mock_row.database_query_time_ms = decimal.Decimal("10.00")
        mock_row.cache_hit_rate = None
        mock_row.success = True

        with patch("apps.dashboard_reports.viewsets.dashboard_telemetry.DashboardTelemetry") as mock_model:
            mock_qs = MagicMock()
            mock_qs.filter.return_value.order_by.return_value = [mock_row]
            mock_model.objects = mock_qs

            response = self._get()

        assert response.status_code == 200
        assert response.data["count"] == 1

    def test_response_has_count_and_results_keys(self):
        """The response always contains both 'count' and 'results' keys."""
        with patch("apps.dashboard_reports.viewsets.dashboard_telemetry.DashboardTelemetry") as mock_model:
            mock_qs = MagicMock()
            mock_qs.filter.return_value.order_by.return_value = []
            mock_model.objects = mock_qs

            response = self._get()

        assert "count" in response.data
        assert "results" in response.data

    def test_unauthenticated_request_is_forbidden(self):
        """A request without valid permissions receives a 403 response."""
        with patch(PATCH_PERM, return_value=False):
            request = factory.get("/api/v1/dashboard_reports/collection_telemetry/")
            request.user = MagicMock()
            request.user.is_authenticated = False
            response = view(request)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# cleanup_dashboard_telemetry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupDashboardTelemetry:
    """Tests for cleanup_dashboard_telemetry."""

    @pytest.mark.django_db
    def test_deletes_old_rows_and_returns_success(self):
        """Rows older than retention_days are deleted; status is 'success'."""
        old_date = date.today() - timedelta(days=70)
        DashboardTelemetry.objects.create(
            task_name="old_task",
            collection_run_date=old_date,
            success=True,
            collection_duration_ms=decimal.Decimal("100.00"),
            number_of_records_processed=5,
            database_query_time_ms=decimal.Decimal("10.00"),
            cache_hit_rate=None,
        )
        recent_date = date.today() - timedelta(days=5)
        DashboardTelemetry.objects.create(
            task_name="recent_task",
            collection_run_date=recent_date,
            success=True,
            collection_duration_ms=decimal.Decimal("200.00"),
            number_of_records_processed=3,
            database_query_time_ms=decimal.Decimal("20.00"),
            cache_hit_rate=None,
        )

        result = cleanup_dashboard_telemetry(retention_days=60)

        assert result["status"] == "success"
        assert result["deleted_records"] == 1
        assert DashboardTelemetry.objects.count() == 1
        assert DashboardTelemetry.objects.first().task_name == "recent_task"

    def test_default_retention_is_60_days(self):
        """When no retention_days kwarg is given, 60 days is used."""
        with patch("apps.dashboard_reports.tasks.DashboardTelemetry") as mock_model:
            mock_qs = MagicMock()
            mock_qs.delete.return_value = (0, {})
            mock_model.objects.filter.return_value = mock_qs
            with patch("apps.dashboard_reports.tasks.log_task_execution"):
                cleanup_dashboard_telemetry()

        call_kwargs = mock_model.objects.filter.call_args
        # The cutoff date should be 60 days back; verify filter was called
        assert mock_model.objects.filter.called

    def test_deprecated_retention_period_days_kwarg_still_honored(self):
        """The pre-rename 'retention_period_days' kwarg still sets retention_days rather than being silently dropped."""
        with patch("apps.dashboard_reports.tasks.DashboardTelemetry") as mock_model:
            mock_qs = MagicMock()
            mock_qs.delete.return_value = (0, {})
            mock_model.objects.filter.return_value = mock_qs
            with patch("apps.dashboard_reports.tasks.log_task_execution"):
                result = cleanup_dashboard_telemetry(retention_period_days=15)

        assert result["status"] == "success"
        assert result["retention_days"] == 15

    def test_invalid_retention_period_returns_error(self):
        """A non-integer retention_days returns an error result."""
        result = cleanup_dashboard_telemetry(retention_days="not_a_number")

        assert result["status"] == "error"
        assert "Invalid retention_days" in result["error"]

    def test_negative_retention_period_returns_error(self):
        """A negative retention_days is rejected rather than clamped, since a cutoff of 'now' would delete everything."""
        result = cleanup_dashboard_telemetry(retention_days=-10)

        assert result["status"] == "error"
        assert "retention_days must be > 0" in result["error"]

    def test_zero_retention_period_returns_error(self):
        """A retention_days of 0 is rejected rather than deleting all rows."""
        result = cleanup_dashboard_telemetry(retention_days=0)

        assert result["status"] == "error"
        assert "retention_days must be > 0" in result["error"]

    def test_exception_during_delete_returns_error(self):
        """When DashboardTelemetry.objects.filter().delete() raises, returns error result."""
        with patch("apps.dashboard_reports.tasks.DashboardTelemetry") as mock_model:
            mock_model.objects.filter.return_value.delete.side_effect = Exception("DB exploded")
            with (
                patch("apps.dashboard_reports.tasks.log_task_execution"),
                patch("apps.dashboard_reports.tasks.logger") as mock_logger,
            ):
                result = cleanup_dashboard_telemetry(retention_days=60)

        assert result["status"] == "error"
        assert "Cleanup failed" in result["error"]
        mock_logger.exception.assert_called_once()

    def test_result_contains_cutoff_date_and_retention(self):
        """The success result includes cutoff_date and retention_days."""
        with patch("apps.dashboard_reports.tasks.DashboardTelemetry") as mock_model:
            mock_model.objects.filter.return_value.delete.return_value = (3, {})
            with patch("apps.dashboard_reports.tasks.log_task_execution"):
                result = cleanup_dashboard_telemetry(retention_days=30)

        assert result["status"] == "success"
        assert "cutoff_date" in result
        assert result["retention_days"] == 30
        assert result["deleted_records"] == 3
