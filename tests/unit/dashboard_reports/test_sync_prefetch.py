"""Tests for batch prefetching in the dashboard job sync pipeline."""

from datetime import UTC, datetime
from unittest import mock

import pytest

from apps.dashboard_reports import tasks
from apps.dashboard_reports.models import JobData, JobHostSummary, JobLabel, TemplateMetadata
from apps.dashboard_reports.tasks import _prefetch_sync_caches, _sync_jobs_atomically

NOW = datetime(2024, 1, 1, tzinfo=UTC)
BATCH_SIZE = 20
TEMPLATE_IDS = (101, 102, 103)
_UNSET = object()

# Measured for 20 jobs: 247 queries for a fresh batch, 249 when updating existing jobs. Each job
# costs ~12 (savepoint + update_or_create + label/host-summary bulk writes + DAB/model hooks), on top
# of the template lookups and the 4 prefetch queries. The +2 margin keeps the cap tight: dropping
# the label or host-summary prefetch adds one query per job and must fail these tests.
CREATE_BATCH_QUERY_CAP = 249
UPDATE_BATCH_QUERY_CAP = 251


def _job(
    job_id: int,
    template_id: int = TEMPLATE_IDS[0],
    labels: list | None = _UNSET,
    host_summaries: list | None = _UNSET,
) -> dict:
    """Build an assembled AWX job payload with labels and host summaries by default."""
    return {
        "id": job_id,
        "name": f"template {template_id}",
        "unified_job_template_id": template_id,
        "organization_id": 1,
        "organization_name": "Org",
        "project_id": None,
        "project_name": None,
        "status": "successful",
        "started": NOW,
        "finished": NOW,
        "elapsed": 60.0,
        "launched_by_id": None,
        "launched_by_username": None,
        "created": NOW,
        "modified": NOW,
        "num_hosts": 2,
        "labels": [1, 2] if labels is _UNSET else labels,
        "host_summaries": (
            [
                {"id": job_id * 10, "host_id": 1, "host_name": "host-1"},
                {"id": job_id * 10 + 1, "host_id": 2, "host_name": "host-2"},
            ]
            if host_summaries is _UNSET
            else host_summaries
        ),
        "job_type": "job",
    }


def _batch(size: int = BATCH_SIZE, offset: int = 0) -> list[dict]:
    """Jobs sharing 3 templates, each with labels and host summaries."""
    return [
        _job(job_id=1000 + offset + index, template_id=TEMPLATE_IDS[index % len(TEMPLATE_IDS)]) for index in range(size)
    ]


def _create_templates() -> None:
    """Create the templates up front so query-count assertions only measure the sync path."""
    for template_id in TEMPLATE_IDS:
        TemplateMetadata.objects.create(
            template_id=template_id,
            template_name=f"template {template_id}",
            time_taken_manually_execute_minutes=60,
            time_taken_create_automation_minutes=20,
        )


@pytest.mark.unit
@pytest.mark.django_db
class TestSyncJobsAtomicallyPrefetch:
    """The batch prefetch must keep template/label/host-summary lookups independent of batch size."""

    def test_prefetch_query_count_does_not_grow_with_batch(self, django_assert_num_queries):
        """Prefetching 20 or 40 jobs costs the same small number of queries."""
        _create_templates()

        with django_assert_num_queries(2):
            template_cache, labels, summaries = _prefetch_sync_caches(_batch(20))

        assert len(template_cache) == 3
        assert labels == {}
        assert summaries == {}

        with django_assert_num_queries(2):
            _prefetch_sync_caches(_batch(40, offset=100))

        assert _sync_jobs_atomically(_batch(20)) == []

        with django_assert_num_queries(4):
            _, labels, summaries = _prefetch_sync_caches(_batch(20))

        assert set(labels) == {job["id"] for job in _batch(20)}
        assert set(summaries) == {job["id"] for job in _batch(20)}

    def test_create_new_batch_query_count_is_bounded(self, django_assert_max_num_queries):
        """A fresh batch issues one prefetch per relation type plus per-job writes, not per-job lookups."""
        _create_templates()
        with django_assert_max_num_queries(CREATE_BATCH_QUERY_CAP):
            failed = _sync_jobs_atomically(_batch())

        assert failed == []
        assert JobData.objects.count() == BATCH_SIZE
        assert TemplateMetadata.objects.count() == len(TEMPLATE_IDS)
        assert JobLabel.objects.count() == BATCH_SIZE * 2
        assert JobHostSummary.objects.count() == BATCH_SIZE * 2

    def test_update_existing_batch_query_count_is_bounded(self, django_assert_max_num_queries):
        """Updating existing jobs reuses prefetched templates, labels, and host summaries."""
        assert _sync_jobs_atomically(_batch()) == []

        updated = [
            _job(
                job_id=job["id"],
                template_id=job["unified_job_template_id"],
                labels=[2, 3],
                host_summaries=[
                    {"id": job["id"] * 10, "host_id": 1, "host_name": "renamed-1"},
                    {"id": job["id"] * 10 + 2, "host_id": 3, "host_name": "host-3"},
                ],
            )
            for job in _batch()
        ]

        with django_assert_max_num_queries(UPDATE_BATCH_QUERY_CAP):
            failed = _sync_jobs_atomically(updated)

        assert failed == []
        assert JobData.objects.count() == BATCH_SIZE
        assert set(JobLabel.objects.values_list("label_id", flat=True)) == {2, 3}
        assert set(JobHostSummary.objects.values_list("host_summary_id", flat=True)) == {
            job["id"] * 10 for job in updated
        } | {job["id"] * 10 + 2 for job in updated}

    def test_duplicate_job_id_uses_database_on_second_occurrence(self):
        """A repeated job_id must not reuse the already-consumed prefetched maps."""
        assert _sync_jobs_atomically([_job(job_id=2000, labels=[1, 2])]) == []

        first = _job(job_id=2000, labels=[2, 3], host_summaries=[{"id": 3000, "host_id": 2, "host_name": "new"}])
        second = _job(job_id=2000, labels=[3, 4], host_summaries=[{"id": 3001, "host_id": 3, "host_name": "newer"}])

        assert _sync_jobs_atomically([first, second]) == []

        job = JobData.objects.get(job_id=2000)
        assert JobLabel.objects.filter(job_data=job).count() == 2
        assert set(JobLabel.objects.filter(job_data=job).values_list("label_id", flat=True)) == {3, 4}
        assert set(JobHostSummary.objects.filter(job_data=job).values_list("host_summary_id", flat=True)) == {3001}

    def test_duplicate_job_id_created_in_same_batch(self):
        """A job created by the first occurrence is updated correctly by the second one."""
        first = _job(job_id=2100, labels=[1, 2])
        second = _job(job_id=2100, labels=[2, 3], host_summaries=[])

        assert _sync_jobs_atomically([first, second]) == []

        job = JobData.objects.get(job_id=2100)
        assert set(JobLabel.objects.filter(job_data=job).values_list("label_id", flat=True)) == {2, 3}
        assert not JobHostSummary.objects.filter(job_data=job).exists()

    def test_none_relationships_preserve_existing_records(self):
        """labels=None and host_summaries=None leave the stored child rows untouched."""
        original = _job(
            job_id=2200,
            labels=[7],
            host_summaries=[{"id": 4000, "host_id": 1, "host_name": "h"}],
        )
        assert _sync_jobs_atomically([original]) == []

        assert _sync_jobs_atomically([_job(job_id=2200, labels=None, host_summaries=None)]) == []

        job = JobData.objects.get(job_id=2200)
        assert list(JobLabel.objects.filter(job_data=job).values_list("label_id", flat=True)) == [7]
        assert list(JobHostSummary.objects.filter(job_data=job).values_list("host_summary_id", flat=True)) == [4000]

    def test_none_relationships_create_no_children(self):
        """labels=None and host_summaries=None do not create child rows for a new job."""
        assert _sync_jobs_atomically([_job(job_id=2300, labels=None, host_summaries=None)]) == []

        job = JobData.objects.get(job_id=2300)
        assert not JobLabel.objects.filter(job_data=job).exists()
        assert not JobHostSummary.objects.filter(job_data=job).exists()

    def test_jobs_without_template_id_share_synthetic_metadata(self):
        """unified_job_template_id=None still links every job to one synthetic TemplateMetadata row."""
        jobs = [_job(job_id=2500, template_id=None), _job(job_id=2501, template_id=None)]

        assert _sync_jobs_atomically(jobs) == []

        metadata = TemplateMetadata.objects.get(template_name="template None")
        assert metadata.template_id < 0
        stored = JobData.objects.filter(job_id__in=[2500, 2501])
        assert set(stored.values_list("template_metadata_id", flat=True)) == {metadata.pk}
        assert set(stored.values_list("template_id", flat=True)) == {None}

    def test_template_cache_miss_in_rolled_back_savepoint_does_not_poison_batch(self, monkeypatch):
        """A template created in a job's rolled-back savepoint must not leak into later jobs."""
        template_cache: dict = {}
        # Simulate the rare miss: pre-creation did not happen, so the first job creates the template
        # inside its own savepoint and then fails.
        monkeypatch.setattr(tasks, "_ensure_template_metadata", lambda job_results: None)
        monkeypatch.setattr(tasks, "_prefetch_sync_caches", lambda job_results: (template_cache, None, None))

        bad = _job(job_id=2600, template_id=999, labels=[{"unhashable": "label id"}])
        good = _job(job_id=2601, template_id=999)

        # Only the bad job fails: the good job saw an empty cache and created the template itself.
        assert _sync_jobs_atomically([bad, good]) == [2600]

        # The batch was rolled back and the cache never held the ghost instance.
        assert template_cache == {}
        assert not TemplateMetadata.objects.filter(template_id=999).exists()
        assert not JobData.objects.filter(job_id__in=[2600, 2601]).exists()

    def test_batch_logs_one_info_summary(self):
        """One INFO summary per batch keeps operators informed now that per-job logs are DEBUG."""
        _create_templates()

        with mock.patch.object(tasks.logger, "info") as info:
            assert _sync_jobs_atomically(_batch()) == []

        info.assert_called_once_with("Dashboard batch sync complete: %s job(s) synced", BATCH_SIZE)


@pytest.mark.unit
@pytest.mark.django_db
class TestTemplateMetadataCache:
    """TemplateMetadata.get_by_awx_id_or_name must honour an optional prefetch cache."""

    @staticmethod
    def _template(template_id: int = 501, name: str | None = None) -> TemplateMetadata:
        return TemplateMetadata.objects.create(
            template_id=template_id,
            template_name=name or f"template {template_id}",
            time_taken_manually_execute_minutes=1,
            time_taken_create_automation_minutes=1,
        )

    def test_cache_hit_skips_database(self, django_assert_num_queries):
        template = self._template()
        cache = {template.template_id: template}

        with django_assert_num_queries(0):
            result = TemplateMetadata.get_by_awx_id_or_name("template 501", awx_id=501, cache=cache)

        assert result is template

    def test_cache_miss_falls_through_without_mutating_cache(self, django_assert_num_queries):
        """A cache miss reads through to the database but must not write the miss into the cache."""
        template = self._template(template_id=-1, name="negative template")
        cache = {}

        # ID lookup, name lookup, audit user lookup, and the template_id promotion update.
        with django_assert_num_queries(4):
            result = TemplateMetadata.get_by_awx_id_or_name("negative template", awx_id=777, cache=cache)

        assert result.template_id == 777
        assert cache == {}
        template.refresh_from_db()
        assert template.template_id == 777

    def test_awx_id_none_does_not_touch_cache(self):
        template = self._template(template_id=-5, name="name only template")
        cache: dict = {}

        result = TemplateMetadata.get_by_awx_id_or_name("name only template", awx_id=None, cache=cache)

        assert result.pk == template.pk
        assert cache == {}
