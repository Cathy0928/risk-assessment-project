# -*- coding: utf-8 -*-
"""
CSV logger for the new CIA AI-rating mechanism's experiment results.

This is intentionally SEPARATE from evaluation/run_evaluation.py (which
tests the unrelated RiskEngine risk-score formula and CVE-source
verification) and does not touch the production Supabase schema. Every
call to log_cia_run() appends exactly one row to
evaluation/cia_rating_results.csv, creating the file with a header row the
first time it is used. Attempt diagnostics belong to cia_attempt_logger.py;
this execution-level schema is intentionally kept stable for existing
analysis consumers.
"""

from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

CSV_PATH = os.path.join(os.path.dirname(__file__), "cia_rating_results.csv")

CSV_COLUMNS = [
    "experiment_id",
    "asset_id",
    "asset_name",
    "asset_type",
    "human_c",
    "human_i",
    "human_a",
    "ai_c",
    "ai_i",
    "ai_a",
    "c_reason",
    "i_reason",
    "a_reason",
    "model_id",
    "temperature",
    "prompt_version",
    "run_number",
    "parse_success",
    "validation_success",
    "retry_count",
    "error_type",
    "latency_ms",
    "timestamp",
]


def _ensure_header():
    if not os.path.exists(CSV_PATH) or os.path.getsize(CSV_PATH) == 0:
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def log_cia_run(
    asset: Dict[str, Any],
    result,
    run_number: int = 1,
    experiment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one row describing a single generate_cia_suggestion() call.

    `result` is a cia_rating_service.CiaSuggestionResult (or any object /
    dict exposing the same fields: success, data, parse_success,
    validation_success, retry_count, error_type, latency_ms, model_id,
    temperature, prompt_version).

    Returns the row dict that was written, for callers that also want to
    display or assert on it (e.g. tests).
    """
    _ensure_header()

    if hasattr(result, "to_dict"):
        result = result.to_dict()

    data = result.get("data") or {}
    c = data.get("confidentiality") or {}
    i = data.get("integrity") or {}
    a = data.get("availability") or {}

    row = {
        "experiment_id": experiment_id or uuid.uuid4().hex[:12],
        "asset_id": asset.get("id"),
        "asset_name": asset.get("asset_name"),
        "asset_type": asset.get("asset_type"),
        "human_c": asset.get("confidentiality"),
        "human_i": asset.get("integrity"),
        "human_a": asset.get("availability"),
        "ai_c": c.get("rating"),
        "ai_i": i.get("rating"),
        "ai_a": a.get("rating"),
        "c_reason": c.get("reason"),
        "i_reason": i.get("reason"),
        "a_reason": a.get("reason"),
        "model_id": result.get("model_id"),
        "temperature": result.get("temperature"),
        "prompt_version": result.get("prompt_version"),
        "run_number": run_number,
        "parse_success": result.get("parse_success"),
        "validation_success": result.get("validation_success"),
        "retry_count": result.get("retry_count"),
        "error_type": result.get("error_type"),
        "latency_ms": result.get("latency_ms"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writerow(row)

    return row
