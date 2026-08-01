import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_module(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    module = importlib.import_module("riskGenie.app")
    return importlib.reload(module)


@pytest.fixture()
def app(app_module):
    return app_module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})


@pytest.fixture()
def client(app):
    return app.test_client()


def login_as(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = "user"
        sess["company_id"] = 7


def valid_ai_payload():
    return {
        "asset_name": "Web Server",
        "cia": "C:5 I:4 A:5",
        "cvss": 9.8,
        "risk_score": 49,
    }


def test_weighted_average_and_weighted_avg_match():
    from riskGenie.models.risk_engine import RiskEngine

    weights = {"c": 0.8, "i": 0.1, "a": 0.1}

    weighted_average = RiskEngine.calculate_risk(
        1, 1, 5, 10, "weighted_average", weights
    )
    weighted_avg = RiskEngine.calculate_risk(
        1, 1, 5, 10, "weighted_avg", weights
    )

    assert weighted_average == weighted_avg


def test_weighted_average_does_not_fall_back_to_max():
    from riskGenie.models.risk_engine import RiskEngine

    weights = {"c": 0.8, "i": 0.1, "a": 0.1}

    weighted_average = RiskEngine.calculate_risk(
        1, 1, 5, 10, "weighted_average", weights
    )
    max_value = RiskEngine.calculate_risk(1, 1, 5, 10, "max", weights)

    assert weighted_average == 14
    assert weighted_average != max_value


def test_ai_advice_requires_login(client):
    response = client.post("/ai-advice", json=valid_ai_payload())

    assert response.status_code == 401
    assert response.is_json
    assert response.get_json()["code"] == "UNAUTHORIZED"


def test_export_requires_login(client):
    response = client.get("/export")

    assert response.status_code == 401
    assert response.is_json
    assert response.get_json()["code"] == "UNAUTHORIZED"


def test_ai_advice_missing_fields_returns_400(client):
    login_as(client)
    payload = valid_ai_payload()
    del payload["risk_score"]

    response = client.post("/ai-advice", json=payload)

    assert response.status_code == 400
    assert response.get_json()["code"] == "MISSING_FIELDS"
    assert response.get_json()["missing_fields"] == ["risk_score"]


def test_ai_advice_without_gemini_key_fails_safely(
    client, monkeypatch
):
    from riskGenie.services import risk_routes

    def fail_if_called(_data):
        raise AssertionError("Gemini must not be called without GEMINI_API_KEY.")

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(risk_routes, "generate_risk_advice", fail_if_called)
    login_as(client)

    response = client.post("/ai-advice", json=valid_ai_payload())

    assert response.status_code == 503
    body = response.get_json()
    assert body["success"] is False
    assert body["code"] == "GEMINI_NOT_CONFIGURED"
    assert "GEMINI_API_KEY" in body["error"]


def test_weight_settings_reports_local_backup_when_supabase_fails(
    tmp_path, monkeypatch
):
    from riskGenie.services import risk_service

    def fail_supabase():
        raise RuntimeError("database connection details must stay server-side")

    monkeypatch.setattr(risk_service, "get_supabase_client", fail_supabase)
    monkeypatch.setattr(
        risk_service,
        "FALLBACK_FILE",
        str(tmp_path / "weight_settings_fallback.json"),
    )

    result = risk_service.RiskService.save_weight_settings(
        company_id=7,
        formula_type="weighted_average",
        weight_c=0.4,
        weight_i=0.3,
        weight_a=0.3,
    )

    assert result["success"] is True
    assert result["supabase_synced"] is False
    assert result["local_backup_saved"] is True
    assert result["status"] == "local_backup_only"
