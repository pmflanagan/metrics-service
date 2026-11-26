"""Regression tests for the performance indexes on task and analytics models.

Step 3 of the low-risk performance plan adds covering indexes for the hottest
status/timestamp queries. These tests assert the indexes are declared on the
models and are present in the database schema (``pg_indexes`` equivalent via
Django's cross-backend introspection).
"""

import pytest
from django.db import connection

from apps.analytics.models import AnalyticsPayload
from apps.tasks.models import AnonymizedMetricsPayload, Task, TaskExecution

pytestmark = [pytest.mark.unit, pytest.mark.django_db]

# model -> index definitions that must be declared in Meta.indexes
EXPECTED_MODEL_INDEXES = {
    Task: {
        ("status", "started_at"),
        ("status", "completed_at"),
        ("function_name", "status"),
    },
    TaskExecution: {
        ("task", "status"),
        ("-started_at",),
    },
    AnonymizedMetricsPayload: {
        ("status", "created"),
        ("summary_date",),
        ("-modified",),
    },
    AnalyticsPayload: {
        ("created",),
        ("collector", "since"),
        ("collector", "until"),
        ("collector", "started_at"),
        ("collector", "finished_at"),
    },
}

# model -> index column sets expected in the live database schema
EXPECTED_DB_INDEXES = {
    Task: [
        ["status", "started_at"],
        ["status", "completed_at"],
        ["function_name", "status"],
    ],
    # Database columns are used here, so the FK is reported as ``task_id``.
    TaskExecution: [
        ["task_id", "status"],
        ["started_at"],
    ],
    AnonymizedMetricsPayload: [
        ["modified"],
    ],
    AnalyticsPayload: [
        ["collector", "finished_at"],
    ],
}


def _declared_index_fields(model) -> set[tuple[str, ...]]:
    """Return the field tuples declared in ``model._meta.indexes``."""
    return {tuple(index.fields) for index in model._meta.indexes}


def _db_index_columns(model) -> set[tuple[str, ...]]:
    """Return column tuples for every index on the model's table, per DB introspection."""
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
    return {
        tuple(info["columns"]) for info in constraints.values() if info.get("index") and not info.get("primary_key")
    }


@pytest.mark.parametrize("model", EXPECTED_MODEL_INDEXES, ids=lambda model: model.__name__)
def test_model_meta_declares_expected_indexes(model):
    """Each model declares the expected indexes without dropping existing ones."""
    declared = _declared_index_fields(model)

    assert EXPECTED_MODEL_INDEXES[model].issubset(declared)


@pytest.mark.parametrize("model", EXPECTED_DB_INDEXES, ids=lambda model: model.__name__)
def test_database_has_expected_indexes(model):
    """The migrations create the expected indexes in the database schema."""
    db_indexes = _db_index_columns(model)

    for expected_columns in EXPECTED_DB_INDEXES[model]:
        assert tuple(expected_columns) in db_indexes
