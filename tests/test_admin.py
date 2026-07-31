import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def mock_audit_log(app_module, monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module.admin_service,
        "write_audit_log",
        lambda **kwargs: calls.append(kwargs),
    )
    return calls


def login_as(client, role_name, user_id="user-id"):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user_id
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role_name"] = role_name
        sess["company_id"] = 7


def new_user_payload(**overrides):
    payload = {
        "username": "new_user",
        "email": "user@example.com",
        "password": "Password123!",
        "role_id": "role-uuid",
    }
    payload.update(overrides)
    return payload


def new_user_service_payload(**overrides):
    payload = new_user_payload(company_id=7)
    payload.update(overrides)
    return payload


class TenantQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.update_values = None
        self.selected_fields = None

    def select(self, fields):
        self.selected_fields = fields
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, _value):
        return self

    def update(self, values):
        self.update_values = values
        return self

    def execute(self):
        records = [
            record.copy() for record in self.client.records.get(self.table_name, [])
        ]
        for field, value in self.filters:
            records = [record for record in records if record.get(field) == value]

        if self.update_values is not None:
            for record in records:
                record.update(self.update_values)

        self.client.executed_queries.append(
            {
                "table": self.table_name,
                "filters": list(self.filters),
                "selected_fields": self.selected_fields,
                "update_values": self.update_values,
            }
        )
        return SimpleNamespace(data=records)


class TenantClient:
    def __init__(self, records):
        self.records = records
        self.executed_queries = []

    def table(self, table_name):
        return TenantQuery(self, table_name)


def test_admin_roles_requires_login(client):
    response = client.get("/api/admin/roles")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}


def test_admin_users_forbids_non_admin(client):
    login_as(client, "一般使用者")

    response = client.get("/api/admin/users")

    assert response.status_code == 403
    assert response.get_json() == {"error": "Forbidden"}


def test_admin_can_list_roles(client, app_module, monkeypatch, mock_audit_log):
    expected_roles = [
        {"id": 1, "role_name": "系統管理員"},
        {"id": 2, "role_name": "一般使用者"},
    ]
    monkeypatch.setattr(app_module.admin_service, "list_roles", lambda: expected_roles)
    login_as(client, "系統管理員")

    response = client.get("/api/admin/roles")

    assert response.status_code == 200
    assert response.get_json() == {"roles": expected_roles}
    assert mock_audit_log[-1]["action"] == "LIST_ROLES"
    assert mock_audit_log[-1]["status"] == "success"


def test_admin_can_list_users(
    client, app_module, monkeypatch, mock_audit_log
):
    expected_users = [
        {
            "id": "user-id",
            "username": "Peggy",
            "email": "peggy@example.com",
            "role_id": 1,
            "company_id": 7,
            "is_active": True,
        }
    ]
    received = {}

    def list_users(company_id):
        received["company_id"] = company_id
        return expected_users

    monkeypatch.setattr(app_module.admin_service, "list_users", list_users)
    login_as(client, "系統管理員")

    response = client.get("/api/admin/users?company_id=99")

    assert response.status_code == 200
    assert response.get_json() == {"users": expected_users}
    assert received == {"company_id": 7}
    assert mock_audit_log[-1]["action"] == "LIST_USERS"
    assert mock_audit_log[-1]["status"] == "success"


def test_list_users_service_filters_company_and_returns_is_active(
    app_module, monkeypatch
):
    fake_client = TenantClient(
        {
            "users": [
                {
                    "id": "company-7-user",
                    "username": "Current",
                    "company_id": 7,
                    "is_active": True,
                },
                {
                    "id": "company-8-user",
                    "username": "Other",
                    "company_id": 8,
                    "is_active": False,
                },
            ]
        }
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )

    users = app_module.admin_service.list_users(7)

    assert [user["id"] for user in users] == ["company-7-user"]
    assert users[0]["is_active"] is True
    assert fake_client.executed_queries == [
        {
            "table": "users",
            "filters": [("company_id", 7)],
            "selected_fields": app_module.admin_service.USER_FIELDS,
            "update_values": None,
        }
    ]


def test_users_operations_require_session_company(client, app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.admin_service,
        "list_roles",
        lambda: pytest.fail("Roles service must not be called."),
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "list_users",
        lambda *_args: pytest.fail("List service must not be called."),
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "create_user",
        lambda **_kwargs: pytest.fail("Create service must not be called."),
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "update_user",
        lambda *_args: pytest.fail("Update service must not be called."),
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "disable_user",
        lambda *_args: pytest.fail("Disable service must not be called."),
    )
    login_as(client, app_module.ADMIN_ROLE_NAME)
    with client.session_transaction() as sess:
        sess.pop("company_id")

    responses = [
        client.get("/api/admin/roles"),
        client.get("/api/admin/users"),
        client.post("/api/admin/users", json=new_user_payload()),
        client.patch(
            "/api/admin/users/target-user",
            json={"username": "updated"},
        ),
        client.post("/api/admin/users/target-user/disable"),
    ]

    assert [response.status_code for response in responses] == [
        403,
        403,
        403,
        403,
        403,
    ]
    assert all(
        response.get_json() == {"error": "Company context is required."}
        for response in responses
    )


def test_admin_roles_database_error_returns_503(
    client, app_module, monkeypatch, mock_audit_log
):
    def raise_database_error():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_module.admin_service, "list_roles", raise_database_error)
    login_as(client, "系統管理員")

    response = client.get("/api/admin/roles")

    assert response.status_code == 503
    assert response.get_json() == {"error": "Unable to load roles"}
    assert mock_audit_log[-1]["action"] == "LIST_ROLES"
    assert mock_audit_log[-1]["status"] == "failed"


def test_create_user_requires_login(client):
    response = client.post("/api/admin/users", json=new_user_payload())

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}


def test_create_user_forbids_non_admin(client):
    login_as(client, "一般使用者")

    response = client.post("/api/admin/users", json=new_user_payload())

    assert response.status_code == 403
    assert response.get_json() == {"error": "Forbidden"}


def test_create_user_missing_field_returns_400(client, mock_audit_log):
    login_as(client, "系統管理員")
    payload = new_user_payload()
    del payload["username"]

    response = client.post("/api/admin/users", json=payload)

    assert response.status_code == 400
    assert response.get_json()["field"] == "username"
    assert mock_audit_log[-1]["action"] == "CREATE_USER"
    assert mock_audit_log[-1]["status"] == "failed"


def test_create_user_rejects_invalid_email(client):
    login_as(client, "系統管理員")

    response = client.post(
        "/api/admin/users",
        json=new_user_payload(email="not-an-email"),
    )

    assert response.status_code == 400
    assert response.get_json()["field"] == "email"


def test_create_user_rejects_short_password(client):
    login_as(client, "系統管理員")

    response = client.post(
        "/api/admin/users",
        json=new_user_payload(password="short"),
    )

    assert response.status_code == 400
    assert response.get_json()["field"] == "password"


def test_create_user_rejects_client_company_id(client, app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.admin_service,
        "create_user",
        lambda **_kwargs: pytest.fail("Service must not be called."),
    )
    login_as(client, app_module.ADMIN_ROLE_NAME)

    response = client.post(
        "/api/admin/users",
        json=new_user_payload(company_id=99),
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Validation failed",
        "field": "company_id",
        "message": "company_id cannot be specified by the client.",
    }


def test_create_user_rejects_missing_company(client, app_module, monkeypatch):
    def raise_company_not_found(**_kwargs):
        raise app_module.admin_service.CompanyNotFoundError()

    monkeypatch.setattr(
        app_module.admin_service,
        "create_user",
        raise_company_not_found,
    )
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users", json=new_user_payload())

    assert response.status_code == 400
    assert response.get_json() == {"error": "Company does not exist."}


def test_create_user_duplicate_email_returns_409(client, app_module, monkeypatch):
    def raise_duplicate(**_kwargs):
        raise app_module.admin_service.DuplicateEmailError()

    monkeypatch.setattr(app_module.admin_service, "create_user", raise_duplicate)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users", json=new_user_payload())

    assert response.status_code == 409
    assert response.get_json() == {"error": "Email is already in use."}


def test_create_user_success_returns_201(
    client, app_module, monkeypatch, mock_audit_log
):
    created_user = {
        "id": "auth-created-id",
        "username": "new_user",
        "email": "user@example.com",
        "role_id": "role-uuid",
        "company_id": 7,
        "is_active": True,
    }
    received = {}

    def create_user(**payload):
        received.update(payload)
        return created_user

    monkeypatch.setattr(app_module.admin_service, "create_user", create_user)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users", json=new_user_payload())

    assert response.status_code == 201
    assert response.get_json() == {"user": created_user}
    assert received["password"] == "Password123!"
    assert received["company_id"] == 7
    assert "password" not in response.get_json()["user"]
    assert mock_audit_log[-1]["action"] == "CREATE_USER"
    assert mock_audit_log[-1]["status"] == "success"


def test_create_user_uses_auth_id_for_profile(app_module, monkeypatch):
    auth_calls = []
    inserted_profiles = []

    class FakeAuthAdmin:
        def create_user(self, attributes):
            auth_calls.append(attributes)
            return SimpleNamespace(user=SimpleNamespace(id="auth-created-id"))

        def delete_user(self, _user_id):
            raise AssertionError("Compensation should not run on success.")

    fake_client = SimpleNamespace(auth=SimpleNamespace(admin=FakeAuthAdmin()))
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "_email_exists",
        lambda _client, _email: False,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "_company_exists",
        lambda _client, _company_id: True,
    )

    def insert_profile(_client, profile):
        inserted_profiles.append(profile)
        return profile

    monkeypatch.setattr(
        app_module.admin_service,
        "_insert_profile",
        insert_profile,
    )

    result = app_module.admin_service.create_user(**new_user_service_payload())

    assert auth_calls == [
        {
            "email": "user@example.com",
            "password": "Password123!",
            "email_confirm": True,
        }
    ]
    assert inserted_profiles[0]["id"] == "auth-created-id"
    assert "password" not in inserted_profiles[0]
    assert "password" not in result


def test_profile_failure_deletes_created_auth_user(app_module, monkeypatch):
    deleted_user_ids = []

    class FakeAuthAdmin:
        def create_user(self, _attributes):
            return SimpleNamespace(user=SimpleNamespace(id="auth-created-id"))

        def delete_user(self, user_id):
            deleted_user_ids.append(user_id)

    fake_client = SimpleNamespace(auth=SimpleNamespace(admin=FakeAuthAdmin()))
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "_email_exists",
        lambda _client, _email: False,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "_company_exists",
        lambda _client, _company_id: True,
    )

    def fail_profile_insert(_client, _profile):
        raise RuntimeError("profile insert failed")

    monkeypatch.setattr(
        app_module.admin_service,
        "_insert_profile",
        fail_profile_insert,
    )

    with pytest.raises(app_module.admin_service.ProfileCreationError):
        app_module.admin_service.create_user(**new_user_service_payload())

    assert deleted_user_ids == ["auth-created-id"]


def test_auth_duplicate_email_is_reported_as_conflict(app_module, monkeypatch):
    class FakeAuthAdmin:
        def create_user(self, _attributes):
            raise RuntimeError("Email already registered")

    fake_client = SimpleNamespace(auth=SimpleNamespace(admin=FakeAuthAdmin()))
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "_email_exists",
        lambda _client, _email: False,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "_company_exists",
        lambda _client, _company_id: True,
    )

    with pytest.raises(app_module.admin_service.DuplicateEmailError):
        app_module.admin_service.create_user(**new_user_service_payload())


def test_update_user_success(client, app_module, monkeypatch, mock_audit_log):
    updated_user = {
        "id": "target-user",
        "username": "updated",
        "email": "target@example.com",
        "role_id": "new-role",
        "company_id": 7,
        "is_active": True,
    }
    received = {}

    def update_user(user_id, changes, company_id):
        received["user_id"] = user_id
        received["changes"] = changes
        received["company_id"] = company_id
        return updated_user

    monkeypatch.setattr(app_module.admin_service, "update_user", update_user)
    login_as(client, "系統管理員")

    response = client.patch(
        "/api/admin/users/target-user",
        json={"username": "updated", "role_id": "new-role"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"user": updated_user}
    assert received == {
        "user_id": "target-user",
        "changes": {
            "username": "updated",
            "role_id": "new-role",
        },
        "company_id": 7,
    }
    assert mock_audit_log[-1]["action"] == "UPDATE_USER"
    assert mock_audit_log[-1]["status"] == "success"


def test_update_user_rejects_email_change(client, app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.admin_service,
        "update_user",
        lambda *_args, **_kwargs: pytest.fail("Service must not be called."),
    )
    login_as(client, "系統管理員")

    response = client.patch(
        "/api/admin/users/target-user",
        json={"email": "changed@example.com"},
    )

    assert response.status_code == 400
    assert response.get_json()["field"] == "email"


def test_update_user_rejects_company_change(client, app_module, monkeypatch):
    monkeypatch.setattr(
        app_module.admin_service,
        "update_user",
        lambda *_args, **_kwargs: pytest.fail("Service must not be called."),
    )
    login_as(client, app_module.ADMIN_ROLE_NAME)

    response = client.patch(
        "/api/admin/users/target-user",
        json={"company_id": 99},
    )

    assert response.status_code == 400
    assert response.get_json()["field"] == "company_id"


def test_update_missing_user_returns_404(client, app_module, monkeypatch):
    def raise_not_found(_user_id, _changes, _company_id):
        raise app_module.admin_service.UserNotFoundError()

    monkeypatch.setattr(app_module.admin_service, "update_user", raise_not_found)
    login_as(client, "系統管理員")

    response = client.patch(
        "/api/admin/users/missing-user",
        json={"username": "updated"},
    )

    assert response.status_code == 404
    assert response.get_json() == {"error": "User not found."}


def test_update_user_service_hides_other_company_user(app_module, monkeypatch):
    fake_client = TenantClient(
        {
            "users": [
                {
                    "id": "other-company-user",
                    "username": "Other",
                    "company_id": 8,
                    "is_active": True,
                }
            ]
        }
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )

    with pytest.raises(app_module.admin_service.UserNotFoundError):
        app_module.admin_service.update_user(
            "other-company-user",
            {"username": "changed"},
            7,
        )

    assert fake_client.executed_queries == [
        {
            "table": "users",
            "filters": [
                ("id", "other-company-user"),
                ("company_id", 7),
            ],
            "selected_fields": app_module.admin_service.USER_FIELDS,
            "update_values": None,
        }
    ]


def test_admin_cannot_disable_self(client, app_module, monkeypatch, mock_audit_log):
    monkeypatch.setattr(
        app_module.admin_service,
        "disable_user",
        lambda _user_id, _company_id: pytest.fail("Service must not be called."),
    )
    login_as(client, "系統管理員", user_id="admin-user")

    response = client.post("/api/admin/users/admin-user/disable")

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Administrators cannot disable themselves."
    }
    assert mock_audit_log[-1]["action"] == "DISABLE_USER"
    assert mock_audit_log[-1]["status"] == "failed"


def test_disable_user_success(client, app_module, monkeypatch, mock_audit_log):
    disabled_user = {
        "id": "target-user",
        "username": "Target",
        "email": "target@example.com",
        "role_id": "role-uuid",
        "company_id": 1,
        "is_active": False,
    }
    received = {}

    def disable_user(user_id, company_id):
        received["user_id"] = user_id
        received["company_id"] = company_id
        return disabled_user, False

    monkeypatch.setattr(app_module.admin_service, "disable_user", disable_user)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users/target-user/disable")

    assert response.status_code == 200
    assert response.get_json() == {
        "user": disabled_user,
        "already_disabled": False,
    }
    assert received == {"user_id": "target-user", "company_id": 7}
    assert mock_audit_log[-1]["action"] == "DISABLE_USER"
    assert mock_audit_log[-1]["status"] == "success"


def test_disable_user_returns_explicit_already_disabled_result(
    client, app_module, monkeypatch
):
    disabled_user = {"id": "target-user", "is_active": False}
    monkeypatch.setattr(
        app_module.admin_service,
        "disable_user",
        lambda _user_id, _company_id: (disabled_user, True),
    )
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users/target-user/disable")

    assert response.status_code == 200
    assert response.get_json()["already_disabled"] is True


def test_disable_user_service_does_not_update_already_disabled_user(
    app_module, monkeypatch
):
    fake_client = TenantClient(
        {
            "users": [
                {
                    "id": "disabled-user",
                    "username": "Disabled",
                    "company_id": 7,
                    "is_active": False,
                }
            ]
        }
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )

    user, already_disabled = app_module.admin_service.disable_user(
        "disabled-user",
        7,
    )

    assert already_disabled is True
    assert user["is_active"] is False
    assert len(fake_client.executed_queries) == 1
    assert fake_client.executed_queries[0]["update_values"] is None


def test_disable_missing_user_returns_404(client, app_module, monkeypatch):
    def raise_not_found(_user_id, _company_id):
        raise app_module.admin_service.UserNotFoundError()

    monkeypatch.setattr(app_module.admin_service, "disable_user", raise_not_found)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users/missing-user/disable")

    assert response.status_code == 404
    assert response.get_json() == {"error": "User not found."}


def test_disable_user_service_hides_other_company_user(app_module, monkeypatch):
    fake_client = TenantClient(
        {
            "users": [
                {
                    "id": "other-company-user",
                    "username": "Other",
                    "company_id": 8,
                    "is_active": True,
                }
            ]
        }
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "get_supabase_admin_client",
        lambda: fake_client,
    )

    with pytest.raises(app_module.admin_service.UserNotFoundError):
        app_module.admin_service.disable_user("other-company-user", 7)

    assert fake_client.executed_queries == [
        {
            "table": "users",
            "filters": [
                ("id", "other-company-user"),
                ("company_id", 7),
            ],
            "selected_fields": app_module.admin_service.USER_FIELDS,
            "update_values": None,
        }
    ]


def test_disable_reports_missing_status_column(client, app_module, monkeypatch):
    def raise_missing_column(_user_id, _company_id):
        raise app_module.admin_service.UserStatusConfigError(
            "public.users.is_active is required to disable accounts."
        )

    monkeypatch.setattr(
        app_module.admin_service,
        "disable_user",
        raise_missing_column,
    )
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users/target-user/disable")

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "public.users.is_active is required to disable accounts.",
        "code": "USER_STATUS_COLUMN_MISSING",
    }


def test_create_user_reports_missing_supabase_secret(
    client, app_module, monkeypatch
):
    def raise_missing_config(**_kwargs):
        raise app_module.SupabaseConfigError(
            "Missing required environment variable: SUPABASE_SECRET_KEY"
        )

    monkeypatch.setattr(
        app_module.admin_service,
        "create_user",
        raise_missing_config,
    )
    login_as(client, "系統管理員")

    response = client.post("/api/admin/users", json=new_user_payload())

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "Missing required environment variable: SUPABASE_SECRET_KEY"
    }
