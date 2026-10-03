"""Phase 4 UI migration contract tests.

Renders the real templates (risk_assessment.html, ai_advice.html,
risk_report.html) through a fake Supabase client and checks the parts of
the markup/script that other code depends on: field ids the inline JS
reads by id, the API endpoints each page actually calls, the RiskOps
status contract, and that the new Risk Report has no hard-coded demo
numbers left over from the old static/mock dashboard.

No live Supabase, Gemini or NVD.
"""

import importlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeQuery:
    def __init__(self, records):
        self._records = records
        self._filters = []
        self._in_filters = []
        self._limit = None

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def in_(self, field, values):
        self._in_filters.append((field, set(values)))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, value):
        self._limit = value
        return self

    def execute(self):
        rows = [
            r for r in self._records
            if all(r.get(f) == v for f, v in self._filters)
            and all(r.get(f) in vs for f, vs in self._in_filters)
        ]
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


def _asset_record(asset_id=1, **overrides):
    record = {
        "id": asset_id,
        "company_id": 7,
        "asset_id_code": f"A-{asset_id}",
        "asset_name": f"Asset {asset_id}",
        "asset_type": "HW",
        "description": "desc",
        "confidentiality": 3,
        "integrity": 3,
        "availability": 3,
        "legality": 1,
        "asset_value": 3,
        "is_deleted": False,
        "status": "active",
    }
    record.update(overrides)
    return record


def _assessment_record(assessment_id=501, **overrides):
    record = {
        "id": assessment_id,
        "company_id": 7,
        "asset_id": 1,
        "ai_suggestion": None,
        "status": "待處理",
        "treatment_note": "",
        "treatment_due_date": None,
        "evidence_url": "",
        "threat_description": "threat",
        "cvss_score": 7.5,
        "likelihood_score": 3,
        "impact_score": 3,
        "risk_score": 9,
        "risk_level": "高風險",
        "created_at": "2026-08-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


# ================================================================
# Risk Assessment: workflow field/route contract
# ================================================================

RISK_ASSESSMENT_FIELD_IDS = (
    "assetSelect", "confidentiality", "integrity", "availability",
    "legality", "threatDescription", "cvssScore", "likelihoodScore",
    "resultBox", "resImpact", "resScore", "resLevel",
    "saveAssessmentBtn", "calculateBtn", "aiAdviceBtn",
)


def test_risk_assessment_keeps_shared_layout_and_field_ids():
    body = get_html_standalone("/risk_assessment")

    assert "css/design-system.css" in body
    assert "css/style.css" not in body

    for field_id in RISK_ASSESSMENT_FIELD_IDS:
        assert f'id="{field_id}"' in body, field_id


def test_risk_assessment_calls_existing_calculate_and_save_endpoints():
    body = get_html_standalone("/risk_assessment")

    assert "/api/risk-assessments/calculate" in body
    assert "/api/risk-assessments/save" in body
    assert "/api/risk-assessments/assets" in body
    assert "currentRiskData" in body
    assert "currentAssessmentId" in body
    assert "calculationGeneration" in body


def test_risk_assessment_no_fake_result_before_calculation():
    body = get_html_standalone("/risk_assessment")

    result_box = re.search(
        r'<div class="section" id="resultBox"[^>]*style="display:none;"', body
    )
    assert result_box, "resultBox must start hidden until a real calculation runs"


def test_risk_assessment_links_to_ai_advice_page():
    body = get_html_standalone("/risk_assessment")

    assert "/ai-advice" in body


# ================================================================
# AI Advice / RiskOps: field + status contract
# ================================================================

RISKOPS_FIELD_IDS = (
    "riskOpsStatus", "treatmentNote", "treatmentDueDate", "evidenceUrl",
    "saveRiskOpsButton",
)


def test_ai_advice_keeps_shared_layout_and_riskops_fields():
    body = get_html_standalone("/ai-advice")

    assert "css/design-system.css" in body
    assert "css/style.css" not in body

    for field_id in RISKOPS_FIELD_IDS:
        assert f'id="{field_id}"' in body, field_id


def test_ai_advice_page_has_no_large_emoji_in_title():
    body = get_html_standalone("/ai-advice")

    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    assert title_match, "expected an h1 page title"
    assert "🤖" not in title_match.group(1)
    assert "🛡️" not in title_match.group(1)


def test_riskops_status_select_matches_backend_contract():
    from riskGenie.services.risk_routes import RISKOPS_STATUSES

    body = get_html_standalone("/ai-advice")

    select_match = re.search(
        r'<select id="riskOpsStatus"[^>]*>(.*?)</select>', body, re.S
    )
    assert select_match, "expected the RiskOps status <select>"

    options = set(re.findall(r'<option value="([^"]+)"', select_match.group(1)))
    assert options == RISKOPS_STATUSES


def test_ai_advice_calls_existing_riskops_and_ai_advice_endpoints():
    body = get_html_standalone("/ai-advice")

    assert "/riskops`" in body
    assert "assessment_id" in body


def test_ai_advice_timestamp_never_labels_assessment_created_at_as_ai_time():
    """Phase 4 item D: without a real AI-generation timestamp column,
    assessment.created_at must be labeled '評鑑建立時間', never
    'AI 建議產生時間'."""
    body = get_html_standalone("/ai-advice")

    assert "data.generated_at ? 'AI 建議產生時間' : '評鑑建立時間'" in body
    assert "assessment_created_at" in body


# ================================================================
# Risk Report: real data source, no mock metrics
# ================================================================

HARDCODED_DEMO_NUMBERS = ("100", "14 <span", "30 <span", "56 <span")


def test_risk_report_keeps_shared_layout():
    body = get_html_standalone("/risk-report")

    assert "css/design-system.css" in body
    assert "css/risk_report.css" not in body
    assert "menu-btn" not in body


def test_risk_report_uses_existing_readonly_endpoints_only():
    body = get_html_standalone("/risk-report")

    assert "/api/risk-assessments" in body
    assert "/api/risk-assessments/assets" in body
    # No new chart dependency.
    assert "chart.js" not in body.lower()
    assert "cdn.jsdelivr.net" not in body


def test_risk_report_has_no_hardcoded_demo_metrics():
    body = get_html_standalone("/risk-report")
    source = (ROOT / "riskGenie" / "static" / "js" / "risk_report.js").read_text(
        encoding="utf-8"
    )

    for needle in HARDCODED_DEMO_NUMBERS:
        assert needle not in body
    # The old mock numbers must not survive anywhere, including the script.
    for literal in ("92", "88", "82", "客戶資料伺服器", "⭐"):
        assert literal not in body
        assert literal not in source


def test_risk_report_script_does_not_write_data():
    source = (ROOT / "riskGenie" / "static" / "js" / "risk_report.js").read_text(
        encoding="utf-8"
    )

    assert not re.search(r"method:\s*['\"](POST|PUT|PATCH|DELETE)", source, re.I)
    assert "innerHTML" not in source


def test_risk_report_export_action_points_at_real_endpoint():
    body = get_html_standalone("/risk-report")

    assert 'href="/export"' in body


# ================================================================
# Standalone client builder (module-level fixtures aren't usable from
# free functions, so these tests build their own app/client directly).
# ================================================================

def get_html_standalone(path):
    import os

    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
    os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "anon-test-key")
    os.environ.setdefault("GEMINI_API_KEY", "gemini-test-key")
    os.environ.setdefault("FLASK_ENV", "development")

    module = importlib.import_module("riskGenie.app")
    module = importlib.reload(module)

    from riskGenie.services import risk_routes

    fake = _FakeSupabase({
        "assets": [_asset_record(1)],
        "risk_assessments": [_assessment_record(501)],
    })

    risk_routes.get_supabase_client = lambda: fake
    risk_routes.get_supabase_admin_client = lambda: fake

    app = module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    test_client = app.test_client()

    with test_client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["role_name"] = "一般使用者"
        sess["company_id"] = 7

    response = test_client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)
