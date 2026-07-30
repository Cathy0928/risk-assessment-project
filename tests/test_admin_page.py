import importlib
import sys

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-flask-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret-key")

    sys.modules.pop("riskGenie.app", None)
    app_module = importlib.import_module("riskGenie.app")
    return app_module.create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-flask-secret",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def login_session(client, role_name):
    with client.session_transaction() as session:
        session["logged_in"] = True
        session["user_id"] = "user-1"
        session["username"] = "tester"
        session["role_name"] = role_name
        session["company_id"] = 1


def test_admin_users_page_requires_login(client):
    response = client.get("/admin/users")

    assert response.status_code == 401
    assert response.get_json()["error"] == "Unauthorized"


def test_admin_users_page_rejects_non_admin(client):
    login_session(client, "一般使用者")

    response = client.get("/admin/users")

    assert response.status_code == 403
    assert response.get_json()["error"] == "Forbidden"


def test_admin_users_page_allows_admin(client):
    login_session(client, "系統管理員")

    response = client.get("/admin/users")

    assert response.status_code == 200
    assert "帳號管理".encode("utf-8") in response.data
    assert b'id="create-user-form"' in response.data
    assert b'id="users-body"' in response.data
    assert b"js/admin_users.js" in response.data
    assert b'name="company_id"' not in response.data


def test_admin_users_page_requires_company_context(client):
    login_session(client, "系統管理員")
    with client.session_transaction() as session:
        session.pop("company_id")

    response = client.get("/admin/users")

    assert response.status_code == 403
    assert response.get_json() == {"error": "Company context is required."}
