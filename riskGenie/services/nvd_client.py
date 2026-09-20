"""
NVD CVE API client for RiskGenie.

Responsibilities:
- Connect to NVD CVE API 2.0
- Fetch CVEs incrementally by last modified time
- Handle pagination
- Handle timeout / retry / rate limiting
- Return raw NVD CVE records

This module must NOT:
- Access Supabase
- Write to database
- Generate embeddings
- Decide whether an asset is vulnerable
- Modify risk scores
"""

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv


load_dotenv()


NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# NVD pagination
DEFAULT_RESULTS_PER_PAGE = 2000
MAX_RESULTS_PER_PAGE = 2000

# HTTP behavior
REQUEST_TIMEOUT = 30
MAX_RETRY = 3
INITIAL_BACKOFF = 2
MAX_BACKOFF = 30

# NVD API key is optional
NVD_API_KEY = os.getenv("NVD_API_KEY")


class NVDAPIError(RuntimeError):
    """Raised when NVD API cannot be reached successfully."""


def _format_nvd_datetime(value: datetime) -> str:
    """
    Convert datetime to NVD API format.

    Example:
        2026-09-21T10:30:00.000Z
    """

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    value = value.astimezone(timezone.utc)

    return value.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_retry_after(response: requests.Response) -> Optional[int]:
    """
    Read Retry-After header if provided by NVD.
    """

    retry_after = response.headers.get("Retry-After")

    if not retry_after:
        return None

    try:
        return max(1, int(retry_after))
    except ValueError:
        return None


def _request(
    params: Dict[str, Any],
    session: requests.Session,
) -> Dict[str, Any]:
    """
    Perform one NVD API request with bounded retry.
    """

    headers = {
        "Accept": "application/json",
    }

    if NVD_API_KEY:
        headers["apiKey"] = NVD_API_KEY

    last_error = None

    for attempt in range(1, MAX_RETRY + 1):

        try:
            response = session.get(
                NVD_API_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            # Success
            if response.status_code == 200:
                return response.json()

            # Rate limit
            if response.status_code == 429:
                retry_after = _parse_retry_after(response)

                if retry_after is None:
                    retry_after = min(
                        INITIAL_BACKOFF * (2 ** (attempt - 1)),
                        MAX_BACKOFF,
                    )

                if attempt < MAX_RETRY:
                    print(
                        f"[NVD] HTTP 429. "
                        f"Retrying in {retry_after} seconds "
                        f"(attempt {attempt}/{MAX_RETRY})"
                    )
                    time.sleep(retry_after)
                    continue

                raise NVDAPIError(
                    "NVD API rate limit exceeded after retries."
                )

            # Temporary server errors
            if response.status_code in (500, 502, 503, 504):

                if attempt < MAX_RETRY:
                    sleep_time = min(
                        INITIAL_BACKOFF * (2 ** (attempt - 1)),
                        MAX_BACKOFF,
                    )

                    print(
                        f"[NVD] HTTP {response.status_code}. "
                        f"Retrying in {sleep_time} seconds "
                        f"(attempt {attempt}/{MAX_RETRY})"
                    )

                    time.sleep(sleep_time)
                    continue

                raise NVDAPIError(
                    f"NVD API server error: HTTP {response.status_code}"
                )

            # Other HTTP errors should fail immediately
            raise NVDAPIError(
                f"NVD API request failed: "
                f"HTTP {response.status_code} - {response.text[:500]}"
            )

        except requests.Timeout as exc:
            last_error = exc

            if attempt < MAX_RETRY:
                sleep_time = min(
                    INITIAL_BACKOFF * (2 ** (attempt - 1)),
                    MAX_BACKOFF,
                )

                print(
                    f"[NVD] Request timeout. "
                    f"Retrying in {sleep_time} seconds "
                    f"(attempt {attempt}/{MAX_RETRY})"
                )

                time.sleep(sleep_time)
                continue

        except requests.RequestException as exc:
            last_error = exc

            if attempt < MAX_RETRY:
                sleep_time = min(
                    INITIAL_BACKOFF * (2 ** (attempt - 1)),
                    MAX_BACKOFF,
                )

                print(
                    f"[NVD] Network error: {exc}. "
                    f"Retrying in {sleep_time} seconds "
                    f"(attempt {attempt}/{MAX_RETRY})"
                )

                time.sleep(sleep_time)
                continue

    raise NVDAPIError(
        f"NVD API request failed after {MAX_RETRY} retries: "
        f"{last_error}"
    )


def fetch_cves(
    modified_start: datetime,
    modified_end: Optional[datetime] = None,
    results_per_page: int = DEFAULT_RESULTS_PER_PAGE,
) -> List[Dict[str, Any]]:
    """
    Fetch CVEs modified within the specified time range.

    Args:
        modified_start:
            Start of NVD last-modified window.

        modified_end:
            End of NVD last-modified window.
            Defaults to current UTC time.

        results_per_page:
            Number of CVEs per API request.

    Returns:
        List of raw NVD vulnerability records.
    """

    if modified_end is None:
        modified_end = datetime.now(timezone.utc)

    if modified_start.tzinfo is None:
        modified_start = modified_start.replace(tzinfo=timezone.utc)

    if modified_end.tzinfo is None:
        modified_end = modified_end.replace(tzinfo=timezone.utc)

    if modified_start > modified_end:
        raise ValueError(
            "modified_start must not be later than modified_end."
        )

    results_per_page = min(
        max(1, results_per_page),
        MAX_RESULTS_PER_PAGE,
    )

    session = requests.Session()

    all_vulnerabilities: List[Dict[str, Any]] = []

    start_index = 0

    start_date = _format_nvd_datetime(modified_start)
    end_date = _format_nvd_datetime(modified_end)

    while True:

        params = {
            "lastModStartDate": start_date,
            "lastModEndDate": end_date,
            "startIndex": start_index,
            "resultsPerPage": results_per_page,
        }

        print(
            f"[NVD] Fetching CVEs "
            f"startIndex={start_index}, "
            f"resultsPerPage={results_per_page}"
        )

        data = _request(
            params=params,
            session=session,
        )

        vulnerabilities = data.get("vulnerabilities", [])

        total_results = int(
            data.get("totalResults", 0)
        )

        all_vulnerabilities.extend(vulnerabilities)

        print(
            f"[NVD] Received {len(vulnerabilities)} CVEs. "
            f"Total fetched: {len(all_vulnerabilities)}/"
            f"{total_results}"
        )

        # Nothing more to fetch
        if not vulnerabilities:
            break

        start_index += len(vulnerabilities)

        # Finished all results
        if start_index >= total_results:
            break

    return all_vulnerabilities


def fetch_cves_since(
    modified_start: datetime,
    overlap_minutes: int = 2,
) -> List[Dict[str, Any]]:
    """
    Fetch CVEs modified since a previous sync.

    A small overlap is intentionally used to avoid missing records
    modified exactly around the synchronization boundary.

    Duplicate CVEs are expected to be removed later by the sync
    service using CVE ID + content hash.
    """

    if modified_start.tzinfo is None:
        modified_start = modified_start.replace(tzinfo=timezone.utc)

    adjusted_start = modified_start - timedelta(
        minutes=overlap_minutes
    )

    modified_end = datetime.now(timezone.utc)

    return fetch_cves(
        modified_start=adjusted_start,
        modified_end=modified_end,
    )


if __name__ == "__main__":
    """
    Simple manual test.

    Fetch CVEs modified during the last 24 hours.
    """

    end = datetime.now(timezone.utc)

    start = end - timedelta(days=1)

    print(
        f"[NVD] Test fetch: "
        f"{_format_nvd_datetime(start)} -> "
        f"{_format_nvd_datetime(end)}"
    )

    try:
        vulnerabilities = fetch_cves(
            modified_start=start,
            modified_end=end,
        )

        print(
            f"[NVD] Successfully fetched "
            f"{len(vulnerabilities)} CVE records."
        )

    except Exception as exc:
        print(f"[NVD] ERROR: {exc}")
        raise