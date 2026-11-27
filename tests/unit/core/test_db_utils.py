"""Unit tests for apps.core.db_utils."""

from unittest.mock import MagicMock

import pytest

from apps.core.db_utils import DEFAULT_DELETE_BATCH_SIZE, delete_queryset_in_batches, resolve_batch_size


@pytest.mark.unit
class TestResolveBatchSize:
    """Tests for resolve_batch_size."""

    def test_returns_valid_positive_integer(self):
        assert resolve_batch_size(50) == 50
        assert resolve_batch_size("50") == 50

    @pytest.mark.parametrize("value", [None, "abc", "", 0, -1, 1.5, True, False, object()])
    def test_falls_back_to_default_for_invalid_values(self, value):
        assert resolve_batch_size(value) == DEFAULT_DELETE_BATCH_SIZE

    def test_uses_custom_default(self):
        assert resolve_batch_size("abc", default=5) == 5


@pytest.mark.unit
def test_delete_queryset_in_batches_rejects_non_positive_batch_size():
    """A non-positive batch size is rejected before any row is queried."""
    with pytest.raises(ValueError, match="batch_size must be > 0"):
        delete_queryset_in_batches(object(), batch_size=0)


@pytest.mark.unit
def test_delete_queryset_in_batches_rejects_boolean_batch_size():
    """A boolean batch size is rejected instead of being treated as 0 or 1."""
    with pytest.raises(ValueError, match="batch_size must be > 0"):
        delete_queryset_in_batches(object(), batch_size=True)


@pytest.mark.unit
def test_delete_queryset_in_batches_stops_on_zero_progress():
    """A batch that deletes nothing ends the loop instead of spinning forever."""
    queryset = MagicMock()
    queryset.order_by.return_value.values_list.return_value.__getitem__.return_value = [1, 2]
    queryset.model.objects.filter.return_value.delete.return_value = (0, {})

    assert delete_queryset_in_batches(queryset, batch_size=2) == {}
    queryset.model.objects.filter.return_value.delete.assert_called_once()
