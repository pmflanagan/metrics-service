"""Tests for API root view."""

from unittest import mock

import pytest
from django.conf import settings
from django.test import TestCase
from django.urls import clear_url_caches, get_resolver
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory


def get_service_name():
    """Get the service name from settings."""
    return settings.ROOT_URLCONF.split(".")[0].replace("_", "-")


SERVICE_NAME = get_service_name()

# Test parameters for JSON API root URL generation
# (path, expected_key, expected_prefix)
API_ROOT_URL_PATTERNS = [
    pytest.param("/", "api", None, id="root-canonical"),
    pytest.param(f"/{SERVICE_NAME}/", "api", f"/{SERVICE_NAME}/", id="root-service-prefix"),
    pytest.param("/api/", "v1", None, id="api-canonical"),
    pytest.param(f"/api/{SERVICE_NAME}/", "v1", f"/api/{SERVICE_NAME}/", id="api-service-prefix"),
]


class TestAPIRootView(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_root_returns_endpoints(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        # Should have some endpoints listed
        self.assertIsInstance(data, dict)

    def test_v1_root_returns_endpoints(self):
        response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIsInstance(data, dict)
        # v1 should have organizations, teams, users endpoints
        self.assertIn("organizations", data)
        self.assertIn("teams", data)
        self.assertIn("users", data)

    def test_api_root_excludes_debug_paths(self):
        response = self.client.get("/")
        data = response.json()
        # Should not include __debug__ paths
        for key in data:
            self.assertNotIn("__debug__", key)

    def test_api_root_get_view_name(self):
        from apps.core.views import APIRootView

        # Test with custom view_name
        view = APIRootView()
        view.view_name = "Custom API"
        self.assertEqual(view.get_view_name(), "Custom API")

        # Test fallback
        view.view_name = None
        self.assertEqual(view.get_view_name(), "API Root")


class TestAPIRootViewPatternCaching(TestCase):
    """The extracted URL pattern list is cached per resolver instance."""

    def setUp(self):
        from apps.core.views.api_root import _EXTRACTED_PATTERNS_CACHE

        self.client = APIClient()
        _EXTRACTED_PATTERNS_CACHE.clear()
        clear_url_caches()

    def tearDown(self):
        from apps.core.views.api_root import _EXTRACTED_PATTERNS_CACHE

        _EXTRACTED_PATTERNS_CACHE.clear()
        clear_url_caches()

    def test_repeated_requests_extract_patterns_once(self):
        """Repeated GETs hit the cache and return identical responses."""
        import apps.core.views.api_root as api_root_module

        original_simplify_regex = api_root_module.simplify_regex
        simplify_calls = []
        extract_calls = []

        def counting_simplify_regex(raw_pattern):
            simplify_calls.append(raw_pattern)
            return original_simplify_regex(raw_pattern)

        resolver = get_resolver()

        def spy_extract_patterns(view, patterns, current_path):
            # The method recurses, so only count invocations on the root pattern list.
            if patterns is resolver.url_patterns:
                extract_calls.append(current_path)
            yield from original_extract_patterns(view, patterns, current_path)

        original_extract_patterns = api_root_module.APIRootView._extract_patterns

        with (
            mock.patch.object(api_root_module, "simplify_regex", side_effect=counting_simplify_regex),
            mock.patch.object(api_root_module.APIRootView, "_extract_patterns", spy_extract_patterns),
        ):
            first = self.client.get("/api/v1/")
            calls_after_first = len(simplify_calls)
            second = self.client.get("/api/v1/")
            third = self.client.get("/api/v1/")

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        assert third.status_code == status.HTTP_200_OK

        # Extraction happened exactly once, on the first request.
        assert len(extract_calls) == 1
        assert calls_after_first > 0
        assert len(simplify_calls) == calls_after_first

        # Cached once per resolver, with one entry per extracted pattern.
        cached = api_root_module._EXTRACTED_PATTERNS_CACHE[resolver]
        assert len(cached) == calls_after_first

        # Responses are byte-for-byte equivalent JSON.
        assert first.json() == second.json() == third.json()

    def test_clear_url_caches_repopulates_new_resolver(self):
        """Clearing URL caches yields a new resolver with a fresh cache entry."""
        import apps.core.views.api_root as api_root_module

        resolver_before = get_resolver()
        self.client.get("/api/v1/")
        assert resolver_before in api_root_module._EXTRACTED_PATTERNS_CACHE
        cached_before = api_root_module._EXTRACTED_PATTERNS_CACHE[resolver_before]

        clear_url_caches()
        resolver_after = get_resolver()
        assert resolver_after is not resolver_before
        assert resolver_after not in api_root_module._EXTRACTED_PATTERNS_CACHE

        response = self.client.get("/api/v1/")
        assert response.status_code == status.HTTP_200_OK
        assert resolver_after in api_root_module._EXTRACTED_PATTERNS_CACHE
        assert api_root_module._EXTRACTED_PATTERNS_CACHE[resolver_after] == cached_before

    def test_non_weakreferenceable_resolver_falls_back(self):
        """A resolver that cannot be weakly referenced must not raise (test doubles)."""
        import apps.core.views.api_root as api_root_module

        class _NoWeakrefResolver:
            __slots__ = ("url_patterns",)

            def __init__(self):
                self.url_patterns = []

        request = APIRequestFactory().get("/api/v1/")
        request.resolver_match = None
        view = api_root_module.APIRootView()

        with mock.patch.object(api_root_module, "get_resolver", return_value=_NoWeakrefResolver()):
            response = view.get(request)

        assert response.status_code == status.HTTP_200_OK
        assert response.data == {}


class TestAPIRootViewUnit(TestCase):
    """Unit tests for APIRootView."""

    def test_view_class_exists(self):
        from apps.core.views import APIRootView

        self.assertTrue(callable(APIRootView))

    def test_view_has_get_method(self):
        from apps.core.views import APIRootView

        view = APIRootView()
        self.assertTrue(hasattr(view, "get"))

    def test_view_get_view_name(self):
        from apps.core.views import APIRootView

        # Test with custom view_name
        view = APIRootView()
        view.view_name = "Custom API"
        self.assertEqual(view.get_view_name(), "Custom API")

        # Test fallback
        view.view_name = None
        self.assertEqual(view.get_view_name(), "API Root")

    def test_view_description(self):
        from apps.core.views import APIRootView

        view = APIRootView()
        # Should have a docstring/description
        self.assertIsNotNone(view.__class__.__doc__)


@pytest.mark.django_db
class TestAPIRootViewURLGeneration:
    """Test that API root views generate correct URLs for all access patterns.

    Four access patterns:
    1. /                              → canonical URLs (no prefix)
    2. /<service-name>/               → prefixed URLs (/<service-name>/...)
    3. /api/                          → canonical URLs (no prefix)
    4. /api/<service-name>/           → prefixed URLs (/api/<service-name>/...)
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        self.client = APIClient()
        self.service_name = SERVICE_NAME

    @pytest.mark.parametrize("path,expected_key,expected_prefix", API_ROOT_URL_PATTERNS)
    def test_json_urls_have_correct_prefix(self, path, expected_key, expected_prefix):
        """JSON response URLs have correct prefix."""
        response = self.client.get(path)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert expected_key in data, f"Expected key '{expected_key}' in response"

        url = data[expected_key]
        if expected_prefix is None:
            # Canonical - should not contain service name
            assert f"/{self.service_name}/" not in url, f"URL {url} should not have service prefix"
        else:
            # Prefixed - should contain the expected prefix
            assert expected_prefix in url, f"URL {url} should contain {expected_prefix}"
