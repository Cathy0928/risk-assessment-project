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


def login_as(client, role_name):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = role_name
        sess["company_id"] = 7


def test_admin_roles_requires_login(client):
    response = client.get("/api/admin/roles")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}


def test_admin_users_forbids_non_admin(client):
    login_as(client, "一般使用者")

    response = client.get("/api/admin/users")

    assert response.status_code == 403
    assert response.get_json() == {"error": "Forbidden"}


def test_admin_can_list_roles(client, app_module, monkeypatch):
    expected_roles = [
        {"id": 1, "role_name": "系統管理員"},
        {"id": 2, "role_name": "一般使用者"},
    ]
    monkeypatch.setattr(app_module.admin_service, "list_roles", lambda: expected_roles)
    login_as(client, "系統管理員")

    response = client.get("/api/admin/roles")

    assert response.status_code == 200
    assert response.get_json() == {"roles": expected_roles}


def test_admin_can_list_users(client, app_module, monkeypatch):
    expected_users = [
        {
            "id": "user-id",
            "username": "Peggy",
            "email": "peggy@example.com",
            "role_id": 1,
            "company_id": 7,
        }
    ]
    monkeypatch.setattr(app_module.admin_service, "list_users", lambda: expected_users)
    login_as(client, "系統管理員")

    response = client.get("/api/admin/users")

    assert response.status_code == 200
    assert response.get_json() == {"users": expected_users}


def test_admin_roles_database_error_returns_503(client, app_module, monkeypatch):
    def raise_database_error():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module.admin_service, "list_roles", raise_database_error)
    login_as(client, "系統管理員")

    response = client.get("/api/admin/roles")

    assert response.status_code == 503
    assert response.get_json() == {"error": "Unable to load roles"}
