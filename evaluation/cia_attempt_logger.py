# -*- coding: utf-8 -*-
"""Attempt-level CSV logging for CIA model calls.

This logger is additive: the existing execution-level
``cia_rating_results.csv`` remains unchanged.  Callers may override
``CSV_PATH`` in tests or isolated experiment runners.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict


CSV_PATH = os.path.join(os.path.dirname(__file__), "cia_attempt_log.csv")

CSV_COLUMNS = [
    "experiment_id",
    "asset_id",
    "run_number",
    "attempt_number",
    "timestamp",
    "stage",
    "success",
    "http_status_code",
    "provider_error_code",
    "provider_error_message",
    "exception_type",
    "error_category",
    "error_message",
    "provider_retry_delay_seconds",
    "actual_wait_seconds",
    "quota_metric",
    "quota_id",
    "quota_dimensions",
    "quota_value",
    "attempt_latency_ms",
    "parse_success",
    "validation_success",
    "model_id",
    "temperature",
    "prompt_version",
]

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{12,}\b"),
)


def _sanitize_message(value: Any) -> str:
    message = str(value or "")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub(r"\1[REDACTED]" if pattern.groups else "[REDACTED]", message)
    return message[:1000]


def _ensure_header() -> None:
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()


def log_cia_attempt(event: Dict[str, Any]) -> Dict[str, Any]:
    """Append one sanitized API/parse/validation/success attempt event."""
    _ensure_header()
    row = {column: event.get(column) for column in CSV_COLUMNS}
    row["error_message"] = _sanitize_message(row["error_message"])
    row["provider_error_message"] = _sanitize_message(
        row["provider_error_message"]
    )
    row["timestamp"] = row["timestamp"] or datetime.now(timezone.utc).isoformat()
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)
    return row
