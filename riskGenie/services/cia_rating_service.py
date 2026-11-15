# -*- coding: utf-8 -*-
"""
CIA AI Rating Service (new, additive module for the TANET 2026 paper)
-----------------------------------------------------------------------
Generates an AI *suggestion* for an information asset's Confidentiality (C),
Integrity (I) and Availability (A) rating on the SAME 1-5 scale already used
by the live asset_add / asset_edit forms (see riskGenie/templates/asset_add.html
and asset_edit.html, `{% for i in range(1,6) %}`).

This module NEVER writes to the database and NEVER decides a final CIA value.
It only calls Gemini, validates the structured JSON response, and returns a
result dict describing what happened (including failures). The caller
(riskGenie/services/cia_routes.py) is responsible for showing the suggestion
to a human and only writing to Supabase when the human explicitly adopts it.

Scale definition (this study's own operationalization -- NOT an ISO 27001
requirement, not a quotation of any ISO 27001/27002 clause):

    1 = Very Low (非常低) 2 = Low (低) 3 = Moderate (中等)
    4 = High (高)        5 = Very High (非常高)

    Confidentiality: impact to the organization if this asset's information
        is disclosed without authorization.
    Integrity: impact to the organization if this asset's content is
        modified, corrupted, or contains errors without authorization.
    Availability: impact to the organization if this asset is unavailable or
        its service is interrupted when needed.

Model / SDK choice:
    riskGenie/services/rag_service.py and riskGenie/services/cve_embedding.py
    (both already in production use) call Gemini via the current
    `from google import genai; genai.Client(api_key=...)` SDK, NOT the legacy
    `google.generativeai` SDK used by the disconnected `其他/riskgenie/modules
    /ai_tagger.py` prototype. This module follows the CURRENT/live pattern
    for consistency with the rest of the codebase, and because the legacy
    SDK is only imported as a design-reference dependency, not a live one.
    The exact model id is centralized in CIA_MODEL_ID below (rag_service.py
    currently calls Gemini with model="gemini-3.1-flash-lite" for its
    generate_content calls; this module reuses the same identifier so the
    paper's experiment reports one real, live model id).
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from dotenv import load_dotenv
from google import genai

from evaluation.cia_attempt_logger import log_cia_attempt

load_dotenv()
logger = logging.getLogger(__name__)


# ============================================================
# Centralized model / prompt configuration
# ============================================================

# Exact Gemini model identifier used for every CIA-suggestion call.
# Kept in one place per the paper's reproducibility requirement.
CIA_MODEL_ID = "gemini-3.1-flash-lite"

# Low temperature chosen to reduce rating variability across repeated runs
# on the same asset (needed for the paper's repeatability experiment).
# 0.1 was chosen (lower than rag_service.py's advice-generation temperature
# of 0.2) because CIA rating is a closed, structured classification task
# rather than free-form advice text, so we bias further toward determinism.
CIA_TEMPERATURE = 0.1

# Bump this whenever CIA_PROMPT_TEMPLATE's wording changes, so evaluation
# CSV rows can be traced back to the exact prompt that produced them.
CIA_PROMPT_VERSION = "v1.0"

# Total attempts = 1 initial call + CIA_MAX_RETRIES retries.
# Per the experiment design, a validation failure may be retried ONCE and
# must otherwise be reported as an explicit error -- never silently
# replaced by a rule-based guess.
CIA_MAX_RETRIES = 1

CIA_DIMENSIONS = ("confidentiality", "integrity", "availability")

CIA_SCALE_DEFINITION = {
    1: "Very Low (非常低)",
    2: "Low (低)",
    3: "Moderate (中等)",
    4: "High (高)",
    5: "Very High (非常高)",
}

# Error type strings recorded in the evaluation CSV / returned to callers.
ERROR_NOT_CONFIGURED = "GEMINI_NOT_CONFIGURED"
ERROR_API_CALL_FAILED = "API_CALL_FAILED"
ERROR_JSON_PARSE_FAILED = "JSON_PARSE_FAILED"
ERROR_NOT_A_JSON_OBJECT = "RESPONSE_NOT_JSON_OBJECT"
ERROR_MISSING_KEY = "MISSING_CIA_KEY"
ERROR_DIMENSION_NOT_OBJECT = "DIMENSION_NOT_OBJECT"
ERROR_RATING_NOT_INT = "RATING_NOT_INT"
ERROR_RATING_OUT_OF_RANGE = "RATING_OUT_OF_RANGE"
ERROR_EMPTY_REASON = "EMPTY_REASON"
ERROR_EMPTY_RESPONSE = "EMPTY_RESPONSE"

ERROR_CATEGORY_RATE_LIMIT = "RATE_LIMIT_OR_QUOTA"
ERROR_CATEGORY_TIMEOUT = "TIMEOUT"
ERROR_CATEGORY_CONNECTION = "CONNECTION_ERROR"
ERROR_CATEGORY_PROVIDER_SERVER = "PROVIDER_SERVER_ERROR"
ERROR_CATEGORY_JSON_PARSE = "JSON_PARSE_ERROR"
ERROR_CATEGORY_VALIDATION = "VALIDATION_ERROR"
ERROR_CATEGORY_NON_RETRYABLE_CLIENT = "NON_RETRYABLE_CLIENT_ERROR"
ERROR_CATEGORY_OTHER = "OTHER"

_SENSITIVE_MESSAGE_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{12,}\b"),
)


# ============================================================
# Prompt (single, importable, versioned location)
# ============================================================

CIA_PROMPT_TEMPLATE = """你是一名企業資訊資產盤點的資安顧問。

你的任務是根據下方「資產資訊」，針對機密性(Confidentiality)、
完整性(Integrity)、可用性(Availability) 三個構面，各自給出
1 到 5 分的評分與各自獨立的理由。

這是本系統自訂的評分尺度（非引用任何 ISO 27001/27002 條文），定義如下：
1 = 非常低 (Very Low)
2 = 低 (Low)
3 = 中等 (Moderate)
4 = 高 (High)
5 = 非常高 (Very High)

三個構面的定義：
- 機密性(C)：此資產資訊遭未經授權揭露時，對組織造成的衝擊程度。
- 完整性(I)：此資產內容遭未經授權竄改、損毀或發生錯誤時，對組織造成的衝擊程度。
- 可用性(A)：此資產無法使用或服務中斷時，對組織造成的衝擊程度。

==================================================
【資產資訊】
==================================================

資產名稱：{asset_name}
資產類型：{asset_type}
資料類型：{data_type}
資產描述：{description}

==================================================
【輸出格式要求】
==================================================

請「只」回傳一個 JSON 物件，不要加上任何說明文字、不要使用 Markdown
程式碼區塊(```)，格式必須「完全」符合下列結構：

{{
  "confidentiality": {{"rating": 1-5 之間的整數, "reason": "獨立的理由文字"}},
  "integrity": {{"rating": 1-5 之間的整數, "reason": "獨立的理由文字"}},
  "availability": {{"rating": 1-5 之間的整數, "reason": "獨立的理由文字"}}
}}

規則：
1. rating 必須是 1 到 5 之間的整數，不可以是字串、小數或超出範圍的數字。
2. 三個構面「各自」必須有自己獨立的 reason，禁止三個構面共用同一段理由。
3. reason 不可以是空字串。
4. 如果資產描述、資產類型或資料類型未提供，仍必須根據可得資訊給出最合理的
   評分與理由，並可在理由中說明資訊不足。
"""


def build_asset_context(asset: Dict[str, Any]) -> Dict[str, str]:
    """Extract only the fields that actually exist and are populated in the
    live asset_add / asset_edit flow (see riskGenie/app.py `asset` /
    `update_data` dicts): asset_name, asset_type, data_type, description.

    NOTE: The disconnected legacy prototype (其他/riskgenie/modules/
    ai_tagger.py) also reads `owner` and `system_dependency`, but neither
    column is written by the live asset_add/asset_edit routes or present in
    riskGenie/app.py's `asset` / `update_data` payloads, so they are
    intentionally NOT used here (see evaluation/CIA_IMPLEMENTATION_NOTES.md).
    Missing/blank values are treated as "未提供" (not provided) so the
    prompt never breaks on incomplete data.
    """

    def _clean(value):
        if not isinstance(value, str):
            return ""
        value = value.strip()
        if value in ("", "-", "無", "無資料", "None", "null"):
            return ""
        return value

    return {
        "asset_name": _clean(asset.get("asset_name")) or "未提供",
        "asset_type": _clean(asset.get("asset_type")) or "未提供",
        "data_type": _clean(asset.get("data_type")) or "未提供",
        "description": _clean(asset.get("description")) or "未提供",
    }


def build_cia_prompt(asset: Dict[str, Any]) -> str:
    context = build_asset_context(asset)
    return CIA_PROMPT_TEMPLATE.format(**context)


# ============================================================
# Gemini client (same pattern as rag_service.py / cve_embedding.py)
# ============================================================

def is_gemini_configured() -> bool:
    api_key = os.getenv("GEMINI_API_KEY")
    return bool(api_key and api_key.strip())


def _get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        raise RuntimeError("Gemini is not configured.")
    return genai.Client(api_key=api_key)


def _call_gemini(prompt: str) -> str:
    client = _get_gemini_client()
    response = client.models.generate_content(
        model=CIA_MODEL_ID,
        contents=prompt,
        config={
            "temperature": CIA_TEMPERATURE,
        },
    )
    if not response or not getattr(response, "text", None):
        raise ValueError("Gemini 沒有回傳內容")
    return response.text


def _sanitize_error_message(value: Any, max_length: int = 1000) -> str:
    """Return a bounded diagnostic message with common secret forms redacted."""
    message = str(value or "")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    for pattern in _SENSITIVE_MESSAGE_PATTERNS:
        if pattern.groups:
            message = pattern.sub(r"\1[REDACTED]", message)
        else:
            message = pattern.sub("[REDACTED]", message)
    return message[:max_length]


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _parse_seconds(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)s\s*", value)
        if match:
            return float(match.group(1))
    return None


def extract_gemini_exception_details(exc: Exception) -> Dict[str, Any]:
    """Extract stable google-genai APIError fields with conservative fallbacks."""
    details = getattr(exc, "details", None)
    http_status = getattr(exc, "code", None)
    if not isinstance(http_status, int):
        http_status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if not isinstance(http_status, int) and response is not None:
        http_status = getattr(response, "status_code", None)

    provider_error_code = getattr(exc, "status", None)
    message = getattr(exc, "message", None) or str(exc)
    retry_delay = None
    quota_metric = None
    quota_id = None
    quota_dimensions = None
    quota_value = None

    for item in _walk_dicts(details):
        provider_error_code = provider_error_code or item.get("status")
        if retry_delay is None:
            retry_delay = _parse_seconds(
                item.get("retryDelay", item.get("retry_delay"))
            )
        violations = item.get("violations")
        if isinstance(violations, list) and violations:
            violation = violations[0] if isinstance(violations[0], dict) else {}
            quota_metric = quota_metric or violation.get("quotaMetric")
            quota_id = quota_id or violation.get("quotaId")
            quota_dimensions = quota_dimensions or violation.get("quotaDimensions")
            quota_value = quota_value or violation.get("quotaValue")

    if not isinstance(http_status, int):
        status_match = re.search(r"\b([45]\d\d)\b", str(exc))
        http_status = int(status_match.group(1)) if status_match else None
    if not provider_error_code:
        for code in ("RESOURCE_EXHAUSTED", "UNAUTHENTICATED", "PERMISSION_DENIED"):
            if code in str(exc):
                provider_error_code = code
                break
    if retry_delay is None:
        delay_match = re.search(
            r"(?:retryDelay['\"\s:=]+|retry in\s+)(\d+(?:\.\d+)?)s",
            str(exc),
            re.IGNORECASE,
        )
        retry_delay = float(delay_match.group(1)) if delay_match else None

    class_name = type(exc).__name__
    class_name_lower = class_name.lower()
    message_lower = str(message).lower()
    if http_status == 429 or provider_error_code == "RESOURCE_EXHAUSTED":
        category, retryable = ERROR_CATEGORY_RATE_LIMIT, True
    elif isinstance(exc, TimeoutError) or "timeout" in class_name_lower:
        category, retryable = ERROR_CATEGORY_TIMEOUT, True
    elif isinstance(exc, ConnectionError) or any(
        token in class_name_lower for token in ("connection", "network", "transport")
    ):
        category, retryable = ERROR_CATEGORY_CONNECTION, True
    elif http_status in (401, 403) or provider_error_code in (
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
    ):
        category, retryable = ERROR_CATEGORY_NON_RETRYABLE_CLIENT, False
    elif isinstance(http_status, int) and 500 <= http_status < 600:
        category, retryable = ERROR_CATEGORY_PROVIDER_SERVER, True
    elif isinstance(http_status, int) and 400 <= http_status < 500:
        category, retryable = ERROR_CATEGORY_NON_RETRYABLE_CLIENT, False
    elif "timed out" in message_lower:
        category, retryable = ERROR_CATEGORY_TIMEOUT, True
    else:
        category, retryable = ERROR_CATEGORY_OTHER, False

    return {
        "http_status_code": http_status,
        "provider_error_code": provider_error_code,
        "exception_type": class_name,
        "error_category": category,
        "provider_error_message": _sanitize_error_message(message),
        "provider_retry_delay_seconds": retry_delay,
        "quota_metric": quota_metric,
        "quota_id": quota_id,
        "quota_dimensions": quota_dimensions,
        "quota_value": quota_value,
        "retryable": retryable,
    }


def _retry_wait_seconds(
    details: Dict[str, Any], attempt_index: int, jitter_fn: Callable[[], float]
) -> float:
    provider_delay = details.get("provider_retry_delay_seconds")
    if provider_delay is not None:
        return max(0.0, float(provider_delay))
    base = min(2.0 ** attempt_index, 8.0)
    return max(0.0, base + max(0.0, float(jitter_fn())))


def _write_attempt_event(event: Dict[str, Any]) -> None:
    """Keep observability failure from changing the rating outcome."""
    try:
        log_cia_attempt(event)
    except Exception:
        logger.exception("Failed to write CIA attempt log")


# ============================================================
# Parsing + validation
# ============================================================

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_code_fence(raw_text: str) -> str:
    return _CODE_FENCE_RE.sub("", raw_text).strip()


def parse_cia_response(raw_text: str):
    """Returns (parsed_dict_or_None, error_type_or_None)."""
    if not raw_text or not raw_text.strip():
        return None, ERROR_EMPTY_RESPONSE

    cleaned = _strip_code_fence(raw_text)

    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, ERROR_JSON_PARSE_FAILED

    if not isinstance(parsed, dict):
        return None, ERROR_NOT_A_JSON_OBJECT

    return parsed, None


def validate_cia_payload(parsed: Dict[str, Any]):
    """Validates the parsed JSON against all required rules.

    Returns (is_valid, error_type_or_None).
    Checks (per spec): all three dimension keys present; rating is an int;
    rating is in [1, 5]; reason is a non-empty string.
    """
    if not isinstance(parsed, dict):
        return False, ERROR_NOT_A_JSON_OBJECT

    for dimension in CIA_DIMENSIONS:
        if dimension not in parsed:
            return False, ERROR_MISSING_KEY

        entry = parsed[dimension]
        if not isinstance(entry, dict):
            return False, ERROR_DIMENSION_NOT_OBJECT

        rating = entry.get("rating")
        # bool is a subclass of int in Python; explicitly excluded.
        if not isinstance(rating, int) or isinstance(rating, bool):
            return False, ERROR_RATING_NOT_INT

        if rating < 1 or rating > 5:
            return False, ERROR_RATING_OUT_OF_RANGE

        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, ERROR_EMPTY_REASON

    return True, None


@dataclass
class CiaSuggestionResult:
    success: bool
    data: Optional[Dict[str, Any]]
    parse_success: bool
    validation_success: bool
    retry_count: int
    error_type: Optional[str]
    latency_ms: float
    raw_text: Optional[str] = None
    model_id: str = CIA_MODEL_ID
    temperature: float = CIA_TEMPERATURE
    prompt_version: str = CIA_PROMPT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "parse_success": self.parse_success,
            "validation_success": self.validation_success,
            "retry_count": self.retry_count,
            "error_type": self.error_type,
            "latency_ms": self.latency_ms,
            "raw_text": self.raw_text,
            "model_id": self.model_id,
            "temperature": self.temperature,
            "prompt_version": self.prompt_version,
        }


def generate_cia_suggestion(
    asset: Dict[str, Any],
    *,
    experiment_id: Optional[str] = None,
    run_number: int = 1,
    wait_fn: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[], float] = lambda: random.uniform(0.0, 0.25),
) -> CiaSuggestionResult:
    """Calls Gemini to obtain a CIA suggestion for one asset.

    Behavior on failure: retryable provider, transport, parsing, and schema
    failures are retried at most once (CIA_MAX_RETRIES). Non-retryable
    authentication and invalid-request failures stop after the first attempt.
    Every terminal failure returns success=False with an explicit error_type.
    It NEVER falls back to a rule-based guess -- that would contaminate the
    paper's reported LLM failure rate.
    """
    if not is_gemini_configured():
        return CiaSuggestionResult(
            success=False,
            data=None,
            parse_success=False,
            validation_success=False,
            retry_count=0,
            error_type=ERROR_NOT_CONFIGURED,
            latency_ms=0.0,
        )

    prompt = build_cia_prompt(asset)
    start = time.perf_counter()

    retry_count = 0
    last_raw_text = None
    last_error_type = None
    last_parse_success = False

    def event_base(attempt_number: int) -> Dict[str, Any]:
        return {
            "experiment_id": experiment_id,
            "asset_id": asset.get("id"),
            "run_number": run_number,
            "attempt_number": attempt_number,
            "model_id": CIA_MODEL_ID,
            "temperature": CIA_TEMPERATURE,
            "prompt_version": CIA_PROMPT_VERSION,
        }

    for attempt_index in range(CIA_MAX_RETRIES + 1):
        attempt_number = attempt_index + 1
        if attempt_index > 0:
            retry_count += 1

        attempt_start = time.perf_counter()
        try:
            raw_text = _call_gemini(prompt)
        except Exception as exc:  # network / SDK / auth errors
            attempt_latency_ms = (time.perf_counter() - attempt_start) * 1000
            details = extract_gemini_exception_details(exc)
            has_next_attempt = attempt_index < CIA_MAX_RETRIES
            actual_wait_seconds = 0.0
            if details["retryable"] and has_next_attempt:
                actual_wait_seconds = _retry_wait_seconds(
                    details, attempt_index, jitter_fn
                )
            event = {
                **event_base(attempt_number),
                "stage": "API_CALL",
                "success": False,
                **{k: v for k, v in details.items() if k != "retryable"},
                "actual_wait_seconds": actual_wait_seconds,
                "attempt_latency_ms": attempt_latency_ms,
                "parse_success": False,
                "validation_success": False,
            }
            if isinstance(event.get("quota_dimensions"), dict):
                event["quota_dimensions"] = json.dumps(
                    event["quota_dimensions"], ensure_ascii=False, sort_keys=True
                )
            _write_attempt_event(event)
            logger.warning(
                "CIA Gemini call failed (attempt %s, category=%s, status=%s)",
                attempt_number,
                details["error_category"],
                details["http_status_code"],
            )
            last_error_type = ERROR_API_CALL_FAILED
            last_raw_text = None
            last_parse_success = False
            if details["retryable"] and has_next_attempt:
                wait_fn(actual_wait_seconds)
                continue
            break

        last_raw_text = raw_text
        parsed, parse_error = parse_cia_response(raw_text)

        if parsed is None:
            last_error_type = parse_error
            last_parse_success = False
            _write_attempt_event({
                **event_base(attempt_number),
                "stage": "JSON_PARSE",
                "success": False,
                "error_category": ERROR_CATEGORY_JSON_PARSE,
                "error_message": parse_error,
                "actual_wait_seconds": 0.0,
                "attempt_latency_ms": (time.perf_counter() - attempt_start) * 1000,
                "parse_success": False,
                "validation_success": False,
            })
            continue

        last_parse_success = True
        is_valid, validation_error = validate_cia_payload(parsed)
        if not is_valid:
            last_error_type = validation_error
            _write_attempt_event({
                **event_base(attempt_number),
                "stage": "SCHEMA_VALIDATION",
                "success": False,
                "error_category": ERROR_CATEGORY_VALIDATION,
                "error_message": validation_error,
                "actual_wait_seconds": 0.0,
                "attempt_latency_ms": (time.perf_counter() - attempt_start) * 1000,
                "parse_success": True,
                "validation_success": False,
            })
            continue

        _write_attempt_event({
            **event_base(attempt_number),
            "stage": "SUCCESS",
            "success": True,
            "actual_wait_seconds": 0.0,
            "attempt_latency_ms": (time.perf_counter() - attempt_start) * 1000,
            "parse_success": True,
            "validation_success": True,
        })

        latency_ms = (time.perf_counter() - start) * 1000
        return CiaSuggestionResult(
            success=True,
            data=parsed,
            parse_success=True,
            validation_success=True,
            retry_count=retry_count,
            error_type=None,
            latency_ms=latency_ms,
            raw_text=raw_text,
        )

    latency_ms = (time.perf_counter() - start) * 1000
    return CiaSuggestionResult(
        success=False,
        data=None,
        parse_success=last_parse_success,
        validation_success=False,
        retry_count=retry_count,
        error_type=last_error_type,
        latency_ms=latency_ms,
        raw_text=last_raw_text,
    )
