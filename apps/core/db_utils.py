"""Shared database helpers for maintenance tasks."""

from typing import Any

from django.db.models import QuerySet

DEFAULT_DELETE_BATCH_SIZE = 1000


def resolve_batch_size(value: Any, default: int = DEFAULT_DELETE_BATCH_SIZE) -> int:
    """Return ``value`` as a positive integer, falling back to ``default`` when invalid."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return default
    try:
        batch_size = int(value)
    except ValueError:
        return default
    return batch_size if batch_size > 0 else default


def delete_queryset_in_batches(queryset: QuerySet, *, batch_size: int = DEFAULT_DELETE_BATCH_SIZE) -> dict[str, int]:
    """Delete the rows matched by ``queryset`` in primary-key batches.

    Each batch is a separate ``DELETE`` with its own transaction, so retention
    cleanups never hold locks on a whole table or build one unbounded list of IDs.
    Rows are re-read from the still-filtered queryset on every pass, so deleting a
    batch never invalidates the next one.

    Returns the aggregated Django deletion info (``{"app_label.ModelName": count}``)
    across all batches, including rows removed by cascade.
    """
    if isinstance(batch_size, bool) or batch_size < 1:
        raise ValueError(f"batch_size must be > 0, got {batch_size!r}")

    deletion_info: dict[str, int] = {}
    while True:
        ids = list(queryset.order_by("pk").values_list("pk", flat=True)[:batch_size])
        if not ids:
            return deletion_info
        deleted_count, batch_info = queryset.model.objects.filter(pk__in=ids).delete()
        if deleted_count == 0:
            return deletion_info
        for label, count in batch_info.items():
            deletion_info[label] = deletion_info.get(label, 0) + count
