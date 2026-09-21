# -*- coding: utf-8 -*-
"""
NVD (National Vulnerability Database) CVE API 2.0 client.

Scope of this module:
    - Fetch CVE records from the official NVD REST API
      (https://services.nvd.nist.gov/rest/json/cves/2.0), including
      pagination, timeouts, HTTP 429 / Retry-After handling, and a
      bounded retry/backoff for transient failures.

Explicitly NOT this module's job:
    - Deciding whether a CVE is "new" / "changed" (see cve_change_detector.py).
    - Writing anything to Supabase.
    - Deciding whether an asset is vulnerable. RiskGenie is an ISMS asset
      inventory / risk assessment tool, not a vulnerability scanner —
      this client only fetches public CVE metadata.
    - Scheduling / running itself periodically. Callers decide when and
      how often to invoke it.

This module makes no network calls at import time and never calls the
real NVD API from tests — every test injects a fake `session` object
whose `.get(...)` is fully under test control.
"""

import logging
import time
from datetime import datetime, timezone

import requests


logger = logging.getLogger(__name__)


# ============================================================
# NVD API 2.0 constants
# ============================================================

NVD_BASE_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# NVD enforces at most 120 consecutive days between lastModStartDate and
# lastModEndDate when both are supplied. Sending a wider range is a
# guaranteed 4xx from NVD, so we fail fast locally instead.
MAX_LAST_MODIFIED_RANGE_DAYS = 120

# NVD caps resultsPerPage at 2000 as of API 2.0.
MAX_RESULTS_PER_PAGE = 2000
DEFAULT_RESULTS_PER_PAGE = 2000

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_MAX_BACKOFF_SECONDS = 60.0

REQUIRED_RESPONSE_FIELDS = (
    "totalResults",
    "resultsPerPage",
    "startIndex",
    "vulnerabilities",
)


# ============================================================
# Errors
# ============================================================

class NVDClientError(Exception):
    """Base error for all NVD client failures."""


class NVDInvalidParametersError(NVDClientError):
    """Raised for parameters we can reject before making a request
    (e.g. a lastModified window wider than NVD allows)."""


class NVDRateLimitExceededError(NVDClientError):
    """Raised when NVD keeps responding 429 past the retry budget."""


class NVDResponseError(NVDClientError):
    """Raised when NVD returns a non-200, non-JSON, or incomplete body."""


# ============================================================
# Client
# ============================================================

class NVDClient:
    """Thin, retrying HTTP client for the NVD CVE API 2.0.

    `session` defaults to the `requests` module itself but can be any
    object exposing a `.get(url, params=, headers=, timeout=)` method
    that returns an object with `.status_code`, `.json()`, and
    `.headers` — this is what tests inject instead of hitting the
    network.

    `sleep` defaults to `time.sleep` but can be replaced in tests so
    retry/backoff logic runs instantly instead of actually waiting.
    """

    def __init__(
        self,
        api_key=None,
        session=None,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_retries=DEFAULT_MAX_RETRIES,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        max_backoff_seconds=DEFAULT_MAX_BACKOFF_SECONDS,
        sleep=None,
    ):
        self._api_key = api_key
        self._session = session or requests
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._sleep = sleep or time.sleep

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def fetch_page(
        self,
        last_mod_start_date=None,
        last_mod_end_date=None,
        start_index=0,
        results_per_page=DEFAULT_RESULTS_PER_PAGE,
    ):
        """Fetch one page of CVE results. Returns the raw decoded JSON
        body (already validated to have the expected top-level shape)."""

        self._validate_date_range(
            last_mod_start_date,
            last_mod_end_date,
        )

        if start_index < 0:
            raise NVDInvalidParametersError(
                "start_index must be >= 0."
            )

        results_per_page = max(
            1,
            min(results_per_page, MAX_RESULTS_PER_PAGE),
        )

        params = {
            "startIndex": start_index,
            "resultsPerPage": results_per_page,
        }

        if last_mod_start_date is not None:
            params["lastModStartDate"] = last_mod_start_date

        if last_mod_end_date is not None:
            params["lastModEndDate"] = last_mod_end_date

        return self._get_with_retry(params)

    def iter_cves(
        self,
        last_mod_start_date=None,
        last_mod_end_date=None,
        results_per_page=DEFAULT_RESULTS_PER_PAGE,
        max_pages=None,
    ):
        """Yield each CVE dict (the value of vulnerabilities[i]['cve'])
        across every page in the requested window.

        Pagination follows NVD's documented contract: keep advancing
        startIndex by the number of results actually returned until
        startIndex >= totalResults, rather than assuming a fixed page
        size (the last page is often short).
        """

        self.last_iteration_complete = False
        start_index = 0
        pages_fetched = 0

        while True:
            page = self.fetch_page(
                last_mod_start_date=last_mod_start_date,
                last_mod_end_date=last_mod_end_date,
                start_index=start_index,
                results_per_page=results_per_page,
            )

            vulnerabilities = page["vulnerabilities"]

            for item in vulnerabilities:
                cve = item.get("cve") if isinstance(item, dict) else None
                if cve:
                    yield cve

            pages_fetched += 1
            fetched_this_page = len(vulnerabilities)
            total_results = page.get("totalResults") or 0

            start_index += fetched_this_page

            if fetched_this_page == 0:
                # No progress possible; avoid an infinite loop on a
                # malformed-but-technically-valid response.
                self.last_iteration_complete = start_index >= total_results
                break

            if start_index >= total_results:
                self.last_iteration_complete = True
                break

            if max_pages is not None and pages_fetched >= max_pages:
                logger.info(
                    "NVD iter_cves stopped at max_pages=%s "
                    "(start_index=%s, totalResults=%s).",
                    max_pages,
                    start_index,
                    total_results,
                )
                break

    # --------------------------------------------------------
    # Internal: HTTP + retry/backoff
    # --------------------------------------------------------

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["apiKey"] = self._api_key
        return headers

    def _get_with_retry(self, params):
        attempt = 0

        while True:
            attempt += 1

            try:
                response = self._session.get(
                    NVD_BASE_URL,
                    params=params,
                    headers=self._headers(),
                    timeout=self._timeout,
                )

            except requests.Timeout as exc:
                self._retry_or_raise(
                    attempt,
                    NVDClientError(
                        f"NVD API timed out after {attempt} attempt(s): {exc}"
                    ),
                )
                continue

            except requests.RequestException as exc:
                self._retry_or_raise(
                    attempt,
                    NVDClientError(
                        f"NVD API request failed after {attempt} "
                        f"attempt(s): {exc}"
                    ),
                )
                continue

            status_code = response.status_code

            if status_code == 429:
                retry_after = self._parse_retry_after(response)
                self._retry_or_raise(
                    attempt,
                    NVDRateLimitExceededError(
                        f"NVD API rate limit exceeded after {attempt} "
                        "attempt(s)."
                    ),
                    delay_override=retry_after,
                )
                continue

            if 500 <= status_code < 600:
                self._retry_or_raise(
                    attempt,
                    NVDClientError(
                        f"NVD API returned {status_code} after "
                        f"{attempt} attempt(s)."
                    ),
                )
                continue

            if status_code != 200:
                raise NVDResponseError(
                    f"NVD API returned unexpected status {status_code}: "
                    f"{self._safe_body_preview(response)}"
                )

            return self._parse_and_validate(response)

    def _retry_or_raise(self, attempt, error, delay_override=None):
        if attempt > self._max_retries:
            raise error

        delay = (
            delay_override
            if delay_override is not None
            else self._backoff_delay(attempt)
        )

        logger.warning(
            "%s Retrying in %.1fs (attempt %s/%s).",
            error,
            delay,
            attempt,
            self._max_retries,
        )

        self._sleep(delay)

    def _backoff_delay(self, attempt):
        delay = self._backoff_seconds * (2 ** (attempt - 1))
        return min(delay, self._max_backoff_seconds)

    @staticmethod
    def _parse_retry_after(response):
        headers = getattr(response, "headers", None) or {}
        raw = headers.get("Retry-After")

        if raw is None:
            return None

        try:
            return max(float(raw), 0.0)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_body_preview(response):
        try:
            return str(getattr(response, "text", ""))[:500]
        except Exception:
            return "<unavailable>"

    def _parse_and_validate(self, response):
        try:
            payload = response.json()
        except ValueError as exc:
            raise NVDResponseError(
                f"NVD API returned a non-JSON response: {exc}"
            ) from exc

        if not isinstance(payload, dict):
            raise NVDResponseError(
                "NVD API response is not a JSON object."
            )

        missing = [
            field
            for field in REQUIRED_RESPONSE_FIELDS
            if field not in payload
        ]

        if missing:
            raise NVDResponseError(
                "NVD API response missing required field(s): "
                + ", ".join(missing)
            )

        if not isinstance(payload["vulnerabilities"], list):
            raise NVDResponseError(
                "NVD API response 'vulnerabilities' field is not a list."
            )

        return payload

    # --------------------------------------------------------
    # Internal: parameter validation
    # --------------------------------------------------------

    @staticmethod
    def _validate_date_range(start, end):
        if start is None or end is None:
            return

        start_dt = NVDClient._parse_iso8601(start, "last_mod_start_date")
        end_dt = NVDClient._parse_iso8601(end, "last_mod_end_date")

        if end_dt < start_dt:
            raise NVDInvalidParametersError(
                "last_mod_end_date must not be before last_mod_start_date."
            )

        span_days = (end_dt - start_dt).total_seconds() / 86400.0

        if span_days > MAX_LAST_MODIFIED_RANGE_DAYS:
            raise NVDInvalidParametersError(
                "NVD only allows up to "
                f"{MAX_LAST_MODIFIED_RANGE_DAYS} days between "
                "last_mod_start_date and last_mod_end_date "
                f"(got {span_days:.1f} days)."
            )

    @staticmethod
    def _parse_iso8601(value, field_name):
        text = value.strip() if isinstance(value, str) else value

        if not isinstance(text, str) or not text:
            raise NVDInvalidParametersError(
                f"{field_name} must be a non-empty ISO-8601 string."
            )

        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise NVDInvalidParametersError(
                f"{field_name}={value!r} is not a valid ISO-8601 "
                f"timestamp: {exc}"
            ) from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed
