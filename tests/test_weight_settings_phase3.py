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


def login_as(client, company_id=7):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = "user"
        if company_id is not None:
            sess["company_id"] = company_id


def weight_payload(**overrides):
    payload = {
        "formula_type": "weighted_average",
        "weight_c": 0.3,
        "weight_i": 0.3,
        "weight_a": 0.4,
    }
    payload.update(overrides)
    return payload


def test_weight_page_redirects_without_login_and_does_not_query_supabase(
    client, monkeypatch
):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "get_weight_settings",
        lambda *_args, **_kwargs: pytest.fail("Supabase must not be queried."),
    )

    response = client.get("/weight_setting")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_weight_api_missing_company_id_returns_403_and_does_not_default_to_one(
    client, monkeypatch
):
    from riskGenie.services import risk_routes

    called = False

    def fail_if_called(_company_id):
        nonlocal called
        called = True
        raise AssertionError("company_id must not default to 1.")

    monkeypatch.setattr(risk_routes.RiskService, "get_weight_settings", fail_if_called)
    login_as(client, company_id=None)

    response = client.get("/api/weight-settings")

    assert response.status_code == 403
    assert response.get_json() == {
        "success": False,
        "error": "帳號缺少公司識別資訊",
        "code": "COMPANY_CONTEXT_REQUIRED",
    }
    assert called is False


def test_weight_api_get_uses_session_company_id(client, monkeypatch):
    from riskGenie.services import risk_routes

    received = {}

    def get_settings(company_id):
        received["company_id"] = company_id
        return {
            "company_id": company_id,
            "formula_type": "weighted_average",
            "weight_c": 0.3,
            "weight_i": 0.3,
            "weight_a": 0.4,
        }

    monkeypatch.setattr(risk_routes.RiskService, "get_weight_settings", get_settings)
    login_as(client, company_id=7)

    response = client.get("/api/weight-settings")

    assert response.status_code == 200
    assert response.get_json()["company_id"] == 7
    assert received == {"company_id": 7}


def test_weight_api_save_ignores_client_company_id(client, monkeypatch):
    from riskGenie.services import risk_routes

    received = {}

    def save_settings(**kwargs):
        received.update(kwargs)
        return {
            "success": True,
            "supabase_synced": True,
            "local_backup_saved": True,
            "status": "synced",
            "message": "ok",
            "settings": kwargs,
        }

    monkeypatch.setattr(risk_routes.RiskService, "save_weight_settings", save_settings)
    login_as(client, company_id=7)

    response = client.post(
        "/api/weight-settings",
        json=weight_payload(company_id=99),
    )

    assert response.status_code == 200
    assert received["company_id"] == 7
    assert received["formula_type"] == "weighted_average"


def test_weight_page_missing_company_id_returns_403(client, monkeypatch):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **_kwargs: pytest.fail("Missing company_id must not save."),
    )
    login_as(client, company_id=None)

    response = client.post("/weight_setting", data=weight_payload())

    assert response.status_code == 403
    assert "帳號缺少公司識別資訊" in response.get_data(as_text=True)


def test_weight_page_post_uses_same_percent_normalization(client, monkeypatch):
    from riskGenie.services import risk_routes

    received = {}
    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **kwargs: received.update(kwargs) or {"success": True},
    )
    login_as(client, company_id=7)

    response = client.post(
        "/weight_setting",
        data=weight_payload(weight_c="30", weight_i="30", weight_a="40"),
    )

    assert response.status_code == 302
    assert received["company_id"] == 7
    assert (
        received["weight_c"],
        received["weight_i"],
        received["weight_a"],
    ) == (0.3, 0.3, 0.4)


def test_weight_page_post_rejects_invalid_formula_like_json_api(
    client, monkeypatch
):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **_kwargs: pytest.fail("Invalid HTML form data must not save."),
    )
    login_as(client, company_id=7)

    response = client.post(
        "/weight_setting",
        data=weight_payload(formula_type="median"),
    )

    assert response.status_code == 400


def test_weighted_avg_is_normalized_before_save(client, monkeypatch):
    from riskGenie.services import risk_routes

    received = {}

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **kwargs: received.update(kwargs) or {"success": True},
    )
    login_as(client, company_id=7)

    response = client.post(
        "/api/weight-settings",
        json=weight_payload(formula_type="weighted_avg"),
    )

    assert response.status_code == 200
    assert received["formula_type"] == "weighted_average"


def test_unknown_formula_returns_400_and_does_not_fall_back_to_max(
    client, monkeypatch
):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **_kwargs: pytest.fail("Invalid formula must not be saved."),
    )
    login_as(client, company_id=7)

    response = client.post(
        "/api/weight-settings",
        json=weight_payload(formula_type="median"),
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_FORMULA_TYPE"


@pytest.mark.parametrize(
    ("weights", "expected"),
    [
        ({"weight_c": 30, "weight_i": 30, "weight_a": 40}, (0.3, 0.3, 0.4)),
        ({"weight_c": 0.3, "weight_i": 0.3, "weight_a": 0.4}, (0.3, 0.3, 0.4)),
    ],
)
def test_weight_inputs_are_normalized(client, monkeypatch, weights, expected):
    from riskGenie.services import risk_routes

    received = {}
    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **kwargs: received.update(kwargs) or {"success": True},
    )
    login_as(client, company_id=7)

    response = client.post(
        "/api/weight-settings",
        json=weight_payload(**weights),
    )

    assert response.status_code == 200
    assert (
        received["weight_c"],
        received["weight_i"],
        received["weight_a"],
    ) == expected


def test_weight_total_must_be_one_for_weighted_average(client, monkeypatch):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **_kwargs: pytest.fail("Invalid weight total must not be saved."),
    )
    login_as(client, company_id=7)

    response = client.post(
        "/api/weight-settings",
        json=weight_payload(weight_c=0.2, weight_i=0.2, weight_a=0.2),
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_WEIGHT_TOTAL"


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), -0.1, True, "not-a-number"],
)
def test_invalid_weight_values_are_rejected(client, monkeypatch, bad_value):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **_kwargs: pytest.fail("Invalid weights must not be saved."),
    )
    login_as(client, company_id=7)

    response = client.post(
        "/api/weight-settings",
        json=weight_payload(weight_c=bad_value),
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_WEIGHT"


def test_weight_get_does_not_leak_raw_supabase_exception(client, monkeypatch):
    from riskGenie.services import risk_routes

    def fail_get(_company_id):
        raise RuntimeError("SECRET_SUPABASE_URL PostgREST traceback")

    monkeypatch.setattr(risk_routes.RiskService, "get_weight_settings", fail_get)
    login_as(client, company_id=7)

    response = client.get("/api/weight-settings")

    body = response.get_json()
    assert response.status_code == 503
    assert body["code"] == "WEIGHT_SETTINGS_UNAVAILABLE"
    assert "SECRET_SUPABASE_URL" not in body["error"]
    assert "PostgREST" not in body["error"]


def test_weight_save_returns_warning_success_when_fallback_succeeds(
    client, monkeypatch
):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes.RiskService,
        "save_weight_settings",
        lambda **kwargs: {
            "success": True,
            "supabase_synced": False,
            "local_backup_saved": True,
            "status": "local_backup_only",
            "message": "已儲存至本機備份，但雲端同步失敗。",
            "settings": kwargs,
        },
    )
    login_as(client, company_id=7)

    response = client.post("/api/weight-settings", json=weight_payload())

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["supabase_synced"] is False
    assert body["status"] == "local_backup_only"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/ai-advice"),
        ("get", "/export"),
        ("post", "/api/risk-assessments/calculate"),
        ("get", "/api/risk-assessments"),
    ],
)
def test_company_context_required_for_ai_export_and_risk_apis(
    client, method, path
):
    login_as(client, company_id=None)
    payload = {
        "asset_id": 701,
        "asset_name": "Web Server",
        "company_id": 99,
        "confidentiality": 5,
        "integrity": 4,
        "availability": 5,
        "legality": 3,
        "cvss_score": 9.8,
        "likelihood_score": 5,
        "impact_score": 9.8,
        "risk_score": 49,
        "risk_level": "極高風險",
    }

    if method == "post":
        response = client.post(path, json=payload)
    else:
        response = client.get(path)

    assert response.status_code == 403
    assert response.get_json() == {
        "success": False,
        "error": "帳號缺少公司識別資訊",
        "code": "COMPANY_CONTEXT_REQUIRED",
    }


def test_ai_missing_company_id_does_not_call_gemini(client, monkeypatch):
    from riskGenie.services import risk_routes

    monkeypatch.setattr(
        risk_routes,
        "generate_advice",
        lambda **_kwargs: pytest.fail("RAG/Gemini must not be called."),
    )
    monkeypatch.setattr(
        risk_routes,
        "is_gemini_configured",
        lambda: pytest.fail("Gemini config must not be checked."),
    )
    monkeypatch.setattr(
        risk_routes,
        "get_supabase_client",
        lambda: pytest.fail("Supabase must not be queried."),
    )
    login_as(client, company_id=None)

    response = client.post(
        "/api/ai-advice",
        json={
            "asset_id": 701,
            "asset_name": "Web Server",
            "company_id": 99,
            "confidentiality": 5,
            "integrity": 4,
            "availability": 5,
            "legality": 3,
            "cvss_score": 9.8,
            "likelihood_score": 5,
            "impact_score": 9.8,
            "risk_score": 49,
            "risk_level": "極高風險",
        },
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "COMPANY_CONTEXT_REQUIRED"


def test_risk_service_rejects_invalid_company_id():
    from riskGenie.services.risk_service import (
        InvalidCompanyContextError,
        RiskService,
    )

    with pytest.raises(InvalidCompanyContextError):
        RiskService.get_weight_settings(True)
