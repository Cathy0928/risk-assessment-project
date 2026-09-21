"""
Tests for riskGenie/services/cve_sync_service.py.

Uses a fully in-memory FakeSupabase (covering cve_documents and
cve_sync_runs) and a FakeNVDClient — no real Supabase project, no real
NVD API, no network calls. Every test also asserts the service never
touches `assets` or `risk_assessments`, matching the "CVE Sync must
never claim a vulnerability exists" system principle.
"""

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from riskGenie.services import cve_sync_service  # noqa: E402
from riskGenie.services import cve_embedding  # noqa: E402
from riskGenie.services.cve_sync_service import (  # noqa: E402
    CVESyncService,
    SyncRunRecordError,
    STATUS_FAILED,
    STATUS_SUCCESS,
)
from riskGenie.services.cve_change_detector import compute_content_hash, normalize_cve  # noqa: E402


# ================================================================
# Fakes
# ================================================================


@pytest.fixture(autouse=True)
def no_real_embedding_service(monkeypatch):
    monkeypatch.setattr(
        cve_sync_service,
        "_default_embedding_updater",
        lambda _supabase, _cve_ids, _max_embeddings: {
            "success_count": 0,
            "error_count": 0,
        },
    )

class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.op = "select"
        self.filters = []
        self.order_field = None
        self.order_desc = False
        self.limit_value = None
        self.range_start = None
        self.range_end = None
        self.payload = None
        self.count_mode = None

    def select(self, _fields, count=None):
        self.op = "select"
        self.count_mode = count
        return self

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, payload, on_conflict=None):
        self.op = "upsert"
        self.payload = payload
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.order_field = field
        self.order_desc = desc
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def range(self, start, end):
        self.range_start = start
        self.range_end = end
        return self

    def _matches(self, row):
        return all(row.get(field) == value for field, value in self.filters)

    def execute(self):
        self.client.calls.append((self.table_name, self.op))

        store = self.client.tables.setdefault(self.table_name, {})

        if self.table_name in self.client.should_fail_tables:
            raise Exception(f"simulated failure for table {self.table_name}")
        if (self.table_name, self.op) in self.client.should_fail_operations:
            raise Exception(
                f"simulated {self.op} failure for table {self.table_name}"
            )

        if self.op == "insert":
            new_id = self.client.next_id
            self.client.next_id += 1
            row = dict(self.payload)
            row["id"] = new_id
            store[new_id] = row
            return FakeResult(data=[row])

        if self.op == "update":
            updated = []
            for row_id, row in store.items():
                if self._matches({"id": row_id}):
                    row.update(self.payload)
                    updated.append(dict(row))
            return FakeResult(data=updated)

        if self.op == "delete":
            deleted = []
            for row_id, row in list(store.items()):
                if self._matches(row):
                    deleted.append(dict(row))
                    del store[row_id]
            return FakeResult(data=deleted)

        if self.op == "upsert":
            key_field = "cve_id"
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            saved = []
            for payload in payloads:
                row = dict(payload)
                matched = False
                for existing_row in store.values():
                    if existing_row.get(key_field) == row.get(key_field):
                        existing_row.update(row)
                        saved.append(dict(existing_row))
                        matched = True
                        break
                if not matched:
                    new_id = self.client.next_id
                    self.client.next_id += 1
                    row["id"] = new_id
                    store[new_id] = row
                    saved.append(dict(row))
            return FakeResult(data=saved)

        # select
        rows = [dict(row) for row in store.values() if self._matches(row)]

        if self.order_field is not None:
            rows.sort(
                key=lambda r: r.get(self.order_field) or "",
                reverse=self.order_desc,
            )

        if self.range_start is not None:
            rows = rows[self.range_start:self.range_end + 1]

        if self.limit_value is not None:
            rows = rows[:self.limit_value]

        count = len(store) if self.count_mode == "exact" else None
        return FakeResult(data=rows, count=count)


class FakeSupabase:
    def __init__(self, cve_documents=None, cve_sync_runs=None):
        self.tables = {
            "cve_documents": {
                i: dict(row) for i, row in enumerate(cve_documents or [])
            },
            "cve_sync_runs": {
                i: dict(row) for i, row in enumerate(cve_sync_runs or [])
            },
            "cve_embeddings": {},
        }
        self.next_id = 10_000
        self.calls = []
        self.should_fail_tables = set()
        self.should_fail_operations = set()

    def table(self, name):
        return FakeQuery(self, name)

    def document_rows(self):
        return list(self.tables["cve_documents"].values())

    def document_by_cve_id(self, cve_id):
        for row in self.tables["cve_documents"].values():
            if row.get("cve_id") == cve_id:
                return row
        return None

    def sync_run_rows(self):
        return list(self.tables["cve_sync_runs"].values())


class FakeNVDClient:
    def __init__(self, cves):
        self._cves = list(cves)
        self.calls = []

    def iter_cves(self, last_mod_start_date=None, last_mod_end_date=None, max_pages=None):
        self.calls.append({
            "last_mod_start_date": last_mod_start_date,
            "last_mod_end_date": last_mod_end_date,
            "max_pages": max_pages,
        })
        for cve in self._cves:
            yield cve


class FakeEmbeddingModels:
    def __init__(self):
        self.calls = []
        self.fail = False

    def embed_content(self, **kwargs):
        self.calls.append(kwargs["contents"])
        if self.fail:
            raise RuntimeError("simulated Gemini embedding failure")
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[0.1] * cve_embedding.EMBEDDING_DIMENSION)
            ]
        )


class FakeEmbeddingClient:
    def __init__(self):
        self.models = FakeEmbeddingModels()


def raw_cve(cve_id, description="desc", cvss_score=9.8, severity="CRITICAL", cwe="CWE-79",
            last_modified="2024-01-01T00:00:00.000", published="2023-12-01T00:00:00.000",
            vuln_status="Analyzed"):
    return {
        "id": cve_id,
        "lastModified": last_modified,
        "published": published,
        "vulnStatus": vuln_status,
        "descriptions": [{"lang": "en", "value": description}],
        "metrics": {
            "cvssMetricV31": [
                {"cvssData": {"baseScore": cvss_score, "baseSeverity": severity}}
            ]
        },
        "weaknesses": [{"description": [{"lang": "en", "value": cwe}]}],
        "references": [],
    }


def document_row(cve_id, content_hash, **overrides):
    row = {
        "cve_id": cve_id,
        "description": "existing",
        "cvss_score": 1.0,
        "severity": "LOW",
        "cwe": None,
        "reference_urls": [],
        "content_hash": content_hash,
        "source_modified_at": "2023-01-01T00:00:00Z",
        "published_at": "2023-01-01T00:00:00Z",
        "synced_at": "2023-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def fixed_now():
    from datetime import datetime, timezone
    return lambda: datetime(2026, 9, 21, 12, 0, 0, tzinfo=timezone.utc)


# ================================================================
# New / updated / unchanged / backfilled classification
# ================================================================

def test_run_inserts_new_cve():
    supabase = FakeSupabase()
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    result = service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.status == STATUS_SUCCESS
    assert result.inserted_count == 1
    assert result.updated_count == 0
    assert result.unchanged_count == 0
    assert result.embedding_updated_count == 0

    stored = supabase.document_by_cve_id("CVE-2024-0001")
    assert stored is not None
    expected_hash = compute_content_hash(normalize_cve(raw_cve("CVE-2024-0001")))
    assert stored["content_hash"] == expected_hash
    assert stored["source_modified_at"] == "2024-01-01T00:00:00.000"
    assert stored["published_at"] == "2023-12-01T00:00:00.000"


def test_run_skips_unchanged_cve():
    cve = raw_cve("CVE-2024-0001")
    existing_hash = compute_content_hash(normalize_cve(cve))
    supabase = FakeSupabase(cve_documents=[document_row("CVE-2024-0001", existing_hash)])
    nvd = FakeNVDClient([cve])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    result = service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.unchanged_count == 1
    assert result.inserted_count == 0
    assert result.updated_count == 0
    # untouched — no upsert call for this table beyond the initial select.
    assert ("cve_documents", "upsert") not in supabase.calls


def test_run_updates_cve_with_changed_description():
    old_cve = raw_cve("CVE-2024-0001", description="old")
    new_cve = raw_cve("CVE-2024-0001", description="brand new content")
    stale_hash = compute_content_hash(normalize_cve(old_cve))

    supabase = FakeSupabase(cve_documents=[document_row("CVE-2024-0001", stale_hash)])
    nvd = FakeNVDClient([new_cve])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    result = service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.updated_count == 1
    assert result.inserted_count == 0
    assert result.embedding_updated_count == 0

    stored = supabase.document_by_cve_id("CVE-2024-0001")
    assert stored["description"] == "brand new content"
    assert stored["content_hash"] != stale_hash


def test_run_treats_legacy_null_content_hash_as_backfill_not_new():
    cve = raw_cve("CVE-2024-0001")
    supabase = FakeSupabase(
        cve_documents=[document_row("CVE-2024-0001", content_hash=None)]
    )
    nvd = FakeNVDClient([cve])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    result = service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.inserted_count == 0
    assert result.updated_count == 1

    stored = supabase.document_by_cve_id("CVE-2024-0001")
    assert stored["content_hash"] is not None


def test_rejected_cve_is_removed_from_documents_and_embedding():
    active = raw_cve("CVE-2024-0001")
    active_hash = compute_content_hash(normalize_cve(active))
    supabase = FakeSupabase(
        cve_documents=[document_row("CVE-2024-0001", active_hash)]
    )
    supabase.tables["cve_embeddings"][1] = {
        "cve_id": "CVE-2024-0001",
        "content": "previously searchable",
        "embedding": [0.1] * cve_embedding.EMBEDDING_DIMENSION,
        "content_hash": active_hash,
    }
    rejected = raw_cve("CVE-2024-0001", vuln_status="Rejected")
    service = CVESyncService(
        FakeNVDClient([rejected]),
        lambda: supabase,
        now=fixed_now(),
    )

    result = service.run(
        last_mod_start_date="2024-01-01T00:00:00Z",
        last_mod_end_date="2024-01-02T00:00:00Z",
    )

    assert result.status == STATUS_SUCCESS
    assert supabase.document_by_cve_id("CVE-2024-0001") is None
    assert supabase.tables["cve_embeddings"] == {}


def test_nvd_sync_flows_through_hash_aware_embedding_and_retries(
    monkeypatch,
):
    monkeypatch.setattr(cve_embedding, "load_dotenv", lambda *_a, **_kw: False)
    monkeypatch.setattr(cve_embedding.time, "sleep", lambda *_a: None)

    supabase = FakeSupabase()
    gemini = FakeEmbeddingClient()

    def update_embeddings(db, cve_ids, max_embeddings):
        return cve_embedding.main(
            supabase=db,
            client=gemini,
            cve_ids=cve_ids,
            max_embeddings=max_embeddings,
        )

    def run_with(cve):
        return CVESyncService(
            FakeNVDClient([cve]),
            lambda: supabase,
            now=fixed_now(),
            embedding_updater=update_embeddings,
        ).run(
            last_mod_start_date="2024-01-01T00:00:00Z",
            last_mod_end_date="2024-01-02T00:00:00Z",
        )

    original = raw_cve("CVE-2024-0001", description="original")
    first = run_with(original)
    stored = next(iter(supabase.tables["cve_embeddings"].values()))
    original_hash = stored["content_hash"]

    assert first.status == STATUS_SUCCESS
    assert first.embedding_updated_count == 1
    assert len(gemini.models.calls) == 1

    unchanged = run_with(original)
    assert unchanged.unchanged_count == 1
    assert unchanged.embedding_updated_count == 0
    assert len(gemini.models.calls) == 1

    changed = raw_cve("CVE-2024-0001", description="changed")
    gemini.models.fail = True
    failed = run_with(changed)
    preserved = next(iter(supabase.tables["cve_embeddings"].values()))

    assert failed.status == STATUS_FAILED
    assert failed.error_count == 1
    assert preserved["content_hash"] == original_hash

    gemini.models.fail = False
    retried = run_with(changed)
    refreshed = next(iter(supabase.tables["cve_embeddings"].values()))

    assert retried.status == STATUS_SUCCESS
    assert retried.embedding_updated_count == 1
    assert refreshed["content_hash"] != original_hash
    touched_tables = {table for table, _operation in supabase.calls}
    assert touched_tables <= {
        "cve_documents",
        "cve_embeddings",
        "cve_sync_runs",
    }
    assert "assets" not in touched_tables
    assert "risk_assessments" not in touched_tables


# ================================================================
# Window resolution / checkpointing
# ================================================================

def test_first_run_requires_an_explicit_query_window():
    supabase = FakeSupabase()
    nvd = FakeNVDClient([])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    with pytest.raises(ValueError, match="explicit"):
        service.run(last_mod_end_date="2026-09-21T12:00:00Z")

    assert nvd.calls == []


def test_run_resumes_from_last_successful_query_end():
    supabase = FakeSupabase(
        cve_sync_runs=[
            {"status": STATUS_SUCCESS, "query_end_date": "2026-09-10T00:00:00Z"},
            {"status": STATUS_FAILED, "query_end_date": "2026-09-20T00:00:00Z"},
        ]
    )
    nvd = FakeNVDClient([])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    service.run(last_mod_end_date="2026-09-21T12:00:00Z")

    call = nvd.calls[0]
    # Resumes from the last *successful* run, ignoring the later failed one.
    assert call["last_mod_start_date"] == "2026-09-10T00:00:00Z"


def test_run_honors_explicit_window_override():
    supabase = FakeSupabase(
        cve_sync_runs=[{"status": STATUS_SUCCESS, "query_end_date": "2026-09-10T00:00:00Z"}]
    )
    nvd = FakeNVDClient([])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    service.run(
        last_mod_start_date="2020-01-01T00:00:00Z",
        last_mod_end_date="2020-02-01T00:00:00Z",
    )

    call = nvd.calls[0]
    assert call["last_mod_start_date"] == "2020-01-01T00:00:00Z"
    assert call["last_mod_end_date"] == "2020-02-01T00:00:00Z"


# ================================================================
# Failure containment
# ================================================================

def test_run_continues_after_a_single_cve_upsert_failure(monkeypatch):
    supabase = FakeSupabase()
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001"), raw_cve("CVE-2024-0002")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    original_process_one = service._process_one
    calls = {"count": 0}

    def flaky_process_one(supabase_arg, raw, hashes, ids, result):
        calls["count"] += 1
        if calls["count"] == 1:
            result.error_count += 1
            result.errors.append("CVE-2024-0001: simulated failure")
            return
        return original_process_one(supabase_arg, raw, hashes, ids, result)

    monkeypatch.setattr(service, "_process_one", flaky_process_one)

    result = service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.status == STATUS_FAILED
    assert result.error_count == 1
    assert result.inserted_count == 1
    assert supabase.document_by_cve_id("CVE-2024-0002") is not None

    rows = supabase.sync_run_rows()
    assert rows[-1]["status"] == STATUS_FAILED
    assert rows[-1]["query_end_date"] == "2024-01-02T00:00:00Z"


def test_page_failure_marks_run_failed_and_does_not_advance_checkpoint():
    class FailingPageClient(FakeNVDClient):
        def iter_cves(self, **kwargs):
            self.calls.append(kwargs)
            yield raw_cve("CVE-2024-0001")
            raise RuntimeError("simulated later-page failure")

    supabase = FakeSupabase(
        cve_sync_runs=[
            {
                "status": STATUS_SUCCESS,
                "query_end_date": "2024-01-01T00:00:00Z",
            }
        ]
    )
    service = CVESyncService(
        FailingPageClient([]),
        lambda: supabase,
        now=fixed_now(),
    )

    result = service.run(last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.status == STATUS_FAILED
    assert result.errors == ["simulated later-page failure"]
    successful = [
        row for row in supabase.sync_run_rows()
        if row.get("status") == STATUS_SUCCESS
    ]
    assert max(row["query_end_date"] for row in successful) == (
        "2024-01-01T00:00:00Z"
    )


def test_page_limit_marks_run_failed_and_does_not_advance_checkpoint():
    class LimitedNVDClient(FakeNVDClient):
        last_iteration_complete = False

    supabase = FakeSupabase(
        cve_sync_runs=[
            {
                "status": STATUS_SUCCESS,
                "query_end_date": "2024-01-01T00:00:00Z",
            }
        ]
    )
    service = CVESyncService(
        LimitedNVDClient([raw_cve("CVE-2024-0001")]),
        lambda: supabase,
        now=fixed_now(),
    )

    result = service.run(last_mod_end_date="2024-01-02T00:00:00Z", max_pages=1)

    assert result.status == STATUS_FAILED
    assert result.complete is False
    assert supabase.sync_run_rows()[-1]["status"] == STATUS_FAILED


def test_embedding_limit_marks_run_failed_and_passes_bound_to_updater():
    observed = {}

    def limited_updater(_db, cve_ids, max_embeddings):
        observed["cve_ids"] = cve_ids
        observed["max_embeddings"] = max_embeddings
        return {
            "success_count": 5,
            "error_count": 0,
            "complete": False,
        }

    cves = [raw_cve(f"CVE-2024-{index:04d}") for index in range(20)]
    supabase = FakeSupabase()
    service = CVESyncService(
        FakeNVDClient(cves),
        lambda: supabase,
        now=fixed_now(),
        embedding_updater=limited_updater,
    )

    result = service.run(
        last_mod_start_date="2024-01-01T00:00:00Z",
        last_mod_end_date="2024-01-02T00:00:00Z",
        max_embeddings=5,
    )

    assert observed["max_embeddings"] == 5
    assert len(observed["cve_ids"]) == 20
    assert result.embedding_updated_count == 5
    assert result.status == STATUS_FAILED
    assert result.complete is False


def test_run_does_not_lose_existing_row_when_its_own_upsert_fails():
    old_cve = raw_cve("CVE-2024-0001", description="old")
    new_cve = raw_cve("CVE-2024-0001", description="new but will fail to save")
    stale_hash = compute_content_hash(normalize_cve(old_cve))

    supabase = FakeSupabase(cve_documents=[document_row("CVE-2024-0001", stale_hash)])
    supabase.should_fail_tables.add("cve_documents")
    nvd = FakeNVDClient([new_cve])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    # cve_documents itself is unreadable/unwritable in this scenario, so
    # the whole run fails — but must not raise out of run(), and must not
    # have corrupted anything (nothing to corrupt since every write on
    # this fake table raises before mutating state).
    result = service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    assert result.status == STATUS_FAILED
    assert result.errors


def test_run_fails_explicitly_if_sync_run_record_cannot_be_created():
    supabase = FakeSupabase()
    supabase.should_fail_tables.add("cve_sync_runs")
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    with pytest.raises(SyncRunRecordError, match="Could not create"):
        service.run(
            last_mod_start_date="2024-01-01T00:00:00Z",
            last_mod_end_date="2024-01-02T00:00:00Z",
        )

    assert supabase.document_by_cve_id("CVE-2024-0001") is None


def test_run_fails_explicitly_if_sync_run_record_cannot_be_finalized():
    supabase = FakeSupabase()
    supabase.should_fail_operations.add(("cve_sync_runs", "update"))
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    with pytest.raises(SyncRunRecordError, match="Could not finalize"):
        service.run(
            last_mod_start_date="2024-01-01T00:00:00Z",
            last_mod_end_date="2024-01-02T00:00:00Z",
        )


# ================================================================
# Run-history bookkeeping
# ================================================================

def test_run_writes_a_sync_runs_record_with_confirmed_fields():
    supabase = FakeSupabase()
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    rows = supabase.sync_run_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_SUCCESS
    assert rows[0]["started_at"] is not None
    assert rows[0]["completed_at"] is not None
    assert rows[0]["inserted_count"] == 1
    assert rows[0]["embedding_updated_count"] == 0
    assert rows[0]["error_message"] is None
    assert "new_count" not in rows[0]
    assert "error_log" not in rows[0]


def test_run_marks_sync_runs_record_failed_on_exception():
    supabase = FakeSupabase()
    supabase.should_fail_tables.add("cve_documents")
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    # cve_sync_runs itself is not in should_fail_tables here, so the
    # bookkeeping write should still have gone through with status=failed.
    rows = supabase.sync_run_rows()
    assert len(rows) == 1
    assert rows[0]["status"] == STATUS_FAILED


# ================================================================
# System principle: never touches assets / risk_assessments
# ================================================================

def test_run_never_touches_assets_or_risk_assessments_tables():
    supabase = FakeSupabase()
    nvd = FakeNVDClient([raw_cve("CVE-2024-0001")])
    service = CVESyncService(nvd, lambda: supabase, now=fixed_now())

    service.run(last_mod_start_date="2024-01-01T00:00:00Z", last_mod_end_date="2024-01-02T00:00:00Z")

    touched_tables = {table for table, _op in supabase.calls}
    assert touched_tables <= {"cve_documents", "cve_sync_runs"}
    assert "assets" not in touched_tables
    assert "risk_assessments" not in touched_tables
