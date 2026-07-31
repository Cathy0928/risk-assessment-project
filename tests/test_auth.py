import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class AuthUser:
    id = "auth-user-id"


class AuthResponse:
    user = AuthUser()


class DataResponse:
    def __init__(self, data):
        self.data = data


class QueryBuilder:
    def __init__(self, data):
        self.data = data

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def single(self):
        return self

    def execute(self):
        return DataResponse(self.data)


class MockAuth:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []
        self.sign_out_calls = 0

    def sign_in_with_password(self, credentials):
        self.calls.append(credentials)
        if self.fail:
            raise RuntimeError("invalid credentials")
        return AuthResponse()

    def sign_out(self):
        self.sign_out_calls += 1


class MockSupabase:
    def __init__(self, fail_auth=False, is_active=True):
        self.auth = MockAuth(fail=fail_auth)
        self.is_active = is_active

    def table(self, name):
        if name == "users":
            return QueryBuilder(
                {
                    "id": "auth-user-id",
                    "username": "Peggy",
                    "email": "peggy@example.com",
                    "role_id": 1,
                    "company_id": 7,
                    "is_active": self.is_active,
                }
            )
        if name == "roles":
            return QueryBuilder({"role_name": "admin"})
        return QueryBuilder(None)


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


def test_get_login_returns_success(client):
    response = client.get("/login")

    assert response.status_code == 200
    assert b'type="password"' in response.data


def test_home_redirects_to_login_when_not_authenticated(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_missing_login_fields_do_not_call_supabase(client, app_module, monkeypatch):
    called = False

    def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("Supabase should not be called.")

    monkeypatch.setattr(app_module, "get_supabase_client", fail_if_called)

    response = client.post("/login", data={"email": "", "password": ""})

    assert response.status_code == 200
    assert called is False


def test_login_failure_shows_generic_error(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "get_supabase_client", lambda: MockSupabase(fail_auth=True))

    response = client.post(
        "/login",
        data={"email": "peggy@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 200
    assert "電子郵件或密碼錯誤".encode("utf-8") in response.data


def test_disabled_user_cannot_create_session(client, app_module, monkeypatch):
    supabase = MockSupabase(is_active=False)
    monkeypatch.setattr(app_module, "get_supabase_client", lambda: supabase)

    response = client.post(
        "/login",
        data={"email": "disabled@example.com", "password": "not-a-real-password"},
    )

    assert response.status_code == 200
    assert response.location is None
    assert app_module.ACCOUNT_DISABLED_MESSAGE.encode("utf-8") in response.data
    assert supabase.auth.sign_out_calls == 1
    with client.session_transaction() as sess:
        assert "logged_in" not in sess
        assert "user_id" not in sess
        assert "role" not in sess
        assert "company_id" not in sess


def test_login_success_creates_session(client, app_module, monkeypatch):
    supabase = MockSupabase()
    monkeypatch.setattr(app_module, "get_supabase_client", lambda: supabase)

    response = client.post(
        "/login",
        data={"email": "peggy@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["user_id"] == "auth-user-id"
        assert sess["username"] == "Peggy"
        assert sess["email"] == "peggy@example.com"
        assert sess["role"] == "admin"
        assert sess["role_name"] == "admin"
        assert sess["company_id"] == 7
        assert sess["logged_in"] is True
        assert "password" not in sess


def test_login_success_redirects_to_home(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, "get_supabase_client", lambda: MockSupabase())

    response = client.post(
        "/login",
        data={"email": "peggy@example.com", "password": "correct-password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_api_auth_me_returns_401_when_not_authenticated(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_api_auth_me_returns_session_user_when_authenticated(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "auth-user-id"
        sess["role"] = "admin"
        sess["company_id"] = 7
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = "admin"
        sess["company_id"] = 7

    response = client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.get_json() == {
        "id": "auth-user-id",
        "username": "Peggy",
        "email": "peggy@example.com",
        "role": "admin",
        "company_id": 7,
    }


def test_post_logout_clears_session(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "auth-user-id"

    response = client.post("/logout")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert "logged_in" not in sess
        assert "user_id" not in sess
        assert "role" not in sess
        assert "company_id" not in sess


def test_get_logout_does_not_log_out(client):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "auth-user-id"

    response = client.get("/logout")

    assert response.status_code == 405
    with client.session_transaction() as sess:
        assert sess["logged_in"] is True
        assert sess["user_id"] == "auth-user-id"
