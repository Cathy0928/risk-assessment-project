# -*- coding: utf-8 -*-
"""
CVE Sync Service — orchestrates NVDClient + cve_change_detector to keep
`cve_documents` up to date from the official NVD API, and records each
run in `cve_sync_runs`.

Scope / RiskGenie system principles this service must respect:
    - RiskGenie is an ISMS asset inventory / risk assessment tool, not a
      vulnerability scanner. This service only maintains public CVE
      reference data (`cve_documents`) and its own run history
      (`cve_sync_runs`). It never touches `assets` or
      `risk_assessments`, and never claims any asset "has" a
      vulnerability just because a CVE was synced.
    - After document sync, it invokes cve_embedding.py. That updater
      compares public.cve_embeddings.content_hash against cve_documents,
      skips unchanged content, and retries stale/missing vectors.
    - It does not run itself on a schedule and does not call the real
      Gemini API. Callers (a CLI entrypoint, a cron job, a future admin
      action) decide when to call `run()`; `nvd_client` is injected so
      tests never hit the real NVD API.

EMBEDDING TABLE STATUS:
    Confirmed via a read-only pg_class query: the real table is plural
    public.cve_embeddings (OID 34266); singular "cve_embedding" does not
    exist. The existing search_cve RPC already reads "FROM cve_embeddings",
    so it was never broken — an earlier screenshot-based guess had this
    backwards and has been corrected throughout this module and
    cve_embedding.py. The index cve_embeddings_cve_id_unique is known to
    exist on this table; its exact definition is not yet independently
    confirmed.

SCHEMA CONFIRMATION STATUS:
    - cve_documents: cve_id, description, cvss_score, severity, cwe,
      reference_urls (all pre-existing, used by import_cve.py) plus
      content_hash, source_modified_at, synced_at, published_at
      (confirmed present via a read-only Supabase check). Column TYPES
      were not independently re-verified beyond "they exist" — this
      service writes what it believes are reasonable values (ISO-8601
      strings for the three date/time fields) but the exact expected
      type (text vs timestamptz) has not been confirmed.
    - cve_sync_runs count/error columns use the confirmed contract:
      inserted_count, updated_count, unchanged_count,
      embedding_updated_count, error_count, error_message.
      query_start_date/query_end_date remain pending read-only schema
      confirmation and are required for safe durable checkpointing.
      Run-history write failures are fatal and explicitly reported.
"""

import logging
from datetime import datetime, timedelta, timezone

try:
    from .cve_change_detector import compute_content_hash, normalize_cve
    from . import cve_embedding
except ImportError:
    from cve_change_detector import compute_content_hash, normalize_cve
    import cve_embedding


logger = logging.getLogger(__name__)


DOCUMENTS_TABLE = "cve_documents"
SYNC_RUNS_TABLE = "cve_sync_runs"

# Confirmed cve_sync_runs columns.
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# No prior successful run to resume from -> don't default to "everything
# NVD has ever published"; bound the first-ever run to a fixed lookback.
DEFAULT_LOOKBACK = timedelta(days=7)

DOCUMENTS_PAGE_SIZE = 1000


def _default_embedding_updater(supabase, cve_ids, max_embeddings):
    """Run the existing hash-aware embedding updater on this DB client."""
    return cve_embedding.main(
        supabase=supabase,
        cve_ids=cve_ids,
        max_embeddings=max_embeddings,
    )


class SyncRunResult:
    """Plain summary of one sync run. Has no dependency on the exact
    cve_sync_runs schema, so it (and the classification logic that
    fills it in) is fully unit-testable without a real database."""

    def __init__(self, window_start, window_end):
        self.window_start = window_start
        self.window_end = window_end
        self.started_at = None
        self.completed_at = None
        self.status = STATUS_RUNNING
        self.inserted_count = 0
        self.updated_count = 0
        self.unchanged_count = 0
        self.embedding_updated_count = 0
        self.error_count = 0
        self.errors = []
        self.complete = True

    def to_dict(self):
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "inserted_count": self.inserted_count,
            "updated_count": self.updated_count,
            "unchanged_count": self.unchanged_count,
            "embedding_updated_count": self.embedding_updated_count,
            "error_count": self.error_count,
            "errors": list(self.errors),
            "complete": self.complete,
        }


class SyncRunRecordError(RuntimeError):
    """Raised when cve_sync_runs cannot record the sync truthfully."""


class CVESyncService:
    """
    `nvd_client` must expose `.iter_cves(last_mod_start_date=, \
    last_mod_end_date=, max_pages=)` (see nvd_client.NVDClient).

    `get_supabase_client` is a zero-arg callable returning a Supabase
    client, matching the lazy-init pattern already used throughout this
    codebase (riskGenie.services.supabase_client.get_supabase_client) —
    injected rather than imported directly so tests can supply a fake.

    `now` is injectable so tests get deterministic timestamps instead
    of depending on wall-clock time.
    """

    def __init__(
        self,
        nvd_client,
        get_supabase_client,
        now=None,
        documents_table=DOCUMENTS_TABLE,
        sync_runs_table=SYNC_RUNS_TABLE,
        documents_page_size=DOCUMENTS_PAGE_SIZE,
        default_lookback=DEFAULT_LOOKBACK,
        embedding_updater=None,
        execution_guard=None,
    ):
        self._nvd_client = nvd_client
        self._get_supabase_client = get_supabase_client
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._documents_table = documents_table
        self._sync_runs_table = sync_runs_table
        self._documents_page_size = documents_page_size
        self._default_lookback = default_lookback
        self._embedding_updater = (
            embedding_updater or _default_embedding_updater
        )
        self._execution_guard = execution_guard or (lambda: None)

    # ------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------

    def run(
        self,
        last_mod_start_date=None,
        last_mod_end_date=None,
        max_pages=None,
        max_embeddings=None,
    ):
        supabase = self._get_supabase_client()

        window_start, window_end = self._resolve_window(
            supabase,
            last_mod_start_date,
            last_mod_end_date,
        )

        result = SyncRunResult(window_start, window_end)
        result.started_at = self._iso_now()

        run_row_id = self._start_run_record(supabase, result)

        try:
            existing_hash_by_id, existing_ids = self._load_existing_documents(
                supabase
            )

            processed_cve_ids = []
            for raw_cve in self._nvd_client.iter_cves(
                last_mod_start_date=window_start,
                last_mod_end_date=window_end,
                max_pages=max_pages,
            ):
                # Checked before every write so a lease lost mid-run (e.g.
                # stolen after expiry) stops this process before it can
                # write again, instead of only being caught at the end.
                self._execution_guard()
                processed_cve_id = self._process_one(
                    supabase,
                    raw_cve,
                    existing_hash_by_id,
                    existing_ids,
                    result,
                )
                if processed_cve_id:
                    processed_cve_ids.append(processed_cve_id)

            if not getattr(self._nvd_client, "last_iteration_complete", True):
                result.complete = False
                result.errors.append(
                    "NVD page limit reached before the query window completed"
                )

            self._execution_guard()
            embedding_result = self._embedding_updater(
                supabase,
                processed_cve_ids,
                max_embeddings,
            ) or {}
            result.embedding_updated_count = int(
                embedding_result.get("success_count", 0)
            )
            embedding_error_count = int(
                embedding_result.get("error_count", 0)
            )
            if embedding_error_count:
                result.error_count += embedding_error_count
                result.errors.append(
                    f"Embedding update failed for "
                    f"{embedding_error_count} CVE(s)"
                )

            if not embedding_result.get("complete", True):
                result.complete = False
                result.errors.append(
                    "Embedding limit reached before all changed CVEs were processed"
                )

            self._execution_guard()

            result.status = (
                STATUS_SUCCESS
                if result.error_count == 0 and result.complete
                else STATUS_FAILED
            )

        except Exception as exc:
            logger.exception("CVE sync run failed: %s", exc)
            result.status = STATUS_FAILED
            result.errors.append(str(exc))

        finally:
            try:
                self._execution_guard()
            except Exception as exc:
                result.status = STATUS_FAILED
                result.complete = False
                result.errors.append(str(exc))
            result.completed_at = self._iso_now()
            self._finish_run_record(supabase, run_row_id, result)

        return result

    # ------------------------------------------------------------
    # Window resolution / checkpointing
    # ------------------------------------------------------------

    def _resolve_window(self, supabase, last_mod_start_date, last_mod_end_date):
        if last_mod_start_date:
            if not last_mod_end_date:
                raise ValueError(
                    "last_mod_end_date is required when an explicit "
                    "last_mod_start_date is provided"
                )
            return last_mod_start_date, last_mod_end_date

        end = last_mod_end_date or self._iso_now()

        checkpoint = self._load_last_successful_query_end(supabase)
        if checkpoint:
            return checkpoint, end

        raise ValueError(
            "No successful checkpoint exists; explicit last_mod_start_date "
            "and last_mod_end_date are required for the first sync"
        )

    def _load_last_successful_query_end(self, supabase):
        """Return the last fully processed NVD query-window end.

        Completion time is deliberately not a checkpoint: the sync may
        finish well after the NVD interval ended. Only successful runs
        (which necessarily have zero item/page errors) may advance it.
        """

        response = (
            supabase
            .table(self._sync_runs_table)
            .select("query_end_date, status")
            .eq("status", STATUS_SUCCESS)
            .order("query_end_date", desc=True)
            .limit(1)
            .execute()
        )

        rows = response.data or []
        if not rows:
            return None

        return rows[0].get("query_end_date")

    # ------------------------------------------------------------
    # cve_documents read/write
    # ------------------------------------------------------------

    def _load_existing_documents(self, supabase):
        """Returns (hash_by_id, id_set).

        A cve_id present in id_set with hash_by_id[cve_id] is None means
        "this row already exists, but content_hash was never backfilled"
        (legacy data written before this column existed by the original
        import_cve.py) — distinct from a cve_id genuinely never seen
        before. Both cases still need their content written/refreshed,
        but they are counted separately (backfilled vs new) so
        cve_sync_runs's counts stay honest.
        """

        hash_by_id = {}
        id_set = set()
        start = 0

        while True:
            end = start + self._documents_page_size - 1

            response = (
                supabase
                .table(self._documents_table)
                .select("cve_id, content_hash")
                .range(start, end)
                .execute()
            )

            rows = response.data or []

            for row in rows:
                cve_id = row.get("cve_id")
                if not cve_id:
                    continue
                id_set.add(cve_id)
                hash_by_id[cve_id] = row.get("content_hash")

            if len(rows) < self._documents_page_size:
                break

            start += self._documents_page_size

        return hash_by_id, id_set

    def _process_one(
        self, supabase, raw_cve, existing_hash_by_id, existing_ids, result
    ):
        normalized = normalize_cve(raw_cve)
        cve_id = normalized.get("cve_id")

        if not cve_id:
            return

        try:
            if str(normalized.get("vuln_status") or "").upper() == "REJECTED":
                self._remove_rejected_cve(supabase, cve_id)
                if cve_id in existing_ids:
                    result.updated_count += 1
                    existing_ids.discard(cve_id)
                    existing_hash_by_id.pop(cve_id, None)
                return None

            new_hash = compute_content_hash(normalized)
            existed_before = cve_id in existing_ids
            existing_hash = existing_hash_by_id.get(cve_id)

            if existed_before and existing_hash == new_hash:
                result.unchanged_count += 1
                return cve_id

            row = self._to_document_row(normalized, new_hash)

            # Upsert only — never delete-then-insert. If this fails, the
            # previous row (if any) is left exactly as it was.
            supabase.table(self._documents_table).upsert(
                row,
                on_conflict="cve_id",
            ).execute()

            existing_ids.add(cve_id)
            existing_hash_by_id[cve_id] = new_hash

            if not existed_before:
                result.inserted_count += 1
            elif existing_hash is None:
                result.updated_count += 1
            else:
                result.updated_count += 1

            return cve_id

        except Exception as exc:
            logger.exception("Failed to sync %s: %s", cve_id, exc)
            result.error_count += 1
            result.errors.append(f"{cve_id}: {exc}")
            # Deliberately not re-raised: one bad CVE must not abort the
            # whole run, and its existing row (if any) stays untouched.
            return None

    def _remove_rejected_cve(self, supabase, cve_id):
        # Remove the vector first so a rejected identifier cannot remain
        # searchable if the following document cleanup needs a retry.
        (
            supabase
            .table(cve_embedding.EMBEDDING_TABLE)
            .delete()
            .eq("cve_id", cve_id)
            .execute()
        )
        (
            supabase
            .table(self._documents_table)
            .delete()
            .eq("cve_id", cve_id)
            .execute()
        )

    def _to_document_row(self, normalized, content_hash):
        return {
            "cve_id": normalized["cve_id"],
            "description": normalized["description"],
            "cvss_score": normalized["cvss_score"],
            "severity": normalized["severity"],
            "cwe": normalized["cwe"],
            "reference_urls": normalized["reference_urls"],
            "content_hash": content_hash,
            "source_modified_at": normalized["last_modified"],
            "published_at": normalized["published"],
            "synced_at": self._iso_now(),
        }

    # ------------------------------------------------------------
    # cve_sync_runs read/write
    #
    # Run history is part of the sync contract. Failures are surfaced
    # explicitly so callers cannot mistake an unrecorded run for success.
    # ------------------------------------------------------------

    def _start_run_record(self, supabase, result):
        try:
            response = (
                supabase
                .table(self._sync_runs_table)
                .insert(self._build_run_record(result))
                .execute()
            )
        except Exception as exc:
            raise SyncRunRecordError(
                "Could not create cve_sync_runs record"
            ) from exc

        rows = response.data or []
        run_row_id = rows[0].get("id") if rows else None
        if run_row_id is None:
            raise SyncRunRecordError(
                "cve_sync_runs insert returned no record id"
            )
        return run_row_id

    def _finish_run_record(self, supabase, run_row_id, result):
        try:
            response = (
                supabase
                .table(self._sync_runs_table)
                .update(self._build_run_record(result))
                .eq("id", run_row_id)
                .execute()
            )
        except Exception as exc:
            raise SyncRunRecordError(
                f"Could not finalize cve_sync_runs record {run_row_id}"
            ) from exc

        if not (response.data or []):
            raise SyncRunRecordError(
                f"cve_sync_runs record {run_row_id} was not finalized"
            )

    def _build_run_record(self, result):
        return {
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "status": result.status,
            "query_start_date": result.window_start,
            "query_end_date": result.window_end,
            "inserted_count": result.inserted_count,
            "updated_count": result.updated_count,
            "unchanged_count": result.unchanged_count,
            "embedding_updated_count": result.embedding_updated_count,
            "error_count": result.error_count,
            "error_message": "\n".join(result.errors) or None,
        }

    # ------------------------------------------------------------
    # Time helpers
    # ------------------------------------------------------------

    def _iso_now(self):
        return self._iso(self._now())

    @staticmethod
    def _iso(dt):
        return dt.isoformat().replace("+00:00", "Z")
