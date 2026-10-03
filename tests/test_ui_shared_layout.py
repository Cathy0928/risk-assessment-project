"""Design System v2 / shared layout (base.html) frontend contract tests.

Renders the real templates through a fake Supabase client and checks the
parts of the markup that other code depends on: navigation hrefs, active
state, admin-only menu, the asset-summary filter contract and the
dashboard's import form / data endpoints. No live Supabase, Gemini or NVD.
"""

import importlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATES_DIR = ROOT / "riskGenie" / "templates"
ADMIN_ROLE_NAME = "系統管理員"

NAV_HREFS = (
    "/",
    "/summary",
    "/asset_add",
    "/asset_missing",
    "/risk_assessment",
    "/ai-advice",
    "/risk-report",
    "/weight_setting",
)


class _FakeQuery:
    def __init__(self, records):
        self._records = records
        self._filters = []

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _value):
        return self

    def execute(self):
        rows = [
            r for r in self._records
            if all(r.get(f) == v for f, v in self._filters)
        ]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, assets):
        self._assets = assets

    def table(self, _name):
        return _FakeQuery(self._assets)


def asset_record(asset_id, **overrides):
    record = {
        "id": asset_id,
        "company_id": 7,
        "asset_id_code": f"A-{asset_id}",
        "asset_name": f"Asset {asset_id}",
        "asset_type": "HW",
        "data_type": "一般資料",
        "description": "desc",
        "department": "IT",
        "risk_owner": "Ops",
        "use_department": "IT",
        "location": "HQ",
        "confidentiality": 3,
        "integrity": 3,
        "availability": 3,
        "legality": 1,
        "asset_value": 3,
        "upload_user": "Peggy",
        "created_at": "2026-08-01T00:00:00+00:00",
        "status": "active",
        "is_deleted": False,
    }
    record.update(overrides)
    return record


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")
    module = importlib.reload(importlib.import_module("riskGenie.app"))

    fake = _FakeSupabase([
        asset_record(1, asset_name="Web Server", asset_type="HW"),
        asset_record(2, asset_name="Mail Gateway", asset_type="SW",
                     department="Finance"),
    ])
    monkeypatch.setattr(module, "get_supabase_client", lambda: fake)
    app = module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    return app.test_client()


def login_as(client, role_name="一般使用者"):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["role_name"] = role_name
        sess["company_id"] = 7


def get_html(client, path, **kwargs):
    login_as(client, **kwargs)
    response = client.get(path)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def aria_current_hrefs(body):
    return re.findall(r'<a [^>]*href="([^"]+)"[^>]*aria-current="page"', body)


@pytest.mark.parametrize("path", ("/", "/summary"))
def test_sidebar_links_every_live_page(client, path):
    body = get_html(client, path)

    for href in NAV_HREFS:
        assert f'href="{href}"' in body, f"{path} 的 sidebar 缺少 {href}"


@pytest.mark.parametrize("path", ("/", "/summary"))
def test_pages_use_shared_stylesheet_and_no_legacy_sidebar(client, path):
    body = get_html(client, path)

    assert "css/design-system.css" in body
    assert "css/style.css" not in body
    assert "menu-btn" not in body


def test_active_nav_state_follows_current_endpoint(client):
    assert aria_current_hrefs(get_html(client, "/")) == ["/"]
    assert aria_current_hrefs(get_html(client, "/summary")) == ["/summary"]


def test_admin_menu_is_admin_only(client):
    admin_body = get_html(client, "/", role_name=ADMIN_ROLE_NAME)
    user_body = get_html(client, "/", role_name="一般使用者")

    assert 'href="/admin/users"' in admin_body
    assert "/admin/users" not in user_body
    assert "帳號管理" not in user_body


@pytest.mark.parametrize("path", ("/", "/summary"))
def test_no_button_without_explicit_type(client, path):
    """避免再出現「看起來能按、其實沒有 action」的 <button>。"""
    body = get_html(client, path)

    for tag in re.findall(r"<button\b[^>]*>", body):
        assert "type=" in tag, f"{path} 有沒有 type 的 button：{tag}"


def test_summary_filter_form_contract(client):
    body = get_html(client, "/summary?asset_name=Web&asset_type=HW&asset_value=3")

    form = re.search(r'<form[^>]*role="search"[^>]*>', body).group(0)
    assert 'method="get"' in form
    assert 'action="/summary"' in form
    for name in (
        "asset_id_code", "asset_name", "asset_type",
        "department", "risk_owner", "asset_value",
    ):
        assert re.search(rf'name="{name}"', body), name
    # 篩選值會回填，且「更多篩選」在有進階條件時自動展開。
    assert 'value="Web"' in body
    assert re.search(r'<option value="HW"\s+selected', body)
    assert re.search(r'<details class="filter-more"\s+open', body)


def test_summary_filtering_still_applies(client):
    body = get_html(client, "/summary?asset_name=mail")

    assert "Mail Gateway" in body
    assert "Web Server" not in body


def test_summary_empty_result_shows_empty_state(client):
    body = get_html(client, "/summary?asset_name=does-not-exist")

    assert "找不到符合條件的資產" in body
    assert "<table" not in body


def test_summary_row_actions_keep_existing_routes(client):
    body = get_html(client, "/summary")

    assert 'href="/asset_edit/1"' in body
    assert 'href="/asset_delete/1"' in body
    assert 'href="/asset_add"' in body
    assert 'href="/asset_missing"' in body


def test_dashboard_import_form_contract(client):
    body = get_html(client, "/")

    form = re.search(r'<form[^>]*upload_excel[^>]*>|<form[^>]*action="/upload_excel"[^>]*>', body).group(0)
    assert 'method="post"' in form
    assert 'enctype="multipart/form-data"' in form
    assert re.search(r'<input[^>]*type="file"[^>]*name="file"[^>]*accept=".xlsx"', body)
    assert 'href="/download_template"' in body


def test_dashboard_reads_only_existing_apis(client):
    body = get_html(client, "/")

    assert 'data-assessments-url="/api/risk-assessments"' in body
    assert 'data-assets-url="/api/risk-assessments/assets"' in body
    assert "js/dashboard.js" in body


def test_dashboard_script_does_not_write_data():
    source = (ROOT / "riskGenie" / "static" / "js" / "dashboard.js").read_text(
        encoding="utf-8"
    )

    assert not re.search(r"method:\s*['\"](POST|PUT|PATCH|DELETE)", source, re.I)
    assert "innerHTML" not in source
