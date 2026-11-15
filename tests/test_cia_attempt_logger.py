# -*- coding: utf-8 -*-
"""Tests for the additive CIA attempt-level CSV logger."""

import csv

from evaluation import cia_attempt_logger


def test_attempt_logger_contains_required_schema():
    required = {
        "experiment_id", "asset_id", "run_number", "attempt_number",
        "timestamp", "stage", "success", "exception_type",
        "http_status_code", "provider_error_code", "provider_error_message",
        "error_category", "provider_retry_delay_seconds",
        "actual_wait_seconds", "attempt_latency_ms", "parse_success",
        "validation_success", "model_id", "temperature", "prompt_version",
    }
    assert required <= set(cia_attempt_logger.CSV_COLUMNS)


def test_attempt_logger_schema_and_secret_redaction(tmp_path, monkeypatch):
    path = tmp_path / "cia_attempt_log.csv"
    monkeypatch.setattr(cia_attempt_logger, "CSV_PATH", str(path))
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key-value")

    cia_attempt_logger.log_cia_attempt({
        "experiment_id": "exp-1",
        "asset_id": "ASSET-001",
        "run_number": 1,
        "attempt_number": 1,
        "stage": "API_CALL",
        "success": False,
        "http_status_code": 429,
        "provider_error_code": "RESOURCE_EXHAUSTED",
        "provider_error_message": "api_key=test-secret-key-value",
        "exception_type": "ClientError",
        "error_category": "RATE_LIMIT_OR_QUOTA",
        "error_message": "api_key=test-secret-key-value token=another-secret",
        "provider_retry_delay_seconds": 20,
        "actual_wait_seconds": 20.1,
        "attempt_latency_ms": 42.5,
        "parse_success": False,
        "validation_success": False,
        "model_id": "gemini-3.1-flash-lite",
        "temperature": 0.1,
        "prompt_version": "v1.0",
    })

    text = path.read_text(encoding="utf-8-sig")
    assert "test-secret-key-value" not in text
    assert "another-secret" not in text
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert reader.fieldnames == cia_attempt_logger.CSV_COLUMNS
    assert rows[0]["stage"] == "API_CALL"
    assert "[REDACTED]" in rows[0]["error_message"]
    assert "[REDACTED]" in rows[0]["provider_error_message"]
