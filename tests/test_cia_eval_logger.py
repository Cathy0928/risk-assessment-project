# -*- coding: utf-8 -*-
"""Tests for evaluation/cia_eval_logger.py (the CIA experiment CSV writer)."""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation import cia_eval_logger  # noqa: E402


def test_log_cia_run_writes_header_and_row(tmp_path, monkeypatch):
    csv_path = tmp_path / "cia_rating_results.csv"
    monkeypatch.setattr(cia_eval_logger, "CSV_PATH", str(csv_path))

    asset = {
        "id": 701,
        "asset_name": "Web Server",
        "asset_type": "HW",
        "confidentiality": 3,
        "integrity": 3,
        "availability": 4,
    }
    result = {
        "success": True,
        "data": {
            "confidentiality": {"rating": 4, "reason": "外洩衝擊高"},
            "integrity": {"rating": 3, "reason": "衝擊中等"},
            "availability": {"rating": 5, "reason": "中斷影響營運"},
        },
        "parse_success": True,
        "validation_success": True,
        "retry_count": 0,
        "error_type": None,
        "latency_ms": 123.4,
        "model_id": "gemini-3.1-flash-lite",
        "temperature": 0.1,
        "prompt_version": "v1.0",
    }

    row = cia_eval_logger.log_cia_run(asset=asset, result=result, run_number=1)

    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 1
    assert rows[0]["asset_id"] == "701"
    assert rows[0]["human_c"] == "3"
    assert rows[0]["ai_c"] == "4"
    assert rows[0]["c_reason"] == "外洩衝擊高"
    assert rows[0]["error_type"] == ""
    assert row["asset_id"] == 701


def test_log_cia_run_records_failure_rows_too(tmp_path, monkeypatch):
    csv_path = tmp_path / "cia_rating_results.csv"
    monkeypatch.setattr(cia_eval_logger, "CSV_PATH", str(csv_path))

    asset = {"id": 5, "asset_name": "X", "asset_type": "SW"}
    result = {
        "success": False,
        "data": None,
        "parse_success": False,
        "validation_success": False,
        "retry_count": 1,
        "error_type": "JSON_PARSE_FAILED",
        "latency_ms": 50.0,
        "model_id": "gemini-3.1-flash-lite",
        "temperature": 0.1,
        "prompt_version": "v1.0",
    }

    row = cia_eval_logger.log_cia_run(asset=asset, result=result, run_number=2)

    assert row["ai_c"] is None
    assert row["error_type"] == "JSON_PARSE_FAILED"

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["error_type"] == "JSON_PARSE_FAILED"
    assert rows[0]["run_number"] == "2"


def test_log_cia_run_appends_multiple_rows(tmp_path, monkeypatch):
    csv_path = tmp_path / "cia_rating_results.csv"
    monkeypatch.setattr(cia_eval_logger, "CSV_PATH", str(csv_path))

    asset = {"id": 1, "asset_name": "A", "asset_type": "SW"}
    result = {
        "success": True,
        "data": {
            "confidentiality": {"rating": 1, "reason": "r1"},
            "integrity": {"rating": 1, "reason": "r2"},
            "availability": {"rating": 1, "reason": "r3"},
        },
        "parse_success": True,
        "validation_success": True,
        "retry_count": 0,
        "error_type": None,
        "latency_ms": 10.0,
        "model_id": "gemini-3.1-flash-lite",
        "temperature": 0.1,
        "prompt_version": "v1.0",
    }

    cia_eval_logger.log_cia_run(asset=asset, result=result, run_number=1)
    cia_eval_logger.log_cia_run(asset=asset, result=result, run_number=2)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
