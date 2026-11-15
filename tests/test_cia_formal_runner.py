import csv
import hashlib
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from evaluation import cia_attempt_logger, cia_eval_logger
from evaluation import run_cia_formal_experiment as runner
from riskGenie.services import cia_rating_service as service


class FakeResult:
    success = True
    parse_success = True
    validation_success = True
    retry_count = 0
    latency_ms = 125.0
    error_type = None
    model_id = service.CIA_MODEL_ID
    temperature = service.CIA_TEMPERATURE
    prompt_version = service.CIA_PROMPT_VERSION
    data = {
        "confidentiality": {"rating": 3, "reason": "c"},
        "integrity": {"rating": 3, "reason": "i"},
        "availability": {"rating": 3, "reason": "a"},
    }

    def to_dict(self):
        return {
            key: getattr(self, key)
            for key in (
                "success",
                "parse_success",
                "validation_success",
                "retry_count",
                "latency_ms",
                "error_type",
                "model_id",
                "temperature",
                "prompt_version",
                "data",
            )
        }


def test_frozen_dataset_loads_exactly_102_ordered_cases():
    cases = runner.load_frozen_cases()
    assert len(cases) == 102
    assert [case["id"] for case in cases] == [
        f"ASSET-{number:03d}" for number in range(1, 103)
    ]
    assert all(
        set(case) == {
            "id",
            "asset_name",
            "asset_type",
            "data_type",
            "description",
        }
        for case in cases
    )
    assert runner.EXPECTED_EXECUTION_COUNT == 306


def test_dataset_hash_matches_frozen_manifest():
    assert runner._sha256(runner.DATASET_PATH) == runner.EXPECTED_DATASET_SHA256
    assert len(runner.load_frozen_cases()) == 102


def test_hash_mismatch_refuses_execution_before_generation(tmp_path):
    altered = tmp_path / "altered.xlsx"
    altered.write_bytes(runner.DATASET_PATH.read_bytes() + b"tampered")
    calls = []

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        runner.run_formal_experiment(
            dataset_path=altered,
            generate_fn=lambda *args, **kwargs: calls.append((args, kwargs)),
            summary_dir=tmp_path,
        )
    assert calls == []


def test_loader_rejects_reference_cia_columns(tmp_path):
    path = tmp_path / "with_reference.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cases"
    sheet.append([
        "case_id",
        "asset_name",
        "asset_type",
        "data_type",
        "description",
        "confidentiality",
    ])
    for number in range(1, 103):
        sheet.append([
            f"ASSET-{number:03d}",
            f"Asset {number}",
            "SW",
            "一般資料",
            "Offline test description.",
            5,
        ])
    workbook.save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="forbidden CIA/reference fields"):
        runner.load_frozen_cases(path, expected_sha256=digest)


def test_loader_opens_only_frozen_dataset_not_human_template(monkeypatch):
    opened = []
    real_loader = runner.load_workbook

    def tracking_loader(filename, *args, **kwargs):
        opened.append(Path(filename).resolve())
        return real_loader(filename, *args, **kwargs)

    monkeypatch.setattr(runner, "load_workbook", tracking_loader)
    runner.load_frozen_cases()
    assert opened == [runner.DATASET_PATH.resolve()]


def test_formal_runner_306_ordered_executions_and_isolated_logs(
    tmp_path, monkeypatch
):
    execution_path = tmp_path / "executions.csv"
    attempt_path = tmp_path / "attempts.csv"
    monkeypatch.setattr(cia_eval_logger, "CSV_PATH", str(execution_path))
    monkeypatch.setattr(cia_attempt_logger, "CSV_PATH", str(attempt_path))
    calls = []
    sleeps = []

    def fake_generate(asset, *, experiment_id, run_number):
        calls.append((
            asset["id"],
            run_number,
            experiment_id,
            tuple(sorted(key for key in asset if key != "id")),
        ))
        cia_attempt_logger.log_cia_attempt({
            "experiment_id": experiment_id,
            "asset_id": asset["id"],
            "run_number": run_number,
            "attempt_number": 1,
            "stage": "SUCCESS",
            "success": True,
            "attempt_latency_ms": 125.0,
            "model_id": service.CIA_MODEL_ID,
            "temperature": service.CIA_TEMPERATURE,
            "prompt_version": service.CIA_PROMPT_VERSION,
        })
        return FakeResult()

    result = runner.run_formal_experiment(
        request_interval_seconds=0,
        generate_fn=fake_generate,
        execution_logger=cia_eval_logger.log_cia_run,
        sleep_fn=sleeps.append,
        now_fn=iter(["start", "end"]).__next__,
        summary_dir=tmp_path,
    )

    experiment_id = result["metadata"]["experiment_id"]
    expected = [
        (f"ASSET-{number:03d}", run, experiment_id)
        for number in range(1, 103)
        for run in range(1, 4)
    ]
    assert [call[:3] for call in calls] == expected
    assert len(calls) == 306
    assert sleeps == [0] * 305
    assert {
        call[3] for call in calls
    } == {tuple(sorted(runner.MODEL_INPUT_FIELDS))}

    with execution_path.open(encoding="utf-8-sig", newline="") as source:
        execution_rows = list(csv.DictReader(source))
    with attempt_path.open(encoding="utf-8-sig", newline="") as source:
        attempt_rows = list(csv.DictReader(source))
    assert len(execution_rows) == len(attempt_rows) == 306

    metadata = result["metadata"]
    assert metadata["dataset_version"] == "cia_cases_v3_102"
    assert metadata["dataset_sha256"] == runner.EXPECTED_DATASET_SHA256
    assert metadata["case_count"] == 102
    assert metadata["runs_per_case"] == 3
    assert metadata["evaluation_execution_count"] == 306
    assert metadata["experiment_id"].startswith("formal_")
    assert metadata["experiment_id"] != runner.LEGACY_FORMAL_EXPERIMENT_ID
    assert metadata["model_id"] == service.CIA_MODEL_ID
    assert metadata["temperature"] == service.CIA_TEMPERATURE
    assert metadata["prompt_version"] == service.CIA_PROMPT_VERSION
    assert metadata["max_retries"] == service.CIA_MAX_RETRIES
    assert metadata["start_timestamp"] == "start"
    assert metadata["end_timestamp"] == "end"
    assert metadata["logger_version"] == runner.LOGGER_VERSION
    assert metadata["api_attempt_count"] == 306
    assert metadata["success_count"] == 306
    assert metadata["failure_count"] == 0

    saved = json.loads(
        Path(result["summary_path"]).read_text(encoding="utf-8")
    )
    assert saved["metadata"] == metadata
    assert len(saved["executions"]) == 306
    assert "key" not in json.dumps(saved).lower()


def test_retry_wait_remains_inside_service_and_outside_pacing(tmp_path):
    sleeps = []
    logged = []
    retry_result = FakeResult()
    retry_result.retry_count = 1
    retry_result.latency_ms = 2125.0

    result = runner.run_formal_experiment(
        experiment_id="formal_retry_test",
        cases=runner.FORMAL_CASES[:1],
        offline_test_mode=True,
        runs_per_case=2,
        request_interval_seconds=5.0,
        generate_fn=lambda *args, **kwargs: retry_result,
        execution_logger=lambda *args, **kwargs: logged.append((args, kwargs)),
        sleep_fn=sleeps.append,
        now_fn=iter(["start", "end"]).__next__,
        summary_dir=tmp_path,
    )
    assert sleeps == [5.0]
    assert [item["pacing_wait_ms"] for item in result["executions"]] == [
        0.0,
        5000.0,
    ]
    assert [item["latency_ms"] for item in result["executions"]] == [
        2125.0,
        2125.0,
    ]
    assert result["metadata"]["api_attempt_count"] == 4


def test_old_formal_output_is_never_overwritten(tmp_path):
    old_path = (
        tmp_path
        / f"cia_{runner.LEGACY_FORMAL_EXPERIMENT_ID}_summary.json"
    )
    old_path.write_text('{"preserved": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="reserved"):
        runner.run_formal_experiment(
            experiment_id=runner.LEGACY_FORMAL_EXPERIMENT_ID,
            cases=(),
            offline_test_mode=True,
            generate_fn=lambda *args, **kwargs: FakeResult(),
            summary_dir=tmp_path,
        )
    assert old_path.read_text(encoding="utf-8") == '{"preserved": true}\n'

    result = runner.run_formal_experiment(
        experiment_id="formal_new_offline",
        cases=(),
        offline_test_mode=True,
        generate_fn=lambda *args, **kwargs: FakeResult(),
        now_fn=iter(["start", "end"]).__next__,
        summary_dir=tmp_path,
    )
    assert Path(result["summary_path"]).name == (
        "cia_formal_new_offline_summary.json"
    )
    assert old_path.read_text(encoding="utf-8") == '{"preserved": true}\n'
