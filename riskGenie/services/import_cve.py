"""
CVE parser / normalizer for RiskGenie.

This module is responsible for:
1. Parsing one NVD vulnerability item
2. Normalizing CVE fields
3. Calculating deterministic content_hash
4. Parsing local NVD JSON files when needed

This module does NOT:
- Create a Supabase client
- Write to Supabase
- Call Gemini
- Automatically scan the cve-data directory
"""

import hashlib
import json
from pathlib import Path


# ============================================================
# Content Hash
# ============================================================

def calculate_content_hash(data):
    """
    Calculate a deterministic hash for the important CVE content.

    The hash is used to determine whether the CVE content has changed.
    If the hash is unchanged, the embedding does not need to be regenerated.
    """

    content = {
        "cve_id": data.get("cve_id"),
        "description": data.get("description"),
        "cvss_score": data.get("cvss_score"),
        "severity": data.get("severity"),
        "cwe": data.get("cwe"),
    }

    raw = json.dumps(
        content,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# Description
# ============================================================

def _extract_description(cve):
    """
    Extract the English CVE description.
    """

    for desc in cve.get("descriptions", []):
        if desc.get("lang") == "en":
            return desc.get("value", "") or ""

    return ""


# ============================================================
# CVSS
# ============================================================

def _extract_cvss(cve):
    """
    Extract CVSS score and severity.

    Priority:
    1. CVSS v4.0
    2. CVSS v3.1
    3. CVSS v3.0
    4. CVSS v2

    CVSS v2 does not normally provide baseSeverity in the same
    structure used by v3/v4, so severity remains None there.
    """

    metrics = cve.get("metrics", {})

    # --------------------------------------------------------
    # CVSS v4.0
    # --------------------------------------------------------

    if metrics.get("cvssMetricV40"):
        metric = metrics["cvssMetricV40"][0]

        cvss = metric.get(
            "cvssData",
            {}
        )

        return (
            cvss.get("baseScore"),
            cvss.get("baseSeverity"),
        )

    # --------------------------------------------------------
    # CVSS v3.1
    # --------------------------------------------------------

    if metrics.get("cvssMetricV31"):
        metric = metrics["cvssMetricV31"][0]

        cvss = metric.get(
            "cvssData",
            {}
        )

        return (
            cvss.get("baseScore"),
            cvss.get("baseSeverity"),
        )

    # --------------------------------------------------------
    # CVSS v3.0
    # --------------------------------------------------------

    if metrics.get("cvssMetricV30"):
        metric = metrics["cvssMetricV30"][0]

        cvss = metric.get(
            "cvssData",
            {}
        )

        return (
            cvss.get("baseScore"),
            cvss.get("baseSeverity"),
        )

    # --------------------------------------------------------
    # CVSS v2
    # --------------------------------------------------------

    if metrics.get("cvssMetricV2"):
        metric = metrics["cvssMetricV2"][0]

        cvss = metric.get(
            "cvssData",
            {}
        )

        return (
            cvss.get("baseScore"),
            None,
        )

    return None, None


# ============================================================
# CWE
# ============================================================

def _extract_cwe(cve):
    """
    Extract the first English CWE value.
    """

    for weakness in cve.get(
        "weaknesses",
        []
    ):

        for desc in weakness.get(
            "description",
            []
        ):

            if desc.get("lang") == "en":
                value = desc.get("value")

                if value:
                    return value

    return None


# ============================================================
# References
# ============================================================

def _extract_references(cve):
    """
    Extract reference URLs from the CVE.
    """

    references = []

    for ref in cve.get(
        "references",
        []
    ):

        url = ref.get("url")

        if url:
            references.append(url)

    return references


# ============================================================
# Source Modified Time
# ============================================================

def _extract_source_modified_at(cve):
    """
    Extract the NVD source modification timestamp.

    NVD normally provides this value through dateModified.
    """

    return cve.get("lastModified")


# ============================================================
# Published Time
# ============================================================

def _extract_published_at(cve):
    """
    Extract the CVE published timestamp.
    """

    return cve.get("published")


# ============================================================
# Normalize One CVE
# ============================================================

def normalize_cve(item):
    """
    Convert one NVD vulnerability item into the normalized
    structure used by RiskGenie.

    Expected input:

        {
            "cve": {
                ...
            }
        }

    Returns:

        {
            "cve_id": ...,
            "description": ...,
            "cvss_score": ...,
            "severity": ...,
            "cwe": ...,
            "reference_urls": ...,
            "source_modified_at": ...,
            "published_at": ...,
            "content_hash": ...
        }

    Returns None when the item does not contain a valid CVE ID.
    """

    if not isinstance(item, dict):
        return None

    cve = item.get(
        "cve",
        {}
    )

    if not isinstance(cve, dict):
        return None

    # --------------------------------------------------------
    # CVE ID
    # --------------------------------------------------------

    cve_id = cve.get("id")

    if not cve_id:
        return None

    # --------------------------------------------------------
    # Description
    # --------------------------------------------------------

    description = _extract_description(cve)

    # --------------------------------------------------------
    # CVSS
    # --------------------------------------------------------

    cvss_score, severity = _extract_cvss(cve)

    # --------------------------------------------------------
    # CWE
    # --------------------------------------------------------

    cwe = _extract_cwe(cve)

    # --------------------------------------------------------
    # References
    # --------------------------------------------------------

    reference_urls = _extract_references(cve)

    # --------------------------------------------------------
    # Source timestamps
    # --------------------------------------------------------

    source_modified_at = _extract_source_modified_at(cve)

    published_at = _extract_published_at(cve)

    # --------------------------------------------------------
    # Normalized data
    # --------------------------------------------------------

    row = {
        "cve_id": cve_id,
        "description": description,
        "cvss_score": cvss_score,
        "severity": severity,
        "cwe": cwe,
        "reference_urls": reference_urls,
        "source_modified_at": source_modified_at,
        "published_at": published_at,
    }

    # --------------------------------------------------------
    # Content hash
    # --------------------------------------------------------

    row["content_hash"] = calculate_content_hash(
        row
    )

    return row


# ============================================================
# Parse NVD JSON data
# ============================================================

def parse_nvd_data(data):
    """
    Parse an NVD JSON response.

    Returns a list of normalized CVEs.

    This function does NOT write to Supabase.
    """

    if not isinstance(data, dict):
        raise ValueError(
            "NVD data must be a dictionary."
        )

    vulnerabilities = data.get(
        "vulnerabilities",
        []
    )

    normalized = []

    for item in vulnerabilities:

        try:

            row = normalize_cve(item)

            if row is not None:
                normalized.append(row)

        except Exception as exc:

            cve_id = None

            try:
                cve_id = (
                    item
                    .get("cve", {})
                    .get("id")
                )
            except Exception:
                pass

            print(
                f"CVE parsing error: {cve_id} | {exc}"
            )

            # One bad CVE should not stop
            # the remaining CVEs.
            continue

    return normalized


# ============================================================
# Parse Local NVD JSON File
# ============================================================

def parse_nvd_json_file(filepath):
    """
    Parse one local NVD JSON file.

    This is kept as a utility for testing or legacy data migration.

    It does NOT write to Supabase.
    """

    path = Path(filepath)

    with path.open(
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    return parse_nvd_data(data)


# ============================================================
# Parse Multiple Local NVD JSON Files
# ============================================================

def parse_nvd_directory(directory):
    """
    Parse all JSON files under a local directory.

    This function only parses files.
    It does NOT write to Supabase.

    It is mainly useful for:
    - testing
    - migration
    - validating old cve-data
    """

    directory = Path(directory)

    if not directory.exists():
        raise FileNotFoundError(
            f"NVD directory does not exist: {directory}"
        )

    results = []

    for filepath in directory.rglob("*.json"):

        try:

            rows = parse_nvd_json_file(
                filepath
            )

            results.extend(rows)

            print(
                f"讀取: {filepath.name} "
                f"數量: {len(rows)}"
            )

        except Exception as exc:

            print(
                f"檔案錯誤: {filepath} | {exc}"
            )

    return results