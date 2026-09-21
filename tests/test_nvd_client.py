"""
Tests for riskGenie/services/nvd_client.py.

Every test injects a FakeSession instead of hitting the real NVD API —
no network calls are made. `sleep` is stubbed to a no-op recorder so
retry/backoff tests run instantly instead of actually waiting.
"""

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from riskGenie.services.nvd_client import (  # noqa: E402
    NVDClient,
    NVDClientError,
    NVDInvalidParametersError,
    NVDRateLimitExceededError,
    NVDResponseError,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data


class FakeSession:
    """Returns queued responses in order; raises the queued exception if
    the queued item is an Exception instance instead of a FakeResponse."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({
            "url": url,
            "params": params,
            "headers": headers,
            "timeout": timeout,
        })

        if not self.responses:
            raise AssertionError("FakeSession ran out of queued responses.")

        item = self.responses.pop(0)

        if isinstance(item, Exception):
            raise item

        return item


def make_page(vulnerabilities, total_results=None, results_per_page=None, start_index=0):
    return {
        "totalResults": (
            total_results if total_results is not None else len(vulnerabilities)
        ),
        "resultsPerPage": (
            results_per_page if results_per_page is not None else len(vulnerabilities)
        ),
        "startIndex": start_index,
        "vulnerabilities": vulnerabilities,
    }


def cve_item(cve_id):
    return {"cve": {"id": cve_id, "descriptions": [], "metrics": {}}}


def no_sleep():
    calls = []

    def sleep(seconds):
        calls.append(seconds)

    sleep.calls = calls
    return sleep


# ================================================================
# Pagination
# ================================================================

def test_iter_cves_paginates_across_multiple_pages():
    page1 = make_page(
        [cve_item("CVE-2024-0001"), cve_item("CVE-2024-0002")],
        total_results=3,
        results_per_page=2,
        start_index=0,
    )
    page2 = make_page(
        [cve_item("CVE-2024-0003")],
        total_results=3,
        results_per_page=2,
        start_index=2,
    )

    session = FakeSession([FakeResponse(json_data=page1), FakeResponse(json_data=page2)])
    client = NVDClient(session=session, sleep=no_sleep())

    ids = [cve["id"] for cve in client.iter_cves(results_per_page=2)]

    assert ids == ["CVE-2024-0001", "CVE-2024-0002", "CVE-2024-0003"]
    assert len(session.calls) == 2
    assert session.calls[0]["params"]["startIndex"] == 0
    assert session.calls[1]["params"]["startIndex"] == 2


def test_iter_cves_stops_when_a_page_returns_zero_results():
    # totalResults overstates what's actually available; must not loop forever.
    page = make_page([], total_results=100, results_per_page=50, start_index=0)
    session = FakeSession([FakeResponse(json_data=page)])
    client = NVDClient(session=session, sleep=no_sleep())

    ids = list(client.iter_cves())

    assert ids == []
    assert len(session.calls) == 1


def test_iter_cves_respects_max_pages():
    page1 = make_page(
        [cve_item("CVE-2024-0001")],
        total_results=10,
        results_per_page=1,
        start_index=0,
    )
    page2 = make_page(
        [cve_item("CVE-2024-0002")],
        total_results=10,
        results_per_page=1,
        start_index=1,
    )

    session = FakeSession([FakeResponse(json_data=page1), FakeResponse(json_data=page2)])
    client = NVDClient(session=session, sleep=no_sleep())

    ids = [cve["id"] for cve in client.iter_cves(results_per_page=1, max_pages=2)]

    assert ids == ["CVE-2024-0001", "CVE-2024-0002"]
    assert len(session.calls) == 2
    assert client.last_iteration_complete is False


def test_fetch_page_caps_results_per_page_at_nvd_maximum():
    page = make_page([], total_results=0)
    session = FakeSession([FakeResponse(json_data=page)])
    client = NVDClient(session=session, sleep=no_sleep())

    client.fetch_page(results_per_page=999999)

    assert session.calls[0]["params"]["resultsPerPage"] == 2000


# ================================================================
# Timeout handling
# ================================================================

def test_fetch_page_retries_on_timeout_then_succeeds():
    import requests

    page = make_page([cve_item("CVE-2024-0001")])
    session = FakeSession([
        requests.Timeout("timed out"),
        FakeResponse(json_data=page),
    ])
    sleep = no_sleep()
    client = NVDClient(session=session, sleep=sleep, max_retries=3)

    result = client.fetch_page()

    assert result["vulnerabilities"][0]["cve"]["id"] == "CVE-2024-0001"
    assert len(session.calls) == 2
    assert len(sleep.calls) == 1


def test_fetch_page_raises_after_exhausting_retries_on_repeated_timeout():
    import requests

    session = FakeSession([
        requests.Timeout("t1"),
        requests.Timeout("t2"),
    ])
    sleep = no_sleep()
    client = NVDClient(session=session, sleep=sleep, max_retries=1)

    with pytest.raises(NVDClientError):
        client.fetch_page()

    assert len(session.calls) == 2


# ================================================================
# HTTP 429 / Retry-After
# ================================================================

def test_fetch_page_honors_retry_after_header_on_429():
    page = make_page([cve_item("CVE-2024-0001")])
    session = FakeSession([
        FakeResponse(status_code=429, headers={"Retry-After": "7"}),
        FakeResponse(json_data=page),
    ])
    sleep = no_sleep()
    client = NVDClient(session=session, sleep=sleep, max_retries=3)

    client.fetch_page()

    assert sleep.calls == [7.0]


def test_fetch_page_raises_rate_limit_error_after_exhausting_retries():
    session = FakeSession([
        FakeResponse(status_code=429, headers={"Retry-After": "1"}),
        FakeResponse(status_code=429, headers={"Retry-After": "1"}),
    ])
    sleep = no_sleep()
    client = NVDClient(session=session, sleep=sleep, max_retries=1)

    with pytest.raises(NVDRateLimitExceededError):
        client.fetch_page()


def test_fetch_page_falls_back_to_backoff_when_retry_after_missing():
    page = make_page([])
    session = FakeSession([
        FakeResponse(status_code=429, headers={}),
        FakeResponse(json_data=page),
    ])
    sleep = no_sleep()
    client = NVDClient(
        session=session,
        sleep=sleep,
        max_retries=3,
        backoff_seconds=2.0,
    )

    client.fetch_page()

    assert sleep.calls == [2.0]


# ================================================================
# Bounded retry/backoff for 5xx
# ================================================================

def test_fetch_page_retries_on_5xx_with_exponential_backoff():
    page = make_page([])
    session = FakeSession([
        FakeResponse(status_code=503),
        FakeResponse(status_code=503),
        FakeResponse(json_data=page),
    ])
    sleep = no_sleep()
    client = NVDClient(
        session=session,
        sleep=sleep,
        max_retries=3,
        backoff_seconds=1.0,
    )

    client.fetch_page()

    assert sleep.calls == [1.0, 2.0]


def test_fetch_page_gives_up_after_max_retries_on_5xx():
    session = FakeSession([
        FakeResponse(status_code=500),
        FakeResponse(status_code=500),
        FakeResponse(status_code=500),
    ])
    sleep = no_sleep()
    client = NVDClient(session=session, sleep=sleep, max_retries=2)

    with pytest.raises(NVDClientError):
        client.fetch_page()

    assert len(session.calls) == 3


# ================================================================
# Malformed / incomplete responses (non-retryable)
# ================================================================

def test_fetch_page_rejects_response_missing_required_field():
    bad_page = {"totalResults": 1, "resultsPerPage": 1}  # no startIndex/vulnerabilities
    session = FakeSession([FakeResponse(json_data=bad_page)])
    client = NVDClient(session=session, sleep=no_sleep())

    with pytest.raises(NVDResponseError):
        client.fetch_page()

    assert len(session.calls) == 1  # not retried — this is not transient


def test_fetch_page_rejects_non_json_body():
    session = FakeSession([FakeResponse(status_code=200, json_data=None)])
    client = NVDClient(session=session, sleep=no_sleep())

    with pytest.raises(NVDResponseError):
        client.fetch_page()


def test_fetch_page_rejects_unexpected_status_code_without_retrying():
    session = FakeSession([FakeResponse(status_code=403, text="Forbidden")])
    client = NVDClient(session=session, sleep=no_sleep())

    with pytest.raises(NVDResponseError):
        client.fetch_page()

    assert len(session.calls) == 1


# ================================================================
# Parameter validation
# ================================================================

def test_fetch_page_rejects_lastmod_window_wider_than_120_days():
    client = NVDClient(session=FakeSession([]), sleep=no_sleep())

    with pytest.raises(NVDInvalidParametersError):
        client.fetch_page(
            last_mod_start_date="2024-01-01T00:00:00.000Z",
            last_mod_end_date="2024-06-01T00:00:00.000Z",
        )


def test_fetch_page_accepts_120_day_window():
    page = make_page([])
    session = FakeSession([FakeResponse(json_data=page)])
    client = NVDClient(session=session, sleep=no_sleep())

    client.fetch_page(
        last_mod_start_date="2024-01-01T00:00:00.000Z",
        last_mod_end_date="2024-04-30T00:00:00.000Z",
    )

    assert session.calls[0]["params"]["lastModStartDate"] == "2024-01-01T00:00:00.000Z"


def test_fetch_page_rejects_end_before_start():
    client = NVDClient(session=FakeSession([]), sleep=no_sleep())

    with pytest.raises(NVDInvalidParametersError):
        client.fetch_page(
            last_mod_start_date="2024-06-01T00:00:00.000Z",
            last_mod_end_date="2024-01-01T00:00:00.000Z",
        )


def test_fetch_page_rejects_negative_start_index():
    client = NVDClient(session=FakeSession([]), sleep=no_sleep())

    with pytest.raises(NVDInvalidParametersError):
        client.fetch_page(start_index=-1)


# ================================================================
# API key header
# ================================================================

def test_fetch_page_sends_api_key_header_when_configured():
    page = make_page([])
    session = FakeSession([FakeResponse(json_data=page)])
    client = NVDClient(session=session, sleep=no_sleep(), api_key="secret-key")

    client.fetch_page()

    assert session.calls[0]["headers"]["apiKey"] == "secret-key"


def test_fetch_page_omits_api_key_header_when_not_configured():
    page = make_page([])
    session = FakeSession([FakeResponse(json_data=page)])
    client = NVDClient(session=session, sleep=no_sleep())

    client.fetch_page()

    assert "apiKey" not in session.calls[0]["headers"]
