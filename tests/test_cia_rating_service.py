# -*- coding: utf-8 -*-
"""
Tests for the new CIA AI-rating mechanism (riskGenie/services/cia_rating_service.py).

Follows this repo's existing convention (see tests/test_risk_phase1.py) of
inserting the project root onto sys.path and never requiring a real
GEMINI_API_KEY -- all Gemini calls are monkeypatched.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from riskGenie.services import cia_rating_service as svc  # noqa: E402
from evaluation import cia_attempt_logger  # noqa: E402


def _asset(**overrides):
    asset = {
        "id": 701,
        "asset_name": "Web Server",
        "asset_type": "HW",
        "data_type": "一般資料",
        "description": "Production web server",
        "confidentiality": 3,
        "integrity": 3,
        "availability": 4,
    }
    asset.update(overrides)
    return asset


def _fake_client(text):
    """Builds a fake Gemini client whose generate_content returns `text`."""
    response = SimpleNamespace(text=text)
    models = SimpleNamespace(generate_content=lambda **_kwargs: response)
    return SimpleNamespace(models=models)


def _fake_client_sequence(texts):
    """Fake client returning a different response text on each successive call."""
    iterator = iter(texts)

    def _generate(**_kwargs):
        return SimpleNamespace(text=next(iterator))

    return SimpleNamespace(models=SimpleNamespace(generate_content=_generate))


VALID_JSON = """{
  "confidentiality": {"rating": 4, "reason": "資產含客戶資料，外洩衝擊高"},
  "integrity": {"rating": 3, "reason": "資料異動需經審核，衝擊中等"},
  "availability": {"rating": 5, "reason": "對外服務中斷會直接影響營運"}
}"""


@pytest.fixture(autouse=True)
def gemini_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        cia_attempt_logger, "CSV_PATH", str(tmp_path / "cia_attempt_log.csv")
    )


# ------------------------------------------------------------------
# 1. Normal, complete asset data
# ------------------------------------------------------------------
def test_normal_complete_asset_succeeds(monkeypatch):
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client(VALID_JSON))

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is True
    assert result.parse_success is True
    assert result.validation_success is True
    assert result.retry_count == 0
    assert result.error_type is None
    assert result.data["confidentiality"]["rating"] == 4
    assert result.data["integrity"]["reason"]
    assert result.data["availability"]["reason"]
    # Each dimension has its OWN reason (not one shared reason).
    reasons = {result.data[d]["reason"] for d in svc.CIA_DIMENSIONS}
    assert len(reasons) == 3


# ------------------------------------------------------------------
# 2. Missing description
# ------------------------------------------------------------------
def test_missing_description_still_builds_prompt_and_succeeds(monkeypatch):
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client(VALID_JSON))
    asset = _asset()
    del asset["description"]

    result = svc.generate_cia_suggestion(asset)

    assert result.success is True


def test_build_asset_context_handles_missing_description():
    context = svc.build_asset_context({"asset_name": "X"})
    assert context["description"] == "未提供"


# ------------------------------------------------------------------
# 3. Missing an optional field that isn't part of the live schema
#    (system_dependency is NOT a real column in riskGenie/app.py's asset
#    insert/update payloads -- see evaluation/CIA_IMPLEMENTATION_NOTES.md --
#    so this test covers the equivalent real case: a missing data_type.)
# ------------------------------------------------------------------
def test_missing_data_type_field_succeeds(monkeypatch):
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client(VALID_JSON))
    asset = _asset()
    del asset["data_type"]

    result = svc.generate_cia_suggestion(asset)

    assert result.success is True


def test_build_asset_context_ignores_unknown_fields():
    context = svc.build_asset_context(
        {"asset_name": "X", "system_dependency": "should be ignored"}
    )
    assert "system_dependency" not in context


# ------------------------------------------------------------------
# 4. Gemini response wrapped in a markdown code fence
# ------------------------------------------------------------------
def test_markdown_code_fence_is_stripped(monkeypatch):
    fenced = "```json\n" + VALID_JSON + "\n```"
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client(fenced))

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is True
    assert result.data["confidentiality"]["rating"] == 4


# ------------------------------------------------------------------
# 5. Missing one of the three CIA keys
# ------------------------------------------------------------------
def test_missing_key_fails_validation(monkeypatch):
    broken = """{
      "confidentiality": {"rating": 4, "reason": "ok"},
      "availability": {"rating": 5, "reason": "ok"}
    }"""
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client_sequence([broken, broken]))

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.parse_success is True
    assert result.validation_success is False
    assert result.error_type == svc.ERROR_MISSING_KEY
    assert result.retry_count == 1  # retried once, per policy


# ------------------------------------------------------------------
# 6. rating value out of range (>5)
# ------------------------------------------------------------------
def test_rating_out_of_range_fails_validation(monkeypatch):
    broken = """{
      "confidentiality": {"rating": 9, "reason": "too high"},
      "integrity": {"rating": 3, "reason": "ok"},
      "availability": {"rating": 4, "reason": "ok"}
    }"""
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client_sequence([broken, broken]))

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.error_type == svc.ERROR_RATING_OUT_OF_RANGE


# ------------------------------------------------------------------
# 7. rating value is a string instead of an integer
# ------------------------------------------------------------------
def test_rating_as_string_fails_validation(monkeypatch):
    broken = """{
      "confidentiality": {"rating": "4", "reason": "stringy"},
      "integrity": {"rating": 3, "reason": "ok"},
      "availability": {"rating": 4, "reason": "ok"}
    }"""
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client_sequence([broken, broken]))

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.error_type == svc.ERROR_RATING_NOT_INT


# ------------------------------------------------------------------
# 8. Empty reason string
# ------------------------------------------------------------------
def test_empty_reason_fails_validation(monkeypatch):
    broken = """{
      "confidentiality": {"rating": 4, "reason": ""},
      "integrity": {"rating": 3, "reason": "ok"},
      "availability": {"rating": 4, "reason": "ok"}
    }"""
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: _fake_client_sequence([broken, broken]))

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.error_type == svc.ERROR_EMPTY_REASON


# ------------------------------------------------------------------
# 9. Simulated API failure
# ------------------------------------------------------------------
def test_simulated_api_failure_is_reported_not_masked(monkeypatch):
    def _raise_client():
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(svc, "_get_gemini_client", _raise_client)

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.error_type == svc.ERROR_API_CALL_FAILED
    assert result.data is None
    assert result.retry_count == 0  # unknown exceptions are not blindly retried


# ------------------------------------------------------------------
# 10. Retry attempted and still fails afterward (JSON always invalid)
# ------------------------------------------------------------------
def test_retry_attempted_and_still_fails(monkeypatch):
    client = _fake_client_sequence(["not json at all", "still not json"])
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: client)

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.parse_success is False
    assert result.validation_success is False
    assert result.error_type == svc.ERROR_JSON_PARSE_FAILED
    assert result.retry_count == 1


def test_retry_succeeds_on_second_attempt(monkeypatch):
    """A first-attempt failure that succeeds on retry should report retry_count=1
    and success=True (retry policy is 'retry once', not 'fail forever')."""
    client = _fake_client_sequence(["not json", VALID_JSON])
    monkeypatch.setattr(svc, "_get_gemini_client", lambda: client)

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is True
    assert result.retry_count == 1


# ------------------------------------------------------------------
# Extra: no silent rule-based fallback on failure, and no call at all
# when Gemini isn't configured.
# ------------------------------------------------------------------
def test_no_call_when_gemini_not_configured(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def fail_if_called():
        pytest.fail("Gemini must not be called without GEMINI_API_KEY.")

    monkeypatch.setattr(svc, "_get_gemini_client", fail_if_called)

    result = svc.generate_cia_suggestion(_asset())

    assert result.success is False
    assert result.error_type == svc.ERROR_NOT_CONFIGURED
    assert result.data is None


def test_validate_cia_payload_rejects_non_dict_dimension():
    payload = {
        "confidentiality": "not a dict",
        "integrity": {"rating": 3, "reason": "ok"},
        "availability": {"rating": 3, "reason": "ok"},
    }
    is_valid, error_type = svc.validate_cia_payload(payload)
    assert is_valid is False
    assert error_type == svc.ERROR_DIMENSION_NOT_OBJECT


def test_validate_cia_payload_accepts_well_formed_payload():
    import json

    parsed = json.loads(VALID_JSON)
    is_valid, error_type = svc.validate_cia_payload(parsed)
    assert is_valid is True
    assert error_type is None


def _api_error(code, status, message="provider error", details=None):
    payload = {
        "error": {
            "code": code,
            "status": status,
            "message": message,
            "details": details or [],
        }
    }
    error_cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return error_cls(code, payload)


def _call_sequence(events):
    iterator = iter(events)

    def call(_prompt):
        event = next(iterator)
        if isinstance(event, BaseException):
            raise event
        return event

    return call


def _attempt_rows():
    import csv

    with open(cia_attempt_logger.CSV_PATH, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_success_writes_attempt_one_without_retry(monkeypatch):
    monkeypatch.setattr(svc, "_call_gemini", lambda _prompt: VALID_JSON)
    result = svc.generate_cia_suggestion(
        _asset(), experiment_id="exp-success", run_number=3
    )

    assert result.success is True
    assert result.retry_count == 0
    rows = _attempt_rows()
    assert [row["stage"] for row in rows] == ["SUCCESS"]
    assert {row["attempt_number"] for row in rows} == {"1"}
    assert {row["experiment_id"] for row in rows} == {"exp-success"}
    assert {row["run_number"] for row in rows} == {"3"}


def test_429_waits_provider_delay_then_succeeds(monkeypatch):
    retry_info = {
        "@type": "type.googleapis.com/google.rpc.RetryInfo",
        "retryDelay": "20.5s",
    }
    quota = {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{
            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
            "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
            "quotaDimensions": {"location": "global", "model": svc.CIA_MODEL_ID},
            "quotaValue": "15",
        }],
    }
    exc = _api_error(429, "RESOURCE_EXHAUSTED", details=[retry_info, quota])
    monkeypatch.setattr(svc, "_call_gemini", _call_sequence([exc, VALID_JSON]))
    waits = []

    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=waits.append, jitter_fn=lambda: 0.2
    )

    assert result.success is True
    assert result.retry_count == 1
    assert waits == [20.5]
    rows = _attempt_rows()
    assert len(rows) == 2
    assert rows[1]["stage"] == "SUCCESS"
    api_rows = [row for row in rows if row["stage"] == "API_CALL"]
    assert len(api_rows) == 1
    assert api_rows[0]["http_status_code"] == "429"
    assert api_rows[0]["provider_error_code"] == "RESOURCE_EXHAUSTED"
    assert api_rows[0]["error_category"] == "RATE_LIMIT_OR_QUOTA"
    assert api_rows[0]["provider_retry_delay_seconds"] == "20.5"
    assert api_rows[0]["actual_wait_seconds"] == "20.5"
    assert api_rows[0]["quota_id"] == "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"


def test_429_twice_logs_both_attempts(monkeypatch):
    exc1 = _api_error(429, "RESOURCE_EXHAUSTED", details=[{"retryDelay": "2s"}])
    exc2 = _api_error(429, "RESOURCE_EXHAUSTED", details=[{"retryDelay": "3s"}])
    monkeypatch.setattr(svc, "_call_gemini", _call_sequence([exc1, exc2]))
    waits = []

    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=waits.append, jitter_fn=lambda: 0.0
    )

    assert result.success is False
    assert result.retry_count == 1
    assert waits == [2.0]
    rows = _attempt_rows()
    assert [(row["attempt_number"], row["success"]) for row in rows] == [
        ("1", "False"), ("2", "False")
    ]
    assert rows[1]["actual_wait_seconds"] == "0.0"


def test_timeout_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(
        svc, "_call_gemini", _call_sequence([TimeoutError("timed out"), VALID_JSON])
    )
    waits = []
    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=waits.append, jitter_fn=lambda: 0.0
    )
    assert result.success is True
    assert result.retry_count == 1
    assert waits == [1.0]
    assert _attempt_rows()[0]["error_category"] == "TIMEOUT"


def test_5xx_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_call_gemini",
        _call_sequence([_api_error(503, "UNAVAILABLE"), VALID_JSON]),
    )
    waits = []
    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=waits.append, jitter_fn=lambda: 0.0
    )
    assert result.success is True
    assert waits == [1.0]
    assert _attempt_rows()[0]["error_category"] == "PROVIDER_SERVER_ERROR"


def test_connection_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_call_gemini",
        _call_sequence([ConnectionError("connection reset"), VALID_JSON]),
    )
    waits = []
    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=waits.append, jitter_fn=lambda: 0.0
    )
    assert result.success is True
    assert result.retry_count == 1
    assert waits == [1.0]
    assert _attempt_rows()[0]["error_category"] == "CONNECTION_ERROR"


@pytest.mark.parametrize("code,status", [(401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")])
def test_authentication_errors_do_not_retry(monkeypatch, code, status):
    calls = []

    def fail(_prompt):
        calls.append(1)
        raise _api_error(code, status)

    monkeypatch.setattr(svc, "_call_gemini", fail)
    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=lambda _seconds: pytest.fail("must not wait")
    )
    assert result.success is False
    assert result.retry_count == 0
    assert len(calls) == 1
    assert _attempt_rows()[0]["error_category"] == "NON_RETRYABLE_CLIENT_ERROR"


def test_other_4xx_does_not_retry(monkeypatch):
    calls = []

    def fail(_prompt):
        calls.append(1)
        raise _api_error(400, "INVALID_ARGUMENT")

    monkeypatch.setattr(svc, "_call_gemini", fail)
    result = svc.generate_cia_suggestion(
        _asset(), wait_fn=lambda _seconds: pytest.fail("must not wait")
    )
    assert result.success is False
    assert result.retry_count == 0
    assert len(calls) == 1
    assert _attempt_rows()[0]["error_category"] == "NON_RETRYABLE_CLIENT_ERROR"


def test_json_parse_failure_is_logged_before_retry_success(monkeypatch):
    monkeypatch.setattr(
        svc, "_call_gemini", _call_sequence(["not json", VALID_JSON])
    )
    result = svc.generate_cia_suggestion(_asset(), jitter_fn=lambda: 0.0)
    assert result.success is True
    assert result.retry_count == 1
    rows = _attempt_rows()
    assert any(
        row["attempt_number"] == "1"
        and row["stage"] == "JSON_PARSE"
        and row["error_category"] == "JSON_PARSE_ERROR"
        for row in rows
    )


def test_schema_validation_failure_is_logged_before_retry_success(monkeypatch):
    invalid = '{"confidentiality": {}, "integrity": {}, "availability": {}}'
    monkeypatch.setattr(
        svc, "_call_gemini", _call_sequence([invalid, VALID_JSON])
    )
    result = svc.generate_cia_suggestion(_asset(), jitter_fn=lambda: 0.0)
    assert result.success is True
    assert result.retry_count == 1
    rows = _attempt_rows()
    assert any(
        row["attempt_number"] == "1"
        and row["stage"] == "SCHEMA_VALIDATION"
        and row["error_category"] == "VALIDATION_ERROR"
        for row in rows
    )


def test_exception_detail_extraction_uses_google_genai_fields():
    exc = _api_error(
        429,
        "RESOURCE_EXHAUSTED",
        message="quota exceeded",
        details=[{"retryDelay": "41.315s"}],
    )
    details = svc.extract_gemini_exception_details(exc)
    assert details["http_status_code"] == 429
    assert details["provider_error_code"] == "RESOURCE_EXHAUSTED"
    assert details["provider_retry_delay_seconds"] == 41.315
    assert details["exception_type"] == "ClientError"
    assert details["provider_error_message"] == "quota exceeded"
