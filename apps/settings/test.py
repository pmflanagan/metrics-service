"""
Testing environment overrides
Inherits from ./defaults.py and adds test-specific settings

NOTE: Tests use PostgreSQL to match the production environment setup.
"""

import os

# Basic required settings
DEBUG = False
TESTING = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
# Use env in CI/SAST to avoid hardcoded-secret findings; default is test-only and never production
SECRET_KEY = os.environ.get("METRICS_SERVICE_SECRET_KEY", "test-only-secret-key-for-testing-purposes-only")

# Database: use 127.0.0.1 (explicit IPv4) so tests connect to Docker postgres without extra env vars.
# localhost resolves to IPv6 first on macOS which fails for Docker-mapped ports.
DATABASES__default__HOST = os.environ.get("METRICS_SERVICE_DATABASES__DEFAULT__HOST", "127.0.0.1")
DATABASES__default__PASSWORD = os.environ.get("METRICS_SERVICE_DATABASES__DEFAULT__PASSWORD", "metrics_service")
DATABASES__default__USER = os.environ.get("METRICS_SERVICE_DATABASES__DEFAULT__USER", "metrics_service")
# Disable persistent connections so pytest can tear down/reuse the test database
# without lingering sessions holding locks between runs.
DATABASES__default__CONN_MAX_AGE = 0
SEGMENT_WRITE_KEY = "test-only-segment-write-key-for-testing-only-purposes"

ANSIBLE_BASE_BYPASS_SUPERUSER_FLAGS = ["is_superuser"]
ANSIBLE_BASE_BYPASS_ACTION_FLAGS = {
    "view": "is_platform_auditor",
}

# Additional DAB RBAC settings required for tests
ANSIBLE_BASE_CHECK_RELATED_PERMISSIONS = ["use", "change", "view"]
ANSIBLE_BASE_CACHE_PARENT_PERMISSIONS = False
ANSIBLE_BASE_EVALUATIONS_IGNORE_CONFLICTS = False
ANSIBLE_BASE_DELETE_REQUIRE_CHANGE = False
ANSIBLE_BASE_CREATOR_DEFAULTS = ["add", "change", "delete", "view"]

# Service identification
SERVICE_ID = "test-service-id"


# Disable caching during tests
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

# Use faster password hashing for tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Email settings for testing
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Disable logging during tests
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "null": {
            "class": "logging.NullHandler",
        },
    },
    "root": {
        "handlers": ["null"],
    },
}

# Disable dispatcherd during tests to avoid background processes
DISPATCHERD_ENABLED = False

# Disable feature enables during tests
# NOTE: relies on get_feature_enabled_from_db falling back to directly using these settings when not in DB...
# ...and on init-default-settings never happening during tests. If it does, @override_settings won't work.
FEATURE = {
    "ANONYMIZED_DATA_COLLECTION": False,
}

# REST Framework overrides for tests - use __ syntax to avoid replacing the whole dict
# (which would drop DEFAULT_RENDERER_CLASSES and other keys set by defaults.py / core/settings.py)
REST_FRAMEWORK__DEFAULT_AUTHENTICATION_CLASSES = [
    "rest_framework.authentication.SessionAuthentication",
]
REST_FRAMEWORK__DEFAULT_PERMISSION_CLASSES = [
    "rest_framework.permissions.AllowAny",
]
REST_FRAMEWORK__TEST_REQUEST_DEFAULT_FORMAT = "json"

# Test database settings
TEST_DATABASE_PREFIX = "test_"
