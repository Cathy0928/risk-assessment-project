"""
CVE Embedding service for RiskGenie.

Responsibilities:
- Read CVE documents from Supabase
- Generate Gemini embeddings
- Compare content_hash
- Refresh embeddings only when necessary
- Support targeted refresh_embeddings(cve_ids)
- Retry Gemini / Supabase failures

This module does NOT:
- Fetch data from NVD
- Parse NVD API responses
- Decide whether an asset is vulnerable
- Modify asset risk scores
"""

import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from google import genai

from riskGenie.services.supabase_client import (
    get_supabase_admin_client,
)
from riskGenie.services.import_cve import (
    calculate_content_hash,
)


# ============================================================
# Environment
# ============================================================

load_dotenv()


# ============================================================
# Supabase
# ============================================================

supabase = get_supabase_admin_client()


# ============================================================
# Gemini
# ============================================================

gemini_api_key = os.getenv("GEMINI_API_KEY")

if not gemini_api_key:
    raise RuntimeError(
        "Missing required environment variable: GEMINI_API_KEY"
    )

client = genai.Client(
    api_key=gemini_api_key
)


# ============================================================
# Settings
# ============================================================

PAGE_SIZE = 1000

BATCH_SIZE = 50

MAX_RETRY = 3

GEMINI_SLEEP_TIME = 0.2

EMBEDDING_DIMENSION = 768

EMBEDDING_MODEL = "gemini-embedding-001"


# ============================================================
# Helpers
# ============================================================

def _utc_now():
    """
    Return current UTC timestamp in ISO format.
    """

    return datetime.now(
        timezone.utc
    ).isoformat()


def _build_embedding_content(cve):
    """
    Build the text that will be sent to Gemini.

    This must stay consistent with the content used by
    the CVE RAG system.
    """

    cve_id = cve.get("cve_id")

    description = cve.get(
        "description"
    ) or ""

    cvss_score = cve.get(
        "cvss_score"
    )

    severity = cve.get(
        "severity"
    )

    cwe = cve.get(
        "cwe"
    )

    return f"""
CVE ID:
{cve_id}

Description:
{description}

CVSS:
{cvss_score}

Severity:
{severity}

CWE:
{cwe}
""".strip()


def _calculate_row_hash(cve):
    """
    Calculate content hash for a CVE document.

    The same hash algorithm is used by import_cve.py.
    """

    return calculate_content_hash(
        cve
    )


# ============================================================
# Gemini Embedding
# ============================================================

def _generate_embedding(content, cve_id):
    """
    Generate one Gemini embedding with bounded retry.

    Returns:
        list[float]

    Raises:
        Exception after MAX_RETRY failures.
    """

    last_error = None

    for retry in range(MAX_RETRY):

        try:

            print(
                f"Gemini Embedding: "
                f"{cve_id} "
                f"({retry + 1}/{MAX_RETRY})"
            )

            result = (
                client
                .models
                .embed_content(
                    model=EMBEDDING_MODEL,
                    contents=content,
                    config={
                        "output_dimensionality":
                            EMBEDDING_DIMENSION
                    },
                )
            )

            embeddings = (
                result.embeddings
            )

            if not embeddings:
                raise ValueError(
                    "Gemini returned no embedding."
                )

            embedding = (
                embeddings[0].values
            )

            if len(embedding) != EMBEDDING_DIMENSION:
                raise ValueError(
                    "Embedding dimension error: "
                    f"{len(embedding)} "
                    f"(expected "
                    f"{EMBEDDING_DIMENSION})"
                )

            return embedding

        except Exception as exc:

            last_error = exc

            print(
                f"Embedding Error: "
                f"{cve_id}"
            )

            print(exc)

            if retry < MAX_RETRY - 1:

                wait_time = 2 ** retry

                print(
                    f"{wait_time} 秒後重試..."
                )

                time.sleep(
                    wait_time
                )

    raise RuntimeError(
        f"Gemini embedding failed for "
        f"{cve_id} after "
        f"{MAX_RETRY} attempts."
    ) from last_error


# ============================================================
# Get existing embeddings
# ============================================================

def _get_existing_embeddings(cve_ids=None):
    """
    Get existing CVE embeddings and their content_hash.

    Returns:

        {
            "CVE-2026-1234": "hash...",
            "CVE-2026-5678": "hash..."
        }

    If content_hash is NULL/missing, the value will be None.
    """

    existing = {}

    start = 0

    while True:

        end = (
            start
            + PAGE_SIZE
            - 1
        )

        query = (
            supabase
            .table("cve_embeddings")
            .select(
                "cve_id, content_hash"
            )
            .range(
                start,
                end
            )
        )

        if cve_ids:

            query = query.in_(
                "cve_id",
                cve_ids
            )

        response = query.execute()

        rows = (
            response.data
            or []
        )

        for row in rows:

            cve_id = row.get(
                "cve_id"
            )

            if cve_id:
                existing[cve_id] = (
                    row.get(
                        "content_hash"
                    )
                )

        if len(rows) < PAGE_SIZE:
            break

        start += PAGE_SIZE

        # When using .in_(), the query is already restricted
        # to the requested CVEs. We still paginate safely.
        if cve_ids and len(existing) >= len(cve_ids):
            break

    return existing


# ============================================================
# Get CVE documents
# ============================================================

def _get_cve_documents(cve_ids=None):
    """
    Get CVE documents from cve_documents.

    If cve_ids is supplied, only those CVEs are returned.
    """

    documents = []

    start = 0

    while True:

        end = (
            start
            + PAGE_SIZE
            - 1
        )

        query = (
            supabase
            .table("cve_documents")
            .select(
                """
                cve_id,
                description,
                cvss_score,
                severity,
                cwe,
                content_hash
                """
            )
            .range(
                start,
                end
            )
        )

        if cve_ids:

            query = query.in_(
                "cve_id",
                cve_ids
            )

        response = query.execute()

        rows = (
            response.data
            or []
        )

        documents.extend(
            rows
        )

        if len(rows) < PAGE_SIZE:
            break

        start += PAGE_SIZE

        if cve_ids and len(documents) >= len(cve_ids):
            break

    return documents


# ============================================================
# Save embedding batch
# ============================================================

def _save_embedding_batch(batch):
    """
    Save a batch of embeddings.

    Important:
    The new embedding is generated BEFORE this function is called.

    Therefore, if Gemini fails, the old embedding remains untouched.
    """

    if not batch:
        return 0

    last_error = None

    for retry in range(MAX_RETRY):

        try:

            response = (
                supabase
                .table("cve_embeddings")
                .upsert(
                    batch,
                    on_conflict="cve_id"
                )
                .execute()
            )

            saved_rows = (
                response.data
                or []
            )

            # Supabase normally returns the written rows.
            # If it does not, successful execution is still
            # considered a successful write.
            if saved_rows:
                return len(saved_rows)

            return len(batch)

        except Exception as exc:

            last_error = exc

            print(
                "Supabase Embedding Batch Error:"
            )

            print(exc)

            if retry < MAX_RETRY - 1:

                wait_time = 5 * (
                    retry + 1
                )

                print(
                    f"{wait_time} 秒後重試..."
                )

                time.sleep(
                    wait_time
                )

    raise RuntimeError(
        "Failed to save embedding batch "
        f"after {MAX_RETRY} attempts."
    ) from last_error


# ============================================================
# Refresh Embeddings
# ============================================================

def refresh_embeddings(cve_ids=None):
    """
    Refresh CVE embeddings.

    Args:
        cve_ids:
            List of CVE IDs to refresh.

            Example:
                [
                    "CVE-2026-1234",
                    "CVE-2026-5678"
                ]

            If None:
                process all CVEs in cve_documents.

    Behavior:

        New CVE
            -> Generate embedding

        Existing CVE + same content_hash
            -> Skip

        Existing CVE + different content_hash
            -> Generate new embedding

        Existing CVE + missing embedding
            -> Generate embedding

        Gemini failure
            -> Keep old embedding

        Supabase write failure
            -> Retry, then report error
    """

    # --------------------------------------------------------
    # Normalize input
    # --------------------------------------------------------

    if cve_ids is not None:

        cve_ids = list(
            dict.fromkeys(
                cve_ids
            )
        )

        if not cve_ids:
            print(
                "沒有需要更新的 CVE。"
            )

            return {
                "success": 0,
                "skipped": 0,
                "error": 0,
                "requested": 0,
            }

    # --------------------------------------------------------
    # Get CVE documents
    # --------------------------------------------------------

    print(
        "正在取得 CVE Documents..."
    )

    cves = _get_cve_documents(
        cve_ids
    )

    print(
        "取得 CVE:",
        len(cves)
    )

    if not cves:

        return {
            "success": 0,
            "skipped": 0,
            "error": 0,
            "requested": (
                len(cve_ids)
                if cve_ids
                else 0
            ),
        }

    # --------------------------------------------------------
    # Get existing embeddings
    # --------------------------------------------------------

    print(
        "正在取得現有 Embedding..."
    )

    existing_embeddings = (
        _get_existing_embeddings(
            [
                cve.get("cve_id")
                for cve in cves
                if cve.get("cve_id")
            ]
        )
    )

    print(
        "目前已有 Embedding:",
        len(existing_embeddings)
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    total_success = 0

    total_skip = 0

    total_error = 0

    batch = []

    # --------------------------------------------------------
    # Process CVEs
    # --------------------------------------------------------

    for cve in cves:

        cve_id = cve.get(
            "cve_id"
        )

        if not cve_id:

            total_error += 1

            print(
                "CVE 缺少 cve_id，跳過。"
            )

            continue

        # ----------------------------------------------------
        # Calculate current hash
        # ----------------------------------------------------

        current_hash = (
            cve.get(
                "content_hash"
            )
        )

        # ----------------------------------------------------
        # Backward compatibility:
        # If cve_documents.content_hash is empty,
        # calculate it locally.
        # ----------------------------------------------------

        if not current_hash:

            current_hash = (
                _calculate_row_hash(
                    cve
                )
            )

        existing_hash = (
            existing_embeddings.get(
                cve_id
            )
        )

        # ----------------------------------------------------
        # Content unchanged
        # ----------------------------------------------------

        if (
            existing_hash
            and existing_hash == current_hash
        ):

            total_skip += 1

            print(
                "Skip（內容未變）:",
                cve_id
            )

            continue

        # ----------------------------------------------------
        # Explain why embedding is needed
        # ----------------------------------------------------

        if cve_id not in existing_embeddings:

            print(
                "New Embedding:",
                cve_id
            )

        elif existing_hash is None:

            print(
                "重新建立 Embedding（舊資料沒有 hash）:",
                cve_id
            )

        else:

            print(
                "重新建立 Embedding（內容已變更）:",
                cve_id
            )

        # ----------------------------------------------------
        # Build embedding content
        # ----------------------------------------------------

        content = _build_embedding_content(
            cve
        )

        # ----------------------------------------------------
        # Generate embedding
        # ----------------------------------------------------

        try:

            embedding = _generate_embedding(
                content,
                cve_id
            )

        except Exception as exc:

            total_error += 1

            print(
                "Embedding 失敗:",
                cve_id
            )

            print(exc)

            # IMPORTANT:
            # Do NOT delete the existing embedding.
            continue

        # ----------------------------------------------------
        # Add to batch
        # ----------------------------------------------------

        batch.append(
            {
                "cve_id": cve_id,
                "content": content,
                "embedding": embedding,
                "content_hash": current_hash,
                "updated_at": _utc_now(),
            }
        )

        # ----------------------------------------------------
        # Save when batch is full
        # ----------------------------------------------------

        if len(batch) >= BATCH_SIZE:

            print()
            print(
                "寫入 Embedding Batch:",
                len(batch)
            )

            try:

                saved_count = (
                    _save_embedding_batch(
                        batch
                    )
                )

                total_success += (
                    saved_count
                )

                print(
                    "Batch 寫入成功:",
                    saved_count
                )

            except Exception as exc:

                total_error += (
                    len(batch)
                )

                print(
                    "Batch 寫入失敗:"
                )

                print(exc)

                # IMPORTANT:
                # Do not pretend these embeddings
                # were successfully updated.

            finally:

                batch = []

        time.sleep(
            GEMINI_SLEEP_TIME
        )

    # ========================================================
    # Save remaining batch
    # ========================================================

    if batch:

        print()
        print(
            "寫入最後 Embedding Batch:",
            len(batch)
        )

        try:

            saved_count = (
                _save_embedding_batch(
                    batch
                )
            )

            total_success += (
                saved_count
            )

            print(
                "最後 Batch 寫入成功:",
                saved_count
            )

        except Exception as exc:

            total_error += (
                len(batch)
            )

            print(
                "最後 Batch 寫入失敗:"
            )

            print(exc)

    # ========================================================
    # Result
    # ========================================================

    result = {
        "success": total_success,
        "skipped": total_skip,
        "error": total_error,
        "requested": (
            len(cve_ids)
            if cve_ids is not None
            else len(cves)
        ),
    }

    print()
    print(
        "=============================="
    )
    print(
        "CVE Embedding 處理完成"
    )
    print(
        "=============================="
    )
    print(
        "處理:",
        result["requested"]
    )
    print(
        "成功:",
        result["success"]
    )
    print(
        "跳過:",
        result["skipped"]
    )
    print(
        "失敗:",
        result["error"]
    )
    print(
        "=============================="
    )

    return result


# ============================================================
# Full Refresh Entry Point
# ============================================================

def main():
    """
    Manual/full embedding refresh.

    This allows:

        python -m riskGenie.services.cve_embedding

    to process all CVEs.

    The hash mechanism still prevents unnecessary
    re-embedding.
    """

    result = refresh_embeddings()

    if result["error"] > 0:

        print(
            "Embedding 工作完成，但有錯誤。"
        )

    else:

        print(
            "Embedding 工作正常完成。"
        )


# ============================================================
# Module Entry Point
# ============================================================

if __name__ == "__main__":
    main()