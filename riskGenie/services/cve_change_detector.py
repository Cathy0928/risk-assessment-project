# -*- coding: utf-8 -*-
"""
CVE normalization and content-change detection.

Pure, side-effect-free logic: no HTTP calls, no Supabase, no Gemini.
Given a raw NVD CVE record (or a previously-normalized one), this module
decides whether it is new, whether its content actually changed since
the last time it was embedded, or whether it is unchanged.

This intentionally does NOT decide whether an asset is vulnerable —
RiskGenie is an ISMS asset inventory / risk assessment tool, not a
vulnerability scanner. It only tracks whether a public CVE record's own
content has changed.
"""

import hashlib
import json


# ============================================================
# Normalization
# ============================================================

def normalize_cve(raw_cve):
    """Extract the fields RiskGenie stores/embeds from a raw NVD CVE
    record, in a stable shape independent of NVD's raw JSON layout.

    `raw_cve` is expected to be the value of `vulnerabilities[i]['cve']`
    from the NVD API 2.0 response (see nvd_client.NVDClient.iter_cves).
    """

    raw_cve = raw_cve or {}

    cve_id = raw_cve.get("id")

    description = _first_english(raw_cve.get("descriptions", []))

    cvss_score, severity = _extract_cvss(raw_cve.get("metrics", {}))

    cwe = _extract_cwe(raw_cve.get("weaknesses", []))

    reference_urls = sorted({
        ref.get("url")
        for ref in raw_cve.get("references", []) or []
        if isinstance(ref, dict) and ref.get("url")
    })

    return {
        "cve_id": cve_id,
        "vuln_status": raw_cve.get("vulnStatus"),
        "description": description,
        "cvss_score": cvss_score,
        "severity": severity,
        "cwe": cwe,
        "reference_urls": reference_urls,
        "last_modified": raw_cve.get("lastModified"),
        "published": raw_cve.get("published"),
    }


def _first_english(descriptions):
    for desc in descriptions or []:
        if isinstance(desc, dict) and desc.get("lang") == "en":
            return desc.get("value", "") or ""
    return ""


def _extract_cvss(metrics):
    metrics = metrics or {}

    for metric_key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(metric_key)
        if entries:
            cvss_data = (entries[0] or {}).get("cvssData", {}) or {}
            return cvss_data.get("baseScore"), cvss_data.get("baseSeverity")

    return None, None


def _extract_cwe(weaknesses):
    for weakness in weaknesses or []:
        for desc in (weakness or {}).get("description", []) or []:
            if isinstance(desc, dict) and desc.get("lang") == "en":
                value = desc.get("value")
                if value:
                    return value
    return None


# ============================================================
# Content hashing
# ============================================================

# Fields that participate in the content hash: i.e. fields whose change
# means "this CVE's content actually changed" and should trigger a
# re-embedding. NVD's own `last_modified` / `published` timestamps are
# deliberately excluded — NVD sometimes bumps `lastModified` without any
# of these fields changing (e.g. internal bookkeeping edits), and we
# don't want to pay for a Gemini re-embed when nothing a reader would
# notice actually moved.
HASHED_FIELDS = (
    "cve_id",
    "description",
    "cvss_score",
    "severity",
    "cwe",
    "reference_urls",
)


def compute_content_hash(normalized_cve):
    """Stable SHA-256 hex digest over the fields in HASHED_FIELDS.

    Uses sort_keys + fixed separators + ensure_ascii so the same
    logical content always serializes identically and therefore always
    hashes the same, regardless of dict key ordering or platform.
    """

    hashable = {
        field: normalized_cve.get(field)
        for field in HASHED_FIELDS
    }

    serialized = json.dumps(
        hashable,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ============================================================
# Change classification
# ============================================================

class ChangeType:
    NEW = "new"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def classify_change(normalized_cve, existing_content_hash):
    """Classify one CVE against the last known content hash for it.

    `existing_content_hash` should be whatever is currently stored for
    this cve_id (e.g. cve_embeddings.content_hash), or None if this CVE
    has never been embedded before.

    Returns (change_type, new_content_hash).
    """

    new_hash = compute_content_hash(normalized_cve)

    if existing_content_hash is None:
        return ChangeType.NEW, new_hash

    if existing_content_hash == new_hash:
        return ChangeType.UNCHANGED, new_hash

    return ChangeType.UPDATED, new_hash


def plan_sync(raw_cves, existing_hash_by_id):
    """Classify a batch of raw NVD CVE records against currently-stored
    content hashes, keyed by cve_id.

    Returns a dict with three lists — "new", "updated", "unchanged" —
    each containing {"cve_id", "normalized", "content_hash"} entries.
    Does not talk to any database or external API; existing_hash_by_id
    is supplied by the caller (typically read once from cve_embeddings).
    """

    plan = {
        ChangeType.NEW: [],
        ChangeType.UPDATED: [],
        ChangeType.UNCHANGED: [],
    }

    for raw_cve in raw_cves or []:
        normalized = normalize_cve(raw_cve)
        cve_id = normalized.get("cve_id")

        if not cve_id:
            continue

        existing_hash = existing_hash_by_id.get(cve_id)
        change_type, content_hash = classify_change(
            normalized,
            existing_hash,
        )

        plan[change_type].append({
            "cve_id": cve_id,
            "normalized": normalized,
            "content_hash": content_hash,
        })

    return plan
