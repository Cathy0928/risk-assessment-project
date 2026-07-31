import importlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DataResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, result, query_log):
        self.result = result
        self.query_log = query_log

    def select(self, columns):
        self.query_log["select"] = columns
        return self

    def eq(self, column, value):
        self.query_log["filters"].append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.query_log["filters"].append(("in", column, list(values)))
        return self

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result

        records = self.result
        for operation, column, value in self.query_log["filters"]:
            if operation == "eq":
                records = [
                    record for record in records
                    if record.get(column) == value
                ]
            else:
                records = [
                    record for record in records
                    if record.get(column) in value
                ]
        return DataResponse(records)


class FakeSupabase:
    def __init__(self, table_results):
        self.table_results = table_results
        self.queried_tables = []
        self.queries = []

    def table(self, table_name):
        self.queried_tables.append(table_name)
        query_log = {
            "table": table_name,
            "select": None,
            "filters": [],
        }
        self.queries.append(query_log)
        return FakeQuery(self.table_results[table_name], query_log)


class FakeDatabaseError(RuntimeError):
    pass


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
def isolate_external_services(app_module, monkeypatch):
    audit_calls = []

    def reject_real_supabase():
        raise AssertionError("A real Supabase client must not be used in backup tests.")

    monkeypatch.setattr(
        app_module.backup_service,
        "get_supabase_admin_client",
        reject_real_supabase,
    )
    monkeypatch.setattr(
        app_module.admin_service,
        "write_audit_log",
        lambda **payload: audit_calls.append(payload),
    )
    return audit_calls


def login_as(client, role_name, user_id="admin-user", company_id=7):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = user_id
        sess["username"] = "Backup Admin"
        sess["email"] = "admin@example.com"
        sess["role_name"] = role_name
        sess["company_id"] = company_id


def successful_table_results(app_module):
    return {
        "companies": [
            {"id": 7, "company_name": "Tenant Seven"},
            {"id": 99, "company_name": "Other Tenant"},
        ],
        "departments": [
            {"id": 701, "company_id": 7},
            {"id": 9901, "company_id": 99},
        ],
        "users": [
            {"id": "user-7", "company_id": 7},
            {"id": "user-99", "company_id": 99},
        ],
        "roles": [
            {"id": "admin-role", "role_name": "系統管理員"},
            {"id": "user-role", "role_name": "一般使用者"},
        ],
        "assets": [
            {"id": 7001, "company_id": 7},
            {"id": 9901, "company_id": 99},
        ],
        "risk_assessments": [
            {"id": 7101, "asset_id": 7001, "vulnerability_id": 7201},
            {"id": 9911, "asset_id": 9901, "vulnerability_id": 9921},
        ],
        "audit_logs": [
            {"id": 7301, "user_id": "user-7"},
            {"id": 9931, "user_id": "user-99"},
        ],
        "vulnerabilities": [
            {"id": 7201, "cve_id": "CVE-TENANT"},
            {"id": 9921, "cve_id": "CVE-OTHER"},
        ],
    }


def install_fake_supabase(app_module, monkeypatch, table_results):
    fake_supabase = FakeSupabase(table_results)
    monkeypatch.setattr(
        app_module.backup_service,
        "get_supabase_admin_client",
        lambda: fake_supabase,
    )
    return fake_supabase


def open_backup(response):
    return zipfile.ZipFile(io.BytesIO(response.data))


def read_json(backup, filename):
    return json.loads(backup.read(filename).decode("utf-8"))


def query_for(fake_supabase, table_name):
    return next(
        query
        for query in fake_supabase.queries
        if query["table"] == table_name
    )


def is_sensitive_field(field_name):
    normalized = str(field_name).casefold()
    compacted = "".join(character for character in normalized if character.isalnum())
    return any(
        marker in normalized
        for marker in ("password", "secret", "token", "api_key")
    ) or "apikey" in compacted or "key" in compacted


def assert_no_sensitive_fields(value):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            assert not is_sensitive_field(key)
            assert_no_sensitive_fields(nested_value)
    elif isinstance(value, list):
        for item in value:
            assert_no_sensitive_fields(item)


def test_backup_export_requires_login(client):
    response = client.post("/api/admin/backups/export")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}


def test_backup_export_forbids_non_admin(client):
    login_as(client, "一般使用者")

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 403
    assert response.get_json() == {"error": "Forbidden"}


@pytest.mark.parametrize("company_id", [None, 0, -1, "7", True])
def test_backup_export_requires_valid_company_in_session(
    client,
    app_module,
    monkeypatch,
    isolate_external_services,
    company_id,
):
    called = False

    def fail_if_called(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("Backup service must not run without company scope.")

    monkeypatch.setattr(
        app_module.backup_service,
        "create_backup_archive",
        fail_if_called,
    )
    login_as(client, "系統管理員", company_id=company_id)

    response = client.post(
        "/api/admin/backups/export?company_id=99",
        json={"company_id": 99},
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Company scope is required."}
    assert called is False
    assert isolate_external_services[-1]["action"] == "EXPORT_BACKUP"
    assert isolate_external_services[-1]["status"] == "failed"


def test_admin_can_download_backup_zip(client, app_module, monkeypatch):
    table_results = successful_table_results(app_module)
    install_fake_supabase(app_module, monkeypatch, table_results)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 200
    assert response.content_type == "application/zip"
    assert "attachment;" in response.headers["Content-Disposition"]
    assert "riskgenie_backup_" in response.headers["Content-Disposition"]
    assert response.headers["Content-Disposition"].rstrip('"').endswith(".zip")


def test_backup_zip_contains_manifest(client, app_module, monkeypatch):
    fake_supabase = install_fake_supabase(
        app_module,
        monkeypatch,
        successful_table_results(app_module),
    )
    login_as(client, "系統管理員")

    response = client.post(
        "/api/admin/backups/export?company_id=99",
        json={"company_id": 99},
    )

    with open_backup(response) as backup:
        expected_files = {"backup_manifest.json"} | {
            f"{table_name}.json"
            for table_name in app_module.backup_service.BACKUP_TABLES
        }
        assert set(backup.namelist()) == expected_files
        manifest = read_json(backup, "backup_manifest.json")
    assert manifest["backup_version"] == "1.0"
    assert manifest["generated_by"] == "admin-user"
    assert manifest["company_id"] == 7
    assert manifest["generated_at"].endswith("Z")
    assert query_for(fake_supabase, "companies")["filters"] == [
        ("eq", "id", 7)
    ]


def test_backup_manifest_record_counts_are_correct(
    client, app_module, monkeypatch
):
    table_results = successful_table_results(app_module)
    table_results["companies"] = [{"id": 7}, {"id": 7}, {"id": 99}]
    table_results["users"] = [
        {"id": "user-1", "company_id": 7},
        {"id": "user-2", "company_id": 99},
    ]
    install_fake_supabase(app_module, monkeypatch, table_results)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    with open_backup(response) as backup:
        manifest = read_json(backup, "backup_manifest.json")
    assert manifest["record_counts"]["companies"] == 2
    assert manifest["record_counts"]["users"] == 1
    assert manifest["record_counts"]["roles"] == 2


def test_backup_removes_sensitive_fields_from_every_json(
    client, app_module, monkeypatch
):
    table_results = successful_table_results(app_module)
    table_results["users"] = [
        {
            "id": "user-1",
            "password": "LEAK_PASSWORD",
            "password_hash": "LEAK_HASH",
            "access_token": "LEAK_ACCESS",
            "refreshToken": "LEAK_REFRESH",
            "api-key": "LEAK_API_KEY",
            "private_key": "LEAK_PRIVATE_KEY",
            "display_name": "Safe User",
            "company_id": 7,
        }
    ]
    install_fake_supabase(app_module, monkeypatch, table_results)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    with open_backup(response) as backup:
        for filename in backup.namelist():
            data = read_json(backup, filename)
            assert_no_sensitive_fields(data)
            serialized = json.dumps(data)
            assert "LEAK_" not in serialized


def test_backup_removes_nested_sensitive_fields(
    client, app_module, monkeypatch
):
    table_results = successful_table_results(app_module)
    table_results["assets"] = [
        {
            "id": 1,
            "company_id": 7,
            "metadata": {
                "safe": "visible",
                "credentials": {
                    "client_secret": "LEAK_NESTED_SECRET",
                    "apiKey": "LEAK_NESTED_KEY",
                    "signingKey": "LEAK_SIGNING_KEY",
                },
                "sessions": [
                    {
                        "session_token": "LEAK_NESTED_TOKEN",
                        "label": "visible",
                    }
                ],
            },
        }
    ]
    install_fake_supabase(app_module, monkeypatch, table_results)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    with open_backup(response) as backup:
        assets = read_json(backup, "assets.json")
    assert assets[0]["metadata"]["safe"] == "visible"
    assert assets[0]["metadata"]["credentials"] == {}
    assert assets[0]["metadata"]["sessions"] == [{"label": "visible"}]
    assert "LEAK_NESTED" not in json.dumps(assets)


def test_direct_tenant_tables_use_company_filters(
    client, app_module, monkeypatch
):
    fake_supabase = install_fake_supabase(
        app_module,
        monkeypatch,
        successful_table_results(app_module),
    )
    login_as(client, "系統管理員", company_id=7)

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 200
    assert query_for(fake_supabase, "companies")["filters"] == [
        ("eq", "id", 7)
    ]
    for table_name in ("users", "departments", "assets"):
        assert query_for(fake_supabase, table_name)["filters"] == [
            ("eq", "company_id", 7)
        ]


def test_related_tables_are_scoped_through_tenant_records(
    client, app_module, monkeypatch
):
    fake_supabase = install_fake_supabase(
        app_module,
        monkeypatch,
        successful_table_results(app_module),
    )
    login_as(client, "系統管理員", company_id=7)

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 200
    assert query_for(fake_supabase, "risk_assessments")["filters"] == [
        ("in", "asset_id", [7001])
    ]
    assert query_for(fake_supabase, "audit_logs")["filters"] == [
        ("in", "user_id", ["user-7"])
    ]
    assert query_for(fake_supabase, "vulnerabilities")["filters"] == [
        ("in", "id", [7201])
    ]


def test_backup_excludes_other_tenant_data_and_keeps_global_roles(
    client, app_module, monkeypatch
):
    fake_supabase = install_fake_supabase(
        app_module,
        monkeypatch,
        successful_table_results(app_module),
    )
    login_as(client, "系統管理員", company_id=7)

    response = client.post("/api/admin/backups/export")

    with open_backup(response) as backup:
        assert [row["id"] for row in read_json(backup, "companies.json")] == [7]
        assert [row["id"] for row in read_json(backup, "departments.json")] == [701]
        assert [row["id"] for row in read_json(backup, "users.json")] == ["user-7"]
        assert [row["id"] for row in read_json(backup, "assets.json")] == [7001]
        assert [
            row["id"] for row in read_json(backup, "risk_assessments.json")
        ] == [7101]
        assert [row["id"] for row in read_json(backup, "audit_logs.json")] == [7301]
        assert [
            row["id"] for row in read_json(backup, "vulnerabilities.json")
        ] == [7201]
        assert len(read_json(backup, "roles.json")) == 2
    assert query_for(fake_supabase, "roles")["filters"] == []


def test_tables_without_safe_scope_are_skipped_without_query(
    client, app_module, monkeypatch
):
    table_results = successful_table_results(app_module)
    table_results["users"] = FakeDatabaseError("users unavailable")
    table_results["assets"] = FakeDatabaseError("assets unavailable")
    fake_supabase = install_fake_supabase(
        app_module,
        monkeypatch,
        table_results,
    )
    login_as(client, "系統管理員", company_id=7)

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 200
    with open_backup(response) as backup:
        assert "risk_assessments.json" not in backup.namelist()
        assert "audit_logs.json" not in backup.namelist()
        assert "vulnerabilities.json" not in backup.namelist()
        manifest = read_json(backup, "backup_manifest.json")
    assert manifest["skipped_tables"] == [
        {
            "table": "risk_assessments",
            "reason": "scope_dependency_unavailable",
        },
        {
            "table": "audit_logs",
            "reason": "scope_dependency_unavailable",
        },
        {
            "table": "vulnerabilities",
            "reason": "scope_dependency_unavailable",
        },
    ]
    assert "risk_assessments" not in fake_supabase.queried_tables
    assert "audit_logs" not in fake_supabase.queried_tables
    assert "vulnerabilities" not in fake_supabase.queried_tables


def test_partial_table_failure_still_returns_zip(
    client, app_module, monkeypatch
):
    table_results = successful_table_results(app_module)
    table_results["departments"] = FakeDatabaseError(
        "database details must not enter the manifest"
    )
    fake_supabase = install_fake_supabase(
        app_module,
        monkeypatch,
        table_results,
    )
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 200
    with open_backup(response) as backup:
        assert "departments.json" not in backup.namelist()
        manifest = read_json(backup, "backup_manifest.json")
        raw_manifest = backup.read("backup_manifest.json").decode("utf-8")
    assert manifest["failed_tables"] == [
        {
            "table": "departments",
            "error_type": "FakeDatabaseError",
        }
    ]
    assert "database details" not in raw_manifest
    assert fake_supabase.queried_tables == list(
        app_module.backup_service.BACKUP_TABLES
    )


def test_all_table_failures_return_503(client, app_module, monkeypatch):
    table_results = {
        table_name: FakeDatabaseError("query failed")
        for table_name in app_module.backup_service.BACKUP_TABLES
    }
    install_fake_supabase(app_module, monkeypatch, table_results)
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 503
    assert response.content_type == "application/json"
    assert response.get_json() == {"error": "Unable to export backup."}


def test_success_and_failure_attempt_audit_logs(
    client, app_module, monkeypatch, isolate_external_services
):
    successful_results = successful_table_results(app_module)
    install_fake_supabase(app_module, monkeypatch, successful_results)
    login_as(client, "系統管理員")

    success_response = client.post("/api/admin/backups/export")

    failed_results = {
        table_name: FakeDatabaseError("query failed")
        for table_name in app_module.backup_service.BACKUP_TABLES
    }
    install_fake_supabase(app_module, monkeypatch, failed_results)
    failed_response = client.post("/api/admin/backups/export")

    assert success_response.status_code == 200
    assert failed_response.status_code == 503
    assert [call["status"] for call in isolate_external_services] == [
        "success",
        "failed",
    ]
    assert all(
        call["action"] == "EXPORT_BACKUP"
        for call in isolate_external_services
    )
    assert all(
        call["operator_id"] == "admin-user"
        for call in isolate_external_services
    )


def test_audit_failure_does_not_break_successful_backup(
    client, app_module, monkeypatch, caplog
):
    install_fake_supabase(
        app_module,
        monkeypatch,
        successful_table_results(app_module),
    )

    def fail_audit_log(**_payload):
        raise RuntimeError("sensitive audit details")

    monkeypatch.setattr(
        app_module.admin_service,
        "write_audit_log",
        fail_audit_log,
    )
    login_as(client, "系統管理員")

    response = client.post("/api/admin/backups/export")

    assert response.status_code == 200
    assert "RuntimeError" in caplog.text
    assert "sensitive audit details" not in caplog.text
