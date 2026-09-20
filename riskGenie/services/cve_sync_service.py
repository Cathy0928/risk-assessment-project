"""
CVE synchronization service for RiskGenie.

Flow:

    Last successful sync
            ↓
        NVD API
            ↓
      normalize_cve()
            ↓
       compare hash
        ↓       ↓
    unchanged   new/changed
                  ↓
            cve_documents
                  ↓
          cve_embedding.py
                  ↓
            cve_embeddings
                  ↓
            cve_sync_runs

Important scope:
- Maintains public vulnerability intelligence only.
- Maintains CVE RAG documents and embeddings.
- Does NOT determine whether an asset is vulnerable.
- Does NOT modify asset risk scores.
- Does NOT run a vulnerability scanner.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from riskGenie.services.nvd_client import fetch_cves_since
from riskGenie.services.import_cve import normalize_cve
from riskGenie.services.cve_embedding import refresh_embeddings
from riskGenie.services.supabase_client import get_supabase_admin_client


load_dotenv()


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

DB_BATCH_SIZE = 100

# When there is no previous sync history but existing
# cve_documents exist, use the newest source_modified_at
# as the synchronization starting point.

# If the database is completely empty, perform an initial
# lookback instead of requesting an unlimited NVD date range.
INITIAL_SYNC_DAYS = int(
    os.getenv("CVE_INITIAL_SYNC_DAYS", "120")
)

# Small overlap prevents boundary misses.
SYNC_OVERLAP_MINUTES = int(
    os.getenv("CVE_SYNC_OVERLAP_MINUTES", "2")
)


supabase = get_supabase_admin_client()


# ---------------------------------------------------------
# Time helpers
# ---------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------
# Sync history
# ---------------------------------------------------------

def _get_last_successful_sync() -> Optional[datetime]:
    """
    Return completed_at of the most recent successful sync.
    """

    response = (
        supabase
        .table("cve_sync_runs")
        .select("completed_at")
        .eq("status", "success")
        .order("completed_at", desc=True)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return None

    value = rows[0].get("completed_at")

    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def _get_latest_existing_cve_modified_at() -> Optional[datetime]:
    """
    Migration-friendly fallback.

    If CVE data already exists from the old manual import process
    but cve_sync_runs has no successful record yet, continue from
    the newest source_modified_at instead of downloading everything.
    """

    response = (
        supabase
        .table("cve_documents")
        .select("source_modified_at")
        .not_.is_("source_modified_at", "null")
        .order("source_modified_at", desc=True)
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return None

    value = rows[0].get("source_modified_at")

    if not value:
        return None

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def _create_sync_run() -> Optional[int]:
    """
    Create a running sync history record.
    """

    response = (
        supabase
        .table("cve_sync_runs")
        .insert({
            "started_at": _to_iso(_utc_now()),
            "status": "running",
        })
        .execute()
    )

    rows = response.data or []

    if not rows:
        return None

    return rows[0].get("id")


def _finish_sync_run(
    run_id: Optional[int],
    status: str,
    stats: Dict[str, int],
    error_message: Optional[str] = None,
) -> None:
    """
    Update sync history after completion.
    """

    if run_id is None:
        return

    payload = {
        "completed_at": _to_iso(_utc_now()),
        "status": status,
        "fetched_count": stats.get("fetched", 0),
        "inserted_count": stats.get("inserted", 0),
        "updated_count": stats.get("updated", 0),
        "unchanged_count": stats.get("unchanged", 0),
        "embedding_updated_count": stats.get(
            "embedding_updated",
            0,
        ),
        "error_count": stats.get("error", 0),
    }

    if error_message:
        payload["error_message"] = error_message[:5000]

    (
        supabase
        .table("cve_sync_runs")
        .update(payload)
        .eq("id", run_id)
        .execute()
    )


# ---------------------------------------------------------
# Existing CVE data
# ---------------------------------------------------------

def _get_existing_documents(
    cve_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Load existing CVE document hashes.

    Query in chunks so a very large CVE list does not become
    one unnecessarily large request.
    """

    result: Dict[str, Dict[str, Any]] = {}

    unique_ids = list(dict.fromkeys(
        cve_id
        for cve_id in cve_ids
        if cve_id
    ))

    for start in range(
        0,
        len(unique_ids),
        DB_BATCH_SIZE,
    ):
        chunk = unique_ids[
            start:start + DB_BATCH_SIZE
        ]

        response = (
            supabase
            .table("cve_documents")
            .select(
                "cve_id,content_hash"
            )
            .in_("cve_id", chunk)
            .execute()
        )

        for row in response.data or []:
            result[row["cve_id"]] = row

    return result


# ---------------------------------------------------------
# CVE database writing
# ---------------------------------------------------------

def _build_document_row(
    cve: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Convert normalized CVE into cve_documents row.
    """

    row = {
        "cve_id": cve["cve_id"],
        "description": cve.get("description"),
        "cvss_score": cve.get("cvss_score"),
        "severity": cve.get("severity"),
        "cwe": cve.get("cwe"),
        "reference_urls": cve.get("reference_urls", []),
        "content_hash": cve.get("content_hash"),
        "source_modified_at": cve.get(
            "source_modified_at"
        ),
        "published_at": cve.get(
            "published_at"
        ),
        "synced_at": _to_iso(_utc_now()),
    }

    return row


def _save_documents(
    rows: List[Dict[str, Any]],
) -> Tuple[int, int, List[str]]:
    """
    Save CVE documents.

    Returns:
        inserted_count,
        updated_count,
        failed_cve_ids
    """

    if not rows:
        return 0, 0, []

    inserted_count = 0
    updated_count = 0
    failed_ids: List[str] = []

    # We already know which records are new/changed before this
    # function is called, so split them into DB batches.
    for start in range(
        0,
        len(rows),
        DB_BATCH_SIZE,
    ):
        batch = rows[
            start:start + DB_BATCH_SIZE
        ]

        try:
            (
                supabase
                .table("cve_documents")
                .upsert(
                    batch,
                    on_conflict="cve_id",
                )
                .execute()
            )

            # The caller already classified these rows,
            # therefore count is calculated outside.
            continue

        except Exception as exc:
            print(
                f"[CVE Sync] Batch upsert failed: {exc}"
            )

            # Fallback to individual records.
            for row in batch:
                try:
                    (
                        supabase
                        .table("cve_documents")
                        .upsert(
                            row,
                            on_conflict="cve_id",
                        )
                        .execute()
                    )

                except Exception as row_exc:
                    cve_id = row.get(
                        "cve_id",
                        "UNKNOWN",
                    )

                    print(
                        f"[CVE Sync] Failed to save "
                        f"{cve_id}: {row_exc}"
                    )

                    failed_ids.append(cve_id)

    return (
        inserted_count,
        updated_count,
        failed_ids,
    )


# ---------------------------------------------------------
# Normalize and compare
# ---------------------------------------------------------

def _prepare_cves(
    vulnerabilities: List[Dict[str, Any]],
    existing: Dict[str, Dict[str, Any]],
    stats: Dict[str, int],
) -> Tuple[
    List[Dict[str, Any]],
    List[str],
]:
    """
    Normalize raw NVD records and determine which CVEs
    need database / embedding updates.
    """

    rows_to_save: List[Dict[str, Any]] = []
    changed_ids: List[str] = []

    seen_ids = set()

    for item in vulnerabilities:

        try:
            cve = normalize_cve(item)

            cve_id = cve.get("cve_id")

            if not cve_id:
                raise ValueError(
                    "Normalized CVE has no cve_id."
                )

            # NVD pagination / overlap can potentially give us
            # the same CVE more than once.
            if cve_id in seen_ids:
                continue

            seen_ids.add(cve_id)

            existing_row = existing.get(cve_id)

            if existing_row is None:
                rows_to_save.append(
                    _build_document_row(cve)
                )

                changed_ids.append(cve_id)

                stats["inserted"] += 1

                continue

            old_hash = existing_row.get(
                "content_hash"
            )

            new_hash = cve.get(
                "content_hash"
            )

            if old_hash == new_hash:
                stats["unchanged"] += 1
                continue

            rows_to_save.append(
                _build_document_row(cve)
            )

            changed_ids.append(cve_id)

            stats["updated"] += 1

        except Exception as exc:
            stats["error"] += 1

            print(
                f"[CVE Sync] Failed to normalize CVE: "
                f"{exc}"
            )

    return rows_to_save, changed_ids


# ---------------------------------------------------------
# Main synchronization
# ---------------------------------------------------------

def run_sync() -> Dict[str, Any]:
    """
    Execute one complete CVE synchronization.

    Returns a summary dictionary.
    """

    stats = {
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "embedding_updated": 0,
        "error": 0,
    }

    run_id = None

    try:
        # -------------------------------------------------
        # 1. Start sync history
        # -------------------------------------------------

        run_id = _create_sync_run()

        print(
            "\n========================================"
        )
        print(
            " RiskGenie CVE Synchronization"
        )
        print(
            "========================================"
        )

        # -------------------------------------------------
        # 2. Determine synchronization starting point
        # -------------------------------------------------

        last_success = _get_last_successful_sync()

        if last_success:
            modified_start = last_success

            print(
                "[CVE Sync] "
                f"Last successful sync: "
                f"{_to_iso(last_success)}"
            )

        else:
            # Existing manually imported CVE data?
            existing_modified = (
                _get_latest_existing_cve_modified_at()
            )

            if existing_modified:
                modified_start = existing_modified

                print(
                    "[CVE Sync] No previous sync history."
                )

                print(
                    "[CVE Sync] Using latest existing "
                    "CVE modified time: "
                    f"{_to_iso(existing_modified)}"
                )

            else:
                # Completely empty database.
                modified_start = (
                    _utc_now()
                    - timedelta(
                        days=INITIAL_SYNC_DAYS
                    )
                )

                print(
                    "[CVE Sync] No existing CVE data."
                )

                print(
                    "[CVE Sync] Initial lookback: "
                    f"{INITIAL_SYNC_DAYS} days."
                )

        # -------------------------------------------------
        # 3. Fetch changed CVEs from NVD
        # -------------------------------------------------

        print(
            "[CVE Sync] Fetching NVD CVEs..."
        )

        vulnerabilities = fetch_cves_since(
            modified_start=modified_start,
            overlap_minutes=SYNC_OVERLAP_MINUTES,
        )

        stats["fetched"] = len(
            vulnerabilities
        )

        print(
            "[CVE Sync] NVD returned "
            f"{len(vulnerabilities)} records."
        )

        # NVD returned nothing.
        if not vulnerabilities:
            _finish_sync_run(
                run_id=run_id,
                status="success",
                stats=stats,
            )

            print(
                "[CVE Sync] Nothing to update."
            )

            return {
                "status": "success",
                **stats,
            }

        # -------------------------------------------------
        # 4. Collect CVE IDs
        # -------------------------------------------------

        raw_ids: List[str] = []

        for item in vulnerabilities:
            try:
                cve_data = item.get(
                    "cve",
                    {}
                )

                cve_id = cve_data.get(
                    "id"
                )

                if cve_id:
                    raw_ids.append(cve_id)

            except Exception:
                stats["error"] += 1

        raw_ids = list(
            dict.fromkeys(raw_ids)
        )

        # -------------------------------------------------
        # 5. Load current DB hashes
        # -------------------------------------------------

        print(
            "[CVE Sync] Loading existing "
            "CVE hashes..."
        )

        existing = _get_existing_documents(
            raw_ids
        )

        print(
            "[CVE Sync] Existing records found: "
            f"{len(existing)}"
        )

        # -------------------------------------------------
        # 6. Normalize + compare hash
        # -------------------------------------------------

        rows_to_save, changed_ids = (
            _prepare_cves(
                vulnerabilities,
                existing,
                stats,
            )
        )

        print(
            "[CVE Sync] New: "
            f"{stats['inserted']}"
        )

        print(
            "[CVE Sync] Changed: "
            f"{stats['updated']}"
        )

        print(
            "[CVE Sync] Unchanged: "
            f"{stats['unchanged']}"
        )

        # -------------------------------------------------
        # 7. Save changed/new CVE documents
        # -------------------------------------------------

        failed_save_ids: List[str] = []

        if rows_to_save:
            print(
                "[CVE Sync] Saving "
                f"{len(rows_to_save)} CVE documents..."
            )

            (
                _,
                _,
                failed_save_ids,
            ) = _save_documents(
                rows_to_save
            )

            if failed_save_ids:
                stats["error"] += len(
                    failed_save_ids
                )

                failed_set = set(
                    failed_save_ids
                )

                changed_ids = [
                    cve_id
                    for cve_id in changed_ids
                    if cve_id not in failed_set
                ]

        # -------------------------------------------------
        # 8. Refresh only changed/new embeddings
        # -------------------------------------------------

        if changed_ids:
            print(
                "[CVE Sync] Refreshing embeddings "
                f"for {len(changed_ids)} CVEs..."
            )

            embedding_result = (
                refresh_embeddings(
                    changed_ids
                )
            )

            stats[
                "embedding_updated"
            ] = embedding_result.get(
                "success",
                0,
            )

            embedding_errors = (
                embedding_result.get(
                    "error",
                    0,
                )
            )

            stats["error"] += embedding_errors

            print(
                "[CVE Sync] Embedding result: "
                f"{embedding_result}"
            )

        else:
            print(
                "[CVE Sync] No embedding update required."
            )

        # -------------------------------------------------
        # 9. Determine final status
        # -------------------------------------------------

        if stats["error"] > 0:
            final_status = "partial"
        else:
            final_status = "success"

        _finish_sync_run(
            run_id=run_id,
            status=final_status,
            stats=stats,
        )

        print(
            "\n========================================"
        )
        print(
            " CVE Synchronization Finished"
        )
        print(
            "========================================"
        )

        print(
            f"Status: {final_status}"
        )

        print(
            f"Fetched: {stats['fetched']}"
        )

        print(
            f"Inserted: {stats['inserted']}"
        )

        print(
            f"Updated: {stats['updated']}"
        )

        print(
            f"Unchanged: {stats['unchanged']}"
        )

        print(
            f"Embeddings updated: "
            f"{stats['embedding_updated']}"
        )

        print(
            f"Errors: {stats['error']}"
        )

        return {
            "status": final_status,
            **stats,
        }

    except Exception as exc:
        # -------------------------------------------------
        # NVD failure / unexpected failure
        #
        # IMPORTANT:
        # Existing CVE data is NOT deleted.
        # -------------------------------------------------

        error_message = str(exc)

        stats["error"] += 1

        print(
            "\n[CVE Sync] FATAL ERROR:"
        )

        print(
            error_message
        )

        _finish_sync_run(
            run_id=run_id,
            status="failed",
            stats=stats,
            error_message=error_message,
        )

        return {
            "status": "failed",
            **stats,
            "error_message": error_message,
        }


def main() -> None:
    """
    Command-line entry point.
    """

    result = run_sync()

    # Make CLI failure visible to schedulers / Task Scheduler.
    if result.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()