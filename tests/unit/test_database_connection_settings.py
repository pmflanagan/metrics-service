"""Tests for database connection lifetime settings (Step 7 performance tuning)."""

import pytest
from django.conf import settings


@pytest.mark.unit
class TestDefaultDatabaseConnectionSettings:
    """Verify the production defaults reuse the primary DB connection."""

    def test_default_alias_reuses_connections(self):
        """The default alias keeps connections alive for 60s with health checks."""
        from apps.settings import defaults

        database = defaults.DATABASES["default"]

        assert database["CONN_MAX_AGE"] == 60
        assert database["CONN_HEALTH_CHECKS"] is True

    def test_awx_alias_does_not_set_connection_reuse(self):
        """The awx alias stays untouched; its raw psycopg3 connection is managed in code."""
        from apps.settings import defaults

        database = defaults.DATABASES["awx"]

        assert "CONN_MAX_AGE" not in database
        assert "CONN_HEALTH_CHECKS" not in database


@pytest.mark.unit
class TestTestModeDatabaseConnectionSettings:
    """Verify test settings disable persistent connections to keep teardown clean."""

    def test_test_settings_set_conn_max_age_zero(self):
        """apps/settings/test.py explicitly sets CONN_MAX_AGE to 0."""
        from apps.settings import test as test_settings

        assert test_settings.DATABASES__default__CONN_MAX_AGE == 0

    def test_active_test_settings_disable_persistent_connections(self):
        """The active test settings reuse defaults but with CONN_MAX_AGE == 0."""
        if not getattr(settings, "TESTING", False):
            pytest.skip(
                "apps/settings/test.py is not active; local pytest runs default to "
                "development mode. CI sets METRICS_SERVICE_MODE=test."
            )

        database = settings.DATABASES["default"]

        assert database["CONN_MAX_AGE"] == 0
        assert database["CONN_HEALTH_CHECKS"] is True
