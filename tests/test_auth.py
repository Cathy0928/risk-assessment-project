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

    def limit(self, *_args):
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


def _login_session(client, user_id, role_name="系統管理員", company_id=7):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user_id
        sess["username"] = user_id
        sess["email"] = f"{user_id}@example.com"
        sess["role"] = role_name
        sess["role_name"] = role_name
        sess["company_id"] = company_id


def _mutable_authorization_directory(app_module):
    users = {
        "operator-admin": {
            "id": "operator-admin",
            "username": "Operator",
            "email": "operator@example.com",
            "role_name": app_module.ADMIN_ROLE_NAME,
            "company_id": 7,
            "is_active": True,
        },
        "target-admin": {
            "id": "target-admin",
            "username": "Target",
            "email": "target@example.com",
            "role_name": app_module.ADMIN_ROLE_NAME,
            "company_id": 7,
            "is_active": True,
        },
    }

    def load_current_profile(user_id):
        profile = users.get(user_id)
        if not profile:
            raise LookupError("User profile not found.")
        if profile["is_active"] is False:
            raise app_module.AccountDisabledError("Account disabled.")
        return dict(profile)

    return users, load_current_profile


def test_disabled_admin_old_session_is_blocked_on_next_sensitive_request(
    app,
    app_module,
    monkeypatch,
):
    users, load_current_profile = _mutable_authorization_directory(app_module)
    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        load_current_profile,
        raising=False,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "write_audit_log",
        lambda **_payload: None,
    )

    def disable_user(user_id, company_id):
        assert company_id == 7
        users[user_id]["is_active"] = False
        return dict(users[user_id]), False

    list_users_called = False

    def list_users(_company_id):
        nonlocal list_users_called
        list_users_called = True
        return []

    monkeypatch.setattr(app_module.admin_service, "disable_user", disable_user)
    monkeypatch.setattr(app_module.admin_service, "list_users", list_users)

    operator_client = app.test_client()
    target_client = app.test_client()
    _login_session(operator_client, "operator-admin")
    _login_session(target_client, "target-admin")

    disable_response = operator_client.post(
        "/api/admin/users/target-admin/disable"
    )
    blocked_response = target_client.get("/api/admin/users")

    assert disable_response.status_code == 200
    assert blocked_response.status_code == 401
    assert blocked_response.get_json() == {"error": "Unauthorized"}
    assert list_users_called is False
    with target_client.session_transaction() as sess:
        assert "logged_in" not in sess
        assert "user_id" not in sess


def test_demoted_admin_old_session_is_forbidden_on_next_sensitive_request(
    app,
    app_module,
    monkeypatch,
):
    users, load_current_profile = _mutable_authorization_directory(app_module)
    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        load_current_profile,
        raising=False,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "write_audit_log",
        lambda **_payload: None,
    )

    def update_user(user_id, changes, company_id):
        assert company_id == 7
        assert changes == {"role_id": "general-user-role"}
        users[user_id]["role_name"] = "一般使用者"
        return dict(users[user_id])

    list_users_called = False

    def list_users(_company_id):
        nonlocal list_users_called
        list_users_called = True
        return []

    monkeypatch.setattr(app_module.admin_service, "update_user", update_user)
    monkeypatch.setattr(app_module.admin_service, "list_users", list_users)

    operator_client = app.test_client()
    target_client = app.test_client()
    _login_session(operator_client, "operator-admin")
    _login_session(target_client, "target-admin")

    demote_response = operator_client.patch(
        "/api/admin/users/target-admin",
        json={"role_id": "general-user-role"},
    )
    blocked_response = target_client.get("/api/admin/users")

    assert demote_response.status_code == 200
    assert blocked_response.status_code == 403
    assert blocked_response.get_json() == {"error": "Forbidden"}
    assert list_users_called is False
    with target_client.session_transaction() as sess:
        assert sess["logged_in"] is True
        assert sess["role"] == "一般使用者"
        assert sess["role_name"] == "一般使用者"


def test_api_write_refreshes_current_company_context(
    app,
    app_module,
    monkeypatch,
):
    current_profile = {
        "id": "active-user",
        "username": "Active User",
        "email": "active@example.com",
        "role_name": "一般使用者",
        "company_id": 9,
        "is_active": True,
    }
    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        lambda _user_id: dict(current_profile),
    )

    @app.post("/api/test-company-context")
    def api_test_company_context():
        return {"company_id": app_module.session.get("company_id")}

    write_client = app.test_client()
    _login_session(
        write_client,
        "active-user",
        role_name="一般使用者",
        company_id=7,
    )

    response = write_client.post("/api/test-company-context")

    assert response.status_code == 200
    assert response.get_json() == {"company_id": 9}


def test_sensitive_authorization_lookup_failure_blocks_api_write(
    app,
    app_module,
    monkeypatch,
):
    view_called = False

    def fail_lookup(_user_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        fail_lookup,
    )

    @app.post("/api/test-write")
    def api_test_write():
        nonlocal view_called
        view_called = True
        return {"ok": True}

    write_client = app.test_client()
    _login_session(write_client, "active-user", role_name="一般使用者")

    response = write_client.post("/api/test-write")

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Authorization service unavailable"
    }
    assert view_called is False


def test_missing_current_profile_clears_session_and_blocks_sensitive_request(
    app,
    app_module,
    monkeypatch,
):
    view_called = False

    def missing_profile(_user_id):
        raise LookupError("User profile not found.")

    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        missing_profile,
    )

    @app.post("/api/test-missing-profile")
    def api_test_missing_profile():
        nonlocal view_called
        view_called = True
        return {"ok": True}

    write_client = app.test_client()
    _login_session(write_client, "deleted-user", role_name="一般使用者")

    response = write_client.post("/api/test-missing-profile")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}
    assert view_called is False
    with write_client.session_transaction() as sess:
        assert "logged_in" not in sess
        assert "user_id" not in sess


def test_profile_lookup_uses_bounded_query_instead_of_single(app_module):
    class EmptyQuery:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, value):
            assert value == 1
            return self

        def single(self):
            raise AssertionError("Profile lookup must not use single().")

        def execute(self):
            return DataResponse([])

    class EmptyProfileSupabase:
        def table(self, table_name):
            assert table_name == "users"
            return EmptyQuery()

    with pytest.raises(LookupError, match="did not return a profile"):
        app_module._load_profile(
            EmptyProfileSupabase(),
            "deleted-user",
            require_active_status=True,
        )


def test_ordinary_read_only_page_keeps_session_without_profile_lookup(
    app,
    app_module,
    monkeypatch,
):
    def fail_if_called(_user_id):
        raise AssertionError("Read-only pages must not refresh authorization.")

    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        fail_if_called,
    )

    @app.get("/test-read-only")
    @app_module.login_required
    def test_read_only():
        return {"ok": True}

    read_client = app.test_client()
    _login_session(read_client, "active-user", role_name="一般使用者")

    response = read_client.get("/test-read-only")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
