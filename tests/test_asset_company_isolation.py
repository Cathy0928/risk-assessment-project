import importlib
import io
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class DataResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = []
        self.insert_payload = None
        self.update_payload = None
        self.delete_requested = False
        self.limit_value = None
        self.order_by = None
        self.desc = False

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.order_by = field
        self.desc = desc
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def single(self):
        return self

    def insert(self, payload):
        self.insert_payload = payload.copy()
        return self

    def update(self, payload):
        self.update_payload = payload.copy()
        return self

    def delete(self):
        self.delete_requested = True
        return self

    def _filtered_records(self):
        records = [
            record.copy()
            for record in self.client.records.get(self.table_name, [])
        ]
        for field, value in self.filters:
            records = [record for record in records if record.get(field) == value]
        if self.order_by:
            records.sort(
                key=lambda record: record.get(self.order_by),
                reverse=self.desc,
            )
        if self.limit_value is not None:
            records = records[: self.limit_value]
        return records

    def execute(self):
        self.client.queries.append(
            {
                "table": self.table_name,
                "filters": list(self.filters),
                "insert": self.insert_payload,
                "update": self.update_payload,
                "delete": self.delete_requested,
            }
        )

        if self.insert_payload is not None:
            if self.table_name == "audit_logs" and self.client.fail_audit:
                raise RuntimeError("audit insert failed")
            payload = self.insert_payload.copy()
            if self.table_name == "audit_logs":
                self.client.audit_payloads.append(payload)
                return DataResponse([payload])
            payload.setdefault(
                "id",
                max(
                    [record.get("id", 0) for record in self.client.records[self.table_name]]
                    or [0]
                )
                + 1,
            )
            self.client.records[self.table_name].append(payload)
            self.client.inserted_payloads.append(payload)
            return DataResponse([payload])

        if self.update_payload is not None:
            updated = []
            for record in self.client.records.get(self.table_name, []):
                if all(record.get(field) == value for field, value in self.filters):
                    record.update(self.update_payload)
                    updated.append(record.copy())
            self.client.updated_payloads.extend(updated)
            return DataResponse(updated)

        if self.delete_requested:
            deleted = self._filtered_records()
            self.client.records[self.table_name] = [
                record
                for record in self.client.records.get(self.table_name, [])
                if not all(record.get(field) == value for field, value in self.filters)
            ]
            self.client.deleted_payloads.extend(deleted)
            return DataResponse(deleted)

        return DataResponse(self._filtered_records())


class FakeSupabase:
    def __init__(self, assets=None, fail_audit=False):
        self.records = {
            "assets": list(assets or []),
            "audit_logs": [],
        }
        self.fail_audit = fail_audit
        self.queries = []
        self.inserted_payloads = []
        self.updated_payloads = []
        self.deleted_payloads = []
        self.audit_payloads = []

    def table(self, table_name):
        self.records.setdefault(table_name, [])
        return FakeQuery(self, table_name)


@pytest.fixture()
def app_module(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    module = importlib.import_module("riskGenie.app")
    return importlib.reload(module)


def create_client(app_module, monkeypatch, fake_supabase):
    monkeypatch.setattr(app_module, "get_supabase_client", lambda: fake_supabase)

    def fake_render_template(_template, **context):
        assets = context.get("assets")
        if assets is not None:
            return "|".join(asset["asset_name"] for asset in assets)
        asset = context.get("asset")
        if asset is not None:
            return asset["asset_name"]
        return context.get("error", "template")

    monkeypatch.setattr(app_module, "render_template", fake_render_template)
    app = app_module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
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


def asset_record(asset_id, company_id, code, name):
    return {
        "id": asset_id,
        "company_id": company_id,
        "asset_id_code": code,
        "asset_name": name,
        "asset_type": "HW",
        "data_type": "一般資料",
        "description": "desc",
        "department": "IT",
        "risk_owner": "Ops",
        "use_department": "IT",
        "location": "HQ",
        "confidentiality": 1,
        "integrity": 2,
        "availability": 3,
        "legality": 1,
        "asset_value": 3,
        "upload_user": "Peggy",
        "created_at": "2026-08-01T00:00:00+00:00",
    }


def asset_form(**overrides):
    payload = {
        "asset_id_code": "A-NEW",
        "asset_type": "HW",
        "data_type": "一般資料",
        "asset_name": "New Asset",
        "description": "desc",
        "department": "IT",
        "risk_owner": "Ops",
        "use_department": "IT",
        "location": "HQ",
        "confidentiality": "1",
        "integrity": "2",
        "availability": "3",
        "legality": "1",
    }
    payload.update(overrides)
    return payload


def test_home_and_summary_only_show_current_company_assets(app_module, monkeypatch):
    fake = FakeSupabase(
        [
            asset_record(1, 7, "A-001", "Company A Asset"),
            asset_record(2, 8, "B-001", "Company B Asset"),
        ]
    )
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    home = client.get("/")
    summary = client.get("/summary")

    assert home.status_code == 200
    assert summary.status_code == 200
    assert "Company A Asset" in home.get_data(as_text=True)
    assert "Company B Asset" not in home.get_data(as_text=True)
    assert "Company A Asset" in summary.get_data(as_text=True)
    assert "Company B Asset" not in summary.get_data(as_text=True)
    asset_selects = [
        query for query in fake.queries
        if query["table"] == "assets" and query["insert"] is None
    ]
    assert all(("company_id", 7) in query["filters"] for query in asset_selects)


def test_asset_add_uses_session_company_id_and_ignores_client_company_id(
    app_module, monkeypatch
):
    fake = FakeSupabase([])
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post("/asset_add", data=asset_form(company_id=999))

    assert response.status_code == 302
    assert fake.inserted_payloads[0]["company_id"] == 7
    assert fake.inserted_payloads[0]["asset_name"] == "New Asset"
    assert "asset_code" not in fake.audit_payloads[0]


def test_excel_import_uses_session_company_id_and_ignores_excel_company_id(
    app_module, monkeypatch
):
    fake = FakeSupabase([])
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    class FakeDataFrame:
        def iterrows(self):
            yield 0, {
                "資產代碼": "XLS-001",
                "資產類型": "HW",
                "資料類型": "一般資料",
                "資產名稱": "Excel Asset",
                "資產描述": "desc",
                "權責單位": "IT",
                "保管單位(風險擁有者)": "Ops",
                "使用單位": "IT",
                "放置地點": "HQ",
                "機密性": 1,
                "完整性": 2,
                "可用性": 3,
                "適法性": 1,
                "company_id": 999,
            }

    monkeypatch.setattr(app_module.pd, "read_excel", lambda _file: FakeDataFrame())

    response = client.post(
        "/upload_excel",
        data={"file": (io.BytesIO(b"not-used"), "assets.xlsx")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 302
    assert fake.inserted_payloads[0]["company_id"] == 7
    assert fake.inserted_payloads[0]["asset_name"] == "Excel Asset"


def test_current_company_cannot_modify_other_company_asset(app_module, monkeypatch):
    fake = FakeSupabase([asset_record(2, 8, "B-001", "Company B Asset")])
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post("/asset_edit/2", data=asset_form(asset_name="Changed"))

    assert response.status_code == 404
    assert fake.updated_payloads == []
    assert "Company B Asset" not in response.get_data(as_text=True)


def test_current_company_cannot_delete_other_company_asset(app_module, monkeypatch):
    fake = FakeSupabase([asset_record(2, 8, "B-001", "Company B Asset")])
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post("/asset_delete/2")

    assert response.status_code == 404
    assert fake.deleted_payloads == []
    assert "Company B Asset" not in response.get_data(as_text=True)


def test_missing_company_id_returns_json_403_before_asset_query(
    app_module, monkeypatch
):
    fake = FakeSupabase([asset_record(1, 7, "A-001", "Company A Asset")])
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=None)

    response = client.post("/asset_add", json={})

    assert response.status_code == 403
    assert response.get_json() == {
        "success": False,
        "error": "帳號缺少公司識別資訊",
    }
    assert fake.queries == []


def test_audit_failure_does_not_turn_successful_add_or_update_into_500(
    app_module, monkeypatch
):
    fake = FakeSupabase([asset_record(1, 7, "A-001", "Company A Asset")], fail_audit=True)
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    add_response = client.post("/asset_add", data=asset_form(asset_id_code="A-002"))
    update_response = client.post(
        "/asset_edit/1",
        data=asset_form(asset_id_code="A-001", asset_name="Changed"),
    )

    assert add_response.status_code == 302
    assert update_response.status_code == 302
    assert any(record["asset_name"] == "Changed" for record in fake.records["assets"])


def test_delete_writes_best_effort_audit_with_no_asset_id(app_module, monkeypatch):
    fake = FakeSupabase([asset_record(1, 7, "A-001", "Company A Asset")])
    client = create_client(app_module, monkeypatch, fake)
    login_as(client, company_id=7)

    response = client.post("/asset_delete/1")

    assert response.status_code == 302
    assert fake.deleted_payloads[0]["id"] == 1
    assert fake.audit_payloads[0]["action"] == "刪除資產"
    assert fake.audit_payloads[0]["asset_id"] is None
    assert "asset_code" not in fake.audit_payloads[0]
