"""Tests for the CIA formal-experiment analyzers. All data here is synthetic."""

import csv
import random

import pytest
from openpyxl import Workbook

from evaluation import analyze_cia_formal_v2 as formal
from evaluation import analyze_human_ai_agreement as agreement

EXP = "formal_synthetic"
CASES = tuple(f"ASSET-{n:03d}" for n in range(1, 103))


# ------------------------------------------------------------- fixtures

def exec_row(case, run, c=3, i=3, a=3, *, valid=True, retry=0, latency=2000.0, exp=EXP, error=""):
    return {
        "experiment_id": exp, "asset_id": case, "run_number": str(run),
        "ai_c": str(c) if valid else "", "ai_i": str(i) if valid else "", "ai_a": str(a) if valid else "",
        "model_id": "m", "temperature": "0.1", "prompt_version": "v1.0",
        "parse_success": "True", "validation_success": str(valid),
        "retry_count": str(retry), "error_type": "" if valid else (error or "API_CALL_FAILED"),
        "latency_ms": str(latency),
    }


def attempt_row(case, run, attempt=1, *, success=True, stage="SUCCESS", category="", status="",
                provider_code="", wait=0.0, latency=1500.0, exp=EXP):
    return {
        "experiment_id": exp, "asset_id": case, "run_number": str(run), "attempt_number": str(attempt),
        "stage": stage, "success": str(success), "http_status_code": str(status),
        "provider_error_code": provider_code, "error_category": category,
        "actual_wait_seconds": str(wait), "attempt_latency_ms": str(latency),
    }


def full_experiment(ratings=None):
    """102 cases x 3 runs, all valid, one SUCCESS attempt each."""
    executions, attempts = [], []
    for case in CASES:
        for run in (1, 2, 3):
            c, i, a = (ratings or {}).get((case, run), (3, 3, 3))
            executions.append(exec_row(case, run, c, i, a))
            attempts.append(attempt_row(case, run))
    return executions, attempts


def analyze(executions, attempts, **kwargs):
    return formal.analyze_formal(executions, attempts, experiment_id=EXP, **kwargs)


def metric(result, name):
    return next(m["value"] for m in result["metrics"] if m["metric"] == name)


def stability(result, dim):
    return next(r for r in result["stability_rows"] if r["dimension"] == dim)


def case_row(result, case):
    return next(r for r in result["case_rows"] if r["case_id"] == case)


# --------------------------------------------------- formal analyzer (RQ1/RQ2)

def test_normal_306_executions():
    executions, attempts = full_experiment()
    result = analyze(executions, attempts)
    assert metric(result, "planned_executions") == 306
    assert metric(result, "actual_executions") == 306
    assert metric(result, "valid_executions") == 306
    assert metric(result, "final_failures") == 0
    assert metric(result, "valid_output_rate_pct_of_planned") == 100.0
    assert metric(result, "complete_case_N") == 102
    assert result["integrity_ok"] is True and result["issues"] == []
    for dim in "CIA":
        row = stability(result, dim)
        assert row["identical_count"] == 102 and row["identical_pct"] == 100.0 and row["max_range"] == 0


def test_missing_execution_is_reported_not_hidden():
    executions, attempts = full_experiment()
    executions = [r for r in executions if not (r["asset_id"] == "ASSET-010" and r["run_number"] == "3")]
    result = analyze(executions, attempts)
    assert metric(result, "actual_executions") == 305
    assert metric(result, "missing_executions") == 1
    assert metric(result, "valid_output_rate_pct_of_planned") == pytest.approx(100 * 305 / 306)
    assert result["integrity_ok"] is False
    assert any(i.startswith("missing_executions") for i in result["issues"])
    assert metric(result, "complete_case_N") == 101


def test_duplicate_execution_counted_once_and_flagged():
    executions, attempts = full_experiment()
    executions.append(dict(executions[0]))
    result = analyze(executions, attempts)
    assert metric(result, "actual_execution_rows_raw") == 307
    assert metric(result, "actual_executions") == 306
    assert metric(result, "duplicate_execution_rows") == 1
    assert metric(result, "valid_executions") == 306
    assert result["integrity_ok"] is False
    assert any(i.startswith("duplicate_execution") for i in result["issues"])


def test_fewer_and_more_than_three_runs_per_case():
    executions, attempts = full_experiment()
    executions = [r for r in executions if not (r["asset_id"] == "ASSET-001" and r["run_number"] == "3")]
    executions.append(exec_row("ASSET-002", 4, 5, 5, 5))
    result = analyze(executions, attempts)
    assert case_row(result, "ASSET-001")["n_valid_runs"] == 2
    assert case_row(result, "ASSET-001")["complete_case"] is False
    assert case_row(result, "ASSET-002")["n_valid_runs"] == 3
    assert case_row(result, "ASSET-002")["complete_case"] is True
    assert case_row(result, "ASSET-002")["median_c"] == 3
    assert any(i.startswith("unexpected_run_number") for i in result["issues"])
    assert metric(result, "complete_case_N") == 101


def test_invalid_rating_is_not_valid_and_flagged():
    executions, attempts = full_experiment()
    for row, value in ((executions[0], "6"), (executions[1], "3.5"), (executions[2], "")):
        row["ai_c"] = value
    result = analyze(executions, attempts)
    assert metric(result, "valid_executions") == 303
    assert metric(result, "final_failures") == 3
    assert case_row(result, "ASSET-001")["complete_case"] is False
    assert sum(i.startswith("invalid_rating_in_success_row") for i in result["issues"]) == 3


def test_failed_execution_excluded_from_stability():
    executions, attempts = full_experiment({("ASSET-005", 1): (4, 4, 4), ("ASSET-005", 2): (3, 3, 3)})
    executions = [r for r in executions if not (r["asset_id"] == "ASSET-005" and r["run_number"] == "3")]
    executions.append(exec_row("ASSET-005", 3, valid=False))
    result = analyze(executions, attempts)
    assert metric(result, "final_failures") == 1
    assert case_row(result, "ASSET-005")["n_valid_runs"] == 2
    assert case_row(result, "ASSET-005")["complete_case"] is False
    assert stability(result, "C")["complete_case_N"] == 101
    assert "ASSET-005" not in stability(result, "C")["variation_case_ids"]


def test_complete_case_n_and_variation_ids():
    ratings = {("ASSET-003", 1): (3, 3, 4), ("ASSET-003", 2): (3, 3, 3), ("ASSET-003", 3): (3, 3, 3),
               ("ASSET-004", 1): (1, 3, 3), ("ASSET-004", 2): (5, 3, 3), ("ASSET-004", 3): (2, 3, 3)}
    executions, attempts = full_experiment(ratings)
    result = analyze(executions, attempts)
    a_row, c_row, i_row = stability(result, "A"), stability(result, "C"), stability(result, "I")
    assert a_row["identical_count"] == 101 and a_row["cases_with_variation"] == 1
    assert a_row["max_range"] == 1 and a_row["range_1"] == 1
    assert a_row["variation_case_ids"] == "ASSET-003(4,3,3)"
    assert c_row["identical_count"] == 101 and c_row["max_range"] == 4 and c_row["range_4"] == 1
    assert i_row["identical_count"] == 102


def test_median_per_case_and_dimension():
    ratings = {("ASSET-001", 1): (1, 5, 4), ("ASSET-001", 2): (5, 5, 3), ("ASSET-001", 3): (2, 5, 3)}
    executions, attempts = full_experiment(ratings)
    row = case_row(analyze(executions, attempts), "ASSET-001")
    assert (row["median_c"], row["median_i"], row["median_a"]) == (2, 5, 3)
    assert (row["range_c"], row["range_i"], row["range_a"]) == (4, 0, 1)


def test_latency_excludes_retry_wait_and_pacing():
    executions, attempts = full_experiment()
    executions[0] = exec_row("ASSET-001", 1, retry=1, latency=5000.0)
    attempts = [a for a in attempts if not (a["asset_id"] == "ASSET-001" and a["run_number"] == "1")]
    attempts += [
        attempt_row("ASSET-001", 1, 1, success=False, stage="API_CALL", category="RATE_LIMIT_OR_QUOTA",
                    status=429, provider_code="RESOURCE_EXHAUSTED", wait=2.0, latency=900.0),
        attempt_row("ASSET-001", 1, 2),
    ]
    result = analyze(executions, attempts)
    assert metric(result, "latency_execution_raw_ms_max") == 5000.0
    assert metric(result, "latency_api_only_ms_max") == 3000.0
    assert metric(result, "latency_api_only_ms_min") == 2000.0
    assert metric(result, "latency_api_only_ms_median") == 2000.0
    assert metric(result, "latency_api_only_ms_sd") is not None


def test_retry_and_error_counts():
    executions, attempts = full_experiment()
    executions[0] = exec_row("ASSET-001", 1, retry=1)
    executions[1] = exec_row("ASSET-001", 2, valid=False, retry=1)
    executions[2] = exec_row("ASSET-001", 3, valid=False, retry=1, error="JSON_PARSE_FAILED")
    attempts = [a for a in attempts if a["asset_id"] != "ASSET-001"]
    attempts += [
        attempt_row("ASSET-001", 1, 1, success=False, stage="API_CALL", category="RATE_LIMIT_OR_QUOTA",
                    status=429, provider_code="RESOURCE_EXHAUSTED", wait=1.0),
        attempt_row("ASSET-001", 1, 2),
        attempt_row("ASSET-001", 2, 1, success=False, stage="API_CALL", category="TIMEOUT", wait=1.0),
        attempt_row("ASSET-001", 2, 2, success=False, stage="API_CALL", category="PROVIDER_SERVER_ERROR", status=503),
        attempt_row("ASSET-001", 3, 1, success=False, stage="JSON_PARSE", category="JSON_PARSE_ERROR"),
        attempt_row("ASSET-001", 3, 2, success=False, stage="SCHEMA_VALIDATION", category="VALIDATION_ERROR"),
    ]
    result = analyze(executions, attempts)
    assert metric(result, "retry_executions") == 3
    assert metric(result, "retry_success") == 1
    assert metric(result, "retry_final_failure") == 2
    assert metric(result, "http_429_or_resource_exhausted_attempts") == 1
    assert metric(result, "timeout_attempts") == 1
    assert metric(result, "http_5xx_attempts") == 1
    assert metric(result, "json_parse_failure_attempts") == 1
    assert metric(result, "validation_failure_attempts") == 1
    assert metric(result, "logged_api_attempts") == 306 - 3 + 6
    assert metric(result, "final_failures") == 2
    assert result["integrity_ok"] is True  # final failures are results; the logs are consistent


def test_other_experiments_are_ignored():
    executions, attempts = full_experiment()
    executions.append(exec_row("ASSET-001", 1, 5, 5, 5, exp="formal_other"))
    result = analyze(executions, attempts)
    assert metric(result, "actual_execution_rows_raw") == 306
    assert result["integrity_ok"] is True


def test_cli_writes_all_tables(tmp_path):
    executions, attempts = full_experiment()
    exec_csv, attempt_csv = tmp_path / "exec.csv", tmp_path / "attempt.csv"
    for path, rows in ((exec_csv, executions), (attempt_csv, attempts)):
        with path.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    out = tmp_path / "out"
    code = formal.main(["--experiment-id", EXP, "--execution-csv", str(exec_csv),
                        "--attempt-csv", str(attempt_csv), "--out-dir", str(out)])
    assert code == 0
    names = {p.name for p in out.iterdir()}
    assert {"formal_execution_summary.csv", "cia_stability_summary.csv", "error_retry_summary.csv",
            "cia_case_level_median.csv", "formal_analysis_tables.xlsx", "formal_analysis_tables.md"} <= names


# ------------------------------------------------------ human-AI agreement

def ai_cases_from(values, incomplete=()):
    """values: {case_id: (c, i, a)}"""
    return {
        case: {"complete": case not in incomplete, "C": v[0], "I": v[1], "A": v[2]}
        for case, v in values.items()
    }


def write_human_workbook(path, rows, *, shuffle=False, rater="R1"):
    rows = list(rows)
    if shuffle:
        random.Random(7).shuffle(rows)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "CIA_Rating"
    sheet.append(["case_id", "asset_name", "confidentiality_rating", "confidentiality_reason",
                  "integrity_rating", "integrity_reason", "availability_rating", "availability_reason"])
    for case, c, i, a in rows:
        sheet.append([case, "n", c, "r", i, "r", a, "r"])
    meta = workbook.create_sheet("Rater_Metadata")
    meta.append(["rater_id", "background"])
    meta.append([rater, "synthetic"])
    workbook.save(path)


SMALL = {"ASSET-001": (3, 3, 5), "ASSET-002": (4, 3, 2), "ASSET-003": (5, 2, 2), "ASSET-004": (2, 5, 4)}
HUMAN_SMALL = [("ASSET-001", 3, 3, 5), ("ASSET-002", 3, 4, 2), ("ASSET-003", 5, 2, 3), ("ASSET-004", 4, 5, 1)]


def load(path, cases, **kwargs):
    return agreement.load_human_ratings(path, list(cases), **kwargs)


def test_ordinal_difference_metrics_are_correct(tmp_path):
    path = tmp_path / "h.xlsx"
    write_human_workbook(path, HUMAN_SMALL)
    result = agreement.compare_ai_human(ai_cases_from(SMALL), load(path, SMALL))
    c_row = next(r for r in result["rows"] if r["dimension"] == "C")
    # ai C = 3,4,5,2 ; human C = 3,3,5,4 -> diffs 0,+1,0,-2
    assert c_row["n_compared"] == 4 and c_row["exact_count"] == 2 and c_row["exact_pct"] == 50.0
    assert c_row["ai_gt_count"] == 1 and c_row["ai_lt_count"] == 1
    assert c_row["mean_abs_diff"] == 0.75 and c_row["median_abs_diff"] == 0.5
    assert c_row["mean_signed_diff"] == -0.25
    assert (c_row["absdiff_0_n"], c_row["absdiff_1_n"], c_row["absdiff_2_n"], c_row["absdiff_3_n"]) == (2, 1, 1, 0)
    a_row = next(r for r in result["rows"] if r["dimension"] == "A")
    # ai A = 5,2,2,4 ; human A = 5,2,3,1 -> diffs 0,0,-1,+3
    assert a_row["absdiff_3_n"] == 1 and a_row["absdiff_1_n"] == 1 and a_row["mean_abs_diff"] == 1.0


def test_row_order_does_not_change_case_id_merge(tmp_path):
    ordered, shuffled = tmp_path / "a.xlsx", tmp_path / "b.xlsx"
    write_human_workbook(ordered, HUMAN_SMALL)
    write_human_workbook(shuffled, HUMAN_SMALL, shuffle=True)
    ai = ai_cases_from(dict(reversed(list(SMALL.items()))))  # AI dict order also reversed
    first = agreement.compare_ai_human(ai, load(ordered, SMALL))["rows"]
    second = agreement.compare_ai_human(ai, load(shuffled, SMALL))["rows"]
    assert first == second


def test_case_id_mismatch_fails_closed(tmp_path):
    missing, extra, dup = tmp_path / "m.xlsx", tmp_path / "e.xlsx", tmp_path / "d.xlsx"
    write_human_workbook(missing, HUMAN_SMALL[:-1])
    write_human_workbook(extra, HUMAN_SMALL + [("ASSET-999", 3, 3, 3)])
    write_human_workbook(dup, HUMAN_SMALL + [HUMAN_SMALL[0]])
    for path in (missing, extra, dup):
        with pytest.raises(agreement.HumanRatingError):
            load(path, SMALL)
    with pytest.raises(agreement.HumanRatingError):
        load(missing, SMALL, allow_partial=True)  # case mismatch is never tolerated


def test_blank_human_rating_detected(tmp_path):
    path = tmp_path / "blank.xlsx"
    write_human_workbook(path, [("ASSET-001", 3, None, 5)] + HUMAN_SMALL[1:])
    with pytest.raises(agreement.HumanRatingError, match="blank"):
        load(path, SMALL)
    partial = load(path, SMALL, allow_partial=True)
    assert partial["blank"]["I"] == ["ASSET-001"]
    rows = agreement.compare_ai_human(ai_cases_from(SMALL), partial)["rows"]
    assert next(r for r in rows if r["dimension"] == "I")["n_compared"] == 3
    assert next(r for r in rows if r["dimension"] == "C")["n_compared"] == 4


@pytest.mark.parametrize("bad", [0, 6, 3.5, "abc", -1])
def test_invalid_human_rating_fails_closed(tmp_path, bad):
    path = tmp_path / "bad.xlsx"
    write_human_workbook(path, [("ASSET-001", bad, 3, 5)] + HUMAN_SMALL[1:])
    with pytest.raises(agreement.HumanRatingError):
        load(path, SMALL, allow_partial=True)


def test_incomplete_ai_cases_are_excluded_and_reported(tmp_path):
    path = tmp_path / "h.xlsx"
    write_human_workbook(path, HUMAN_SMALL)
    ai = ai_cases_from(SMALL, incomplete={"ASSET-004"})
    ai["ASSET-004"] = {"complete": False, "C": None, "I": None, "A": None}
    result = agreement.compare_ai_human(ai, load(path, SMALL))
    assert all(r["n_compared"] == 3 for r in result["rows"])
    scope = {s["item"]: s["value"] for s in result["scope"]}
    assert scope["ai_incomplete_case_ids"] == "ASSET-004" and scope["human_rater_id"] == "R1"


def test_ai_case_level_rejects_bad_medians():
    good = {"case_id": "A", "complete_case": "True", "median_c": "3", "median_i": "3", "median_a": "3"}
    assert agreement.load_ai_case_level([good])["A"]["C"] == 3
    for bad in ({**good, "median_c": "2.5"}, {**good, "median_a": "9"}, {**good, "median_i": ""}):
        with pytest.raises(agreement.AiCaseLevelError):
            agreement.load_ai_case_level([bad])
    with pytest.raises(agreement.AiCaseLevelError):
        agreement.load_ai_case_level([good, good])


def test_ordinal_agreement_metrics_known_values():
    assert agreement.weighted_kappa([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert agreement.weighted_kappa([1, 1, 5, 5], [5, 5, 1, 1]) == pytest.approx(-1.0)
    assert agreement.weighted_kappa([3, 3, 3], [3, 3, 3]) is None
    assert agreement.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert agreement.kendall_tau_b([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert agreement.kendall_tau_b([2, 2, 2], [1, 2, 3]) is None


def test_human_human_function_is_symmetric_and_fails_closed():
    a = {"X": {"C": 3, "I": 3, "A": 3}, "Y": {"C": 4, "I": 2, "A": 5}}
    b = {"X": {"C": 3, "I": 4, "A": 3}, "Y": {"C": 2, "I": 2, "A": 5}}
    rows = agreement.human_human_agreement(a, b)
    c_row = next(r for r in rows if r["dimension"] == "C")
    assert c_row["exact_count"] == 1 and c_row["rater_a_gt_count"] == 1 and c_row["mean_abs_diff"] == 1.0
    with pytest.raises(agreement.HumanRatingError):
        agreement.human_human_agreement(a, {"X": b["X"]})


def test_agreement_cli_end_to_end(tmp_path):
    executions, attempts = full_experiment()
    result = analyze(executions, attempts)
    case_csv = tmp_path / "cases.csv"
    columns = ["case_id", "n_executions", "n_valid_runs", "complete_case", "median_c", "median_i", "median_a"]
    with case_csv.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["case_rows"])
    human = tmp_path / "human.xlsx"
    write_human_workbook(human, [(case, 3, 3, 3) for case in CASES], shuffle=True)
    out = tmp_path / "out"
    assert agreement.main(["--case-level-csv", str(case_csv), "--human-workbook", str(human),
                           "--out-dir", str(out)]) == 0
    assert {"ai_human_comparison.csv", "ai_human_scope.csv", "ai_human_tables.xlsx"} <= {p.name for p in out.iterdir()}
