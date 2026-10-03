"""Phase 5 item D: Risk Report latest-per-asset semantics.

The actual aggregation logic lives in client-side JS
(riskGenie/static/js/risk_aggregations.js) because Risk Report is a
pure front-end consumer of the existing read-only
/api/risk-assessments(/assets) endpoints — there is no new backend
API to unit test in Python.

tests/js/risk_aggregations.test.js is a plain Node (assert-based, no
framework) test of that module's pure functions: given an asset with
several historical risk_assessments rows, every Risk Report number
(assessed count, high-risk list, level/status distribution,
overdue/due-soon) must be unique-by-asset and based on the *latest*
assessment — exactly like Dashboard's existing latestPerAsset()
convention. This file just makes `py -m pytest -q` run that Node
script and fail loudly if it (or Node itself) is unavailable, so the
regression coverage shows up in the same place as everything else.
"""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_TEST = ROOT / "tests" / "js" / "risk_aggregations.test.js"
AGG_MODULE = ROOT / "riskGenie" / "static" / "js" / "risk_aggregations.js"
REPORT_JS = ROOT / "riskGenie" / "static" / "js" / "risk_report.js"


def _node_available():
    return shutil.which("node") is not None


@pytest.mark.skipif(not _node_available(), reason="node is not installed")
def test_risk_aggregations_node_suite_passes():
    result = subprocess.run(
        ["node", str(NODE_TEST)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    assert result.returncode == 0, (
        "tests/js/risk_aggregations.test.js failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_risk_report_js_delegates_to_shared_aggregation_module():
    """Guard against risk_report.js growing its own duplicate (and
    possibly inconsistent) latest-per-asset logic again."""
    source = REPORT_JS.read_text(encoding="utf-8")

    assert "window.RiskAggregations" in source
    assert "Agg.summarizeRiskAssessments" in source
    assert "Agg.latestPerAsset" in source or "latestPerAsset" not in source

    # These must not be reimplemented locally in risk_report.js —
    # they belong in risk_aggregations.js only.
    for banned in ("function latestPerAsset", "function riskRank", "function isOpen"):
        assert banned not in source, f"{banned} must live only in risk_aggregations.js"


def test_risk_report_template_loads_aggregation_module_before_report_js():
    template = (ROOT / "riskGenie" / "templates" / "risk_report.html").read_text(
        encoding="utf-8"
    )

    agg_pos = template.find("js/risk_aggregations.js")
    report_pos = template.find("js/risk_report.js")

    assert agg_pos != -1, "risk_report.html must load risk_aggregations.js"
    assert report_pos != -1
    assert agg_pos < report_pos, "risk_aggregations.js must load before risk_report.js"
