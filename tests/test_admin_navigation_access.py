"""Admin-users ("帳號管理") navigation regression tests.

Every active sidebar template used to render "帳號管理" as a bare
<button> with no href — a logged-in admin had no way to click through
to /admin/users; they had to type the URL by hand. The backend route
(admin_users_page, decorated with @admin_required) was never the
problem; the navigation simply never pointed at it.

This file renders the REAL templates (unlike
tests/test_asset_company_isolation.py, which stubs render_template
out entirely) through a fake Supabase client, so a broken Jinja block
would actually fail here. It checks:

1. An admin session sees a real <a href="/admin/users"> link on every
   active page that has a "帳號管理" sidebar item.
2. A non-admin session still sees the old inert button (no new access
   implied by the frontend link).
3. The backend authorization contract is untouched: admin_required
   still gates /admin/users, and a non-admin session still gets
   redirected/rejected on both the page and its APIs.

No live Supabase, no Gemini, no NVD.
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ADMIN_ROLE_NAME = "系統管理員"


# ================================================================
# Minimal fake Supabase client — enough for the GET-only page
# renders exercised here (assets select, optionally by id).
# ================================================================

class _FakeQuery:
    def __init__(self, records):
        self._records = records
        self._filters = []
        self._limit = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self._filters.append((field, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self._limit = value
        return self

    def execute(self):
        rows = [
            record
            for record in self._records
            if all(record.get(field) == value for field, value in self._filters)
        ]
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, assets=None):
        self._tables = {"assets": list(assets or [])}

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


def asset_record(asset_id=701, company_id=7, **overrides):
    record = {
        "id": asset_id,
        "company_id": company_id,
        "asset_id_code": f"A-{asset_id}",
        "asset_name": "Web Server",
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
def app_module(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    module = importlib.import_module("riskGenie.app")
    return importlib.reload(module)


@pytest.fixture()
def client(app_module, monkeypatch):
    from riskGenie.services import risk_routes

    fake = _FakeSupabase(assets=[asset_record()])
    monkeypatch.setattr(app_module, "get_supabase_client", lambda: fake)
    # ai_advice_page() 等頁面是透過 risk_routes 拿 anon client，不是
    # app_module 的 _LazySupabaseClient，兩邊都要 patch 掉，否則會
    # 真的嘗試對外建立連線。
    monkeypatch.setattr(risk_routes, "get_supabase_client", lambda: fake)

    app = app_module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})
    return app.test_client()


def login_as(client, role_name="user", company_id=7):
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "user-id"
        sess["username"] = "Peggy"
        sess["email"] = "peggy@example.com"
        sess["role"] = role_name
        sess["role_name"] = role_name
        sess["company_id"] = company_id


ADMIN_LINK_NEEDLE = 'href="/admin/users"'

# 已改用 base.html 的頁面：非管理員完全看不到「帳號管理」。
# 尚未遷移的舊頁面仍保留沒有 href 的裝飾性按鈕，等遷移後再移出此清單。
PAGES_ON_SHARED_LAYOUT = (
    "/", "/summary", "/asset_add", "/asset_edit/701", "/weight_setting",
    "/risk_assessment", "/ai-advice", "/risk-report", "/asset_delete/701",
)

# (page path, requires a DB-backed asset to exist at this id or not)
PAGES_WITH_ADMIN_NAV_ITEM = (
    "/",
    "/summary",
    "/asset_add",
    "/asset_edit/701",
    "/asset_delete/701",
    "/weight_setting",
    "/risk_assessment",
    "/risk-report",
    "/ai-advice",
)


@pytest.mark.parametrize("path", PAGES_WITH_ADMIN_NAV_ITEM)
def test_admin_session_sees_real_admin_users_link(client, monkeypatch, path):
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    # risk_assessments 的 admin-client 查詢（ai_advice_page / 其他頁面
    # 內部可能用到）在這裡故意失敗，模擬本機沒有 SUPABASE_SECRET_KEY
    # 的狀態；這些頁面必須照樣 render，不能因此連頁面都開不起來。
    def fail_admin_client():
        raise SupabaseConfigError("Missing required environment variable: SUPABASE_SECRET_KEY")

    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )

    login_as(client, role_name=ADMIN_ROLE_NAME)

    response = client.get(path)

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ADMIN_LINK_NEEDLE in body, (
        f"{path} 在管理員 session 下應該看到可點擊的"
        "/admin/users 連結，而不是裝飾性按鈕"
    )


@pytest.mark.parametrize("path", PAGES_WITH_ADMIN_NAV_ITEM)
def test_non_admin_session_does_not_get_a_clickable_admin_link(
    client, monkeypatch, path
):
    """非管理員不應該因為這次改動多看到任何新的連結/權限 ——
    畫面上仍然是原本那個沒有 href 的裝飾性按鈕。
    """
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    def fail_admin_client():
        raise SupabaseConfigError("Missing required environment variable: SUPABASE_SECRET_KEY")

    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )

    login_as(client, role_name="一般使用者")

    response = client.get(path)

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ADMIN_LINK_NEEDLE not in body, (
        f"{path} 在非管理員 session 下不應該出現可點擊的"
        "/admin/users 連結"
    )
    if path in PAGES_ON_SHARED_LAYOUT:
        assert "帳號管理" not in body
    else:
        assert "帳號管理" in body


# ================================================================
# Backend authorization contract 不變：前端加連結不等於後端放寬權限。
# ================================================================

def test_admin_users_page_still_requires_admin_role(client):
    login_as(client, role_name="一般使用者")

    response = client.get("/admin/users")

    assert response.status_code == 403


def test_admin_users_page_requires_login_at_all(client):
    response = client.get("/admin/users")

    assert response.status_code == 401


def test_admin_users_page_allows_real_admin_role(client):
    login_as(client, role_name=ADMIN_ROLE_NAME)

    response = client.get("/admin/users")

    assert response.status_code == 200


def test_admin_users_api_still_requires_admin_role(client):
    """確認這次只是加前端連結，没有連帶放寬任何 /api/admin/* 的
    authorization —— 非管理員打 API 一樣要被擋。
    """
    login_as(client, role_name="一般使用者")

    response = client.get("/api/admin/users")

    assert response.status_code == 403
