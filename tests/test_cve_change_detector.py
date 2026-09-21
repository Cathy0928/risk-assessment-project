from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from riskGenie.services.cve_change_detector import (  # noqa: E402
    ChangeType,
    classify_change,
    compute_content_hash,
    normalize_cve,
    plan_sync,
)


def raw_cve(
    cve_id="CVE-2024-0001",
    description="A critical remote code execution vulnerability.",
    cvss_score=9.8,
    severity="CRITICAL",
    cwe="CWE-79",
    urls=("https://example.com/a", "https://example.com/b"),
    last_modified="2024-01-01T00:00:00.000",
):
    return {
        "id": cve_id,
        "lastModified": last_modified,
        "published": "2023-12-01T00:00:00.000",
        "descriptions": [
            {"lang": "fr", "value": "ignore-moi"},
            {"lang": "en", "value": description},
        ],
        "metrics": {
            "cvssMetricV31": [
                {
                    "cvssData": {
                        "baseScore": cvss_score,
                        "baseSeverity": severity,
                    }
                }
            ]
        },
        "weaknesses": [
            {"description": [{"lang": "en", "value": cwe}]}
        ],
        "references": [{"url": url} for url in urls],
    }


# ================================================================
# normalize_cve
# ================================================================

def test_normalize_cve_extracts_english_description_only():
    normalized = normalize_cve(raw_cve())
    assert normalized["description"] == "A critical remote code execution vulnerability."


def test_normalize_cve_prefers_cvss_v31_over_v2():
    cve = raw_cve()
    cve["metrics"]["cvssMetricV2"] = [
        {"cvssData": {"baseScore": 5.0}}
    ]
    normalized = normalize_cve(cve)
    assert normalized["cvss_score"] == 9.8


def test_normalize_cve_falls_back_to_cvss_v2_when_v3_absent():
    cve = raw_cve()
    del cve["metrics"]["cvssMetricV31"]
    cve["metrics"]["cvssMetricV2"] = [
        {"cvssData": {"baseScore": 5.0, "baseSeverity": "MEDIUM"}}
    ]
    normalized = normalize_cve(cve)
    assert normalized["cvss_score"] == 5.0


def test_normalize_cve_handles_missing_optional_fields():
    normalized = normalize_cve({"id": "CVE-2024-9999"})

    assert normalized["cve_id"] == "CVE-2024-9999"
    assert normalized["description"] == ""
    assert normalized["cvss_score"] is None
    assert normalized["severity"] is None
    assert normalized["cwe"] is None
    assert normalized["reference_urls"] == []


def test_normalize_cve_deduplicates_and_sorts_reference_urls():
    cve = raw_cve(urls=("https://b.example", "https://a.example", "https://a.example"))
    normalized = normalize_cve(cve)
    assert normalized["reference_urls"] == ["https://a.example", "https://b.example"]


# ================================================================
# compute_content_hash: stability
# ================================================================

def test_content_hash_is_stable_across_repeated_calls():
    normalized = normalize_cve(raw_cve())
    assert compute_content_hash(normalized) == compute_content_hash(normalized)


def test_content_hash_is_independent_of_dict_key_order():
    a = {"cve_id": "X", "description": "d", "cvss_score": 1, "severity": "LOW", "cwe": None, "reference_urls": []}
    b = {"reference_urls": [], "cwe": None, "severity": "LOW", "cvss_score": 1, "description": "d", "cve_id": "X"}
    assert compute_content_hash(a) == compute_content_hash(b)


def test_content_hash_ignores_nvd_last_modified_timestamp():
    normalized_v1 = normalize_cve(raw_cve(last_modified="2024-01-01T00:00:00.000"))
    normalized_v2 = normalize_cve(raw_cve(last_modified="2024-06-01T00:00:00.000"))

    # NVD bumping lastModified without any real content change must not
    # trigger a re-embed.
    assert compute_content_hash(normalized_v1) == compute_content_hash(normalized_v2)


def test_content_hash_changes_when_description_changes():
    v1 = normalize_cve(raw_cve(description="Original description."))
    v2 = normalize_cve(raw_cve(description="Updated description with new details."))
    assert compute_content_hash(v1) != compute_content_hash(v2)


def test_content_hash_changes_when_cvss_score_changes():
    v1 = normalize_cve(raw_cve(cvss_score=9.8))
    v2 = normalize_cve(raw_cve(cvss_score=7.5))
    assert compute_content_hash(v1) != compute_content_hash(v2)


def test_content_hash_unaffected_by_unrelated_field():
    v1 = normalize_cve(raw_cve(last_modified="2024-01-01T00:00:00.000"))
    v2 = dict(v1)
    v2["some_future_field_not_in_hashed_fields"] = "whatever"
    assert compute_content_hash(v1) == compute_content_hash(v2)


# ================================================================
# classify_change
# ================================================================

def test_classify_change_new_when_no_existing_hash():
    normalized = normalize_cve(raw_cve())
    change_type, new_hash = classify_change(normalized, existing_content_hash=None)
    assert change_type == ChangeType.NEW
    assert new_hash == compute_content_hash(normalized)


def test_classify_change_unchanged_when_hash_matches():
    normalized = normalize_cve(raw_cve())
    existing_hash = compute_content_hash(normalized)
    change_type, new_hash = classify_change(normalized, existing_content_hash=existing_hash)
    assert change_type == ChangeType.UNCHANGED
    assert new_hash == existing_hash


def test_classify_change_updated_when_hash_differs():
    normalized = normalize_cve(raw_cve(description="New text."))
    change_type, _ = classify_change(normalized, existing_content_hash="stale-hash-value")
    assert change_type == ChangeType.UPDATED


# ================================================================
# plan_sync
# ================================================================

def test_plan_sync_buckets_new_updated_and_unchanged_correctly():
    unchanged_cve = raw_cve(cve_id="CVE-2024-0001", description="stable")
    updated_cve = raw_cve(cve_id="CVE-2024-0002", description="changed now")
    new_cve = raw_cve(cve_id="CVE-2024-0003", description="brand new")

    unchanged_hash = compute_content_hash(normalize_cve(unchanged_cve))

    plan = plan_sync(
        [unchanged_cve, updated_cve, new_cve],
        existing_hash_by_id={
            "CVE-2024-0001": unchanged_hash,
            "CVE-2024-0002": "some-old-hash-that-no-longer-matches",
        },
    )

    assert [entry["cve_id"] for entry in plan[ChangeType.UNCHANGED]] == ["CVE-2024-0001"]
    assert [entry["cve_id"] for entry in plan[ChangeType.UPDATED]] == ["CVE-2024-0002"]
    assert [entry["cve_id"] for entry in plan[ChangeType.NEW]] == ["CVE-2024-0003"]


def test_plan_sync_skips_entries_without_a_cve_id():
    plan = plan_sync([{"lastModified": "x"}], existing_hash_by_id={})

    assert plan[ChangeType.NEW] == []
    assert plan[ChangeType.UPDATED] == []
    assert plan[ChangeType.UNCHANGED] == []


def test_plan_sync_does_not_mutate_existing_hash_map():
    original = {"CVE-2024-0001": "hash-a"}
    frozen_copy = dict(original)

    plan_sync([raw_cve(cve_id="CVE-2024-0001")], existing_hash_by_id=original)

    assert original == frozen_copy
