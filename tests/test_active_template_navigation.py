"""Active UI / navigation regression tests.

RiskGenie has a handful of templates that are no longer rendered by any
live route (risk_assessment_result.html, asset_inventory_result.html,
setting.html). Those templates still contain bare url_for() calls that
would BuildError if anyone ever reconnected them, but since nothing
renders them today they are out of scope for "active UI" work.

This file guards two things for the templates that ARE reachable from a
real route:

1. Every url_for('...') call inside an active template points at an
   endpoint that actually exists in the Flask app (no BuildError traps
   left in the navigation a user can actually click through).
2. The dead templates listed above stay dead - nothing in app.py /
   risk_routes.py should start rendering them again without this test
   being updated on purpose.

It also confirms that the handful of page routes which need zero
Supabase access (or already fail closed and still render) come up fine
even when the privileged admin client is unavailable - i.e. without
SUPABASE_SECRET_KEY configured, which is the local dev reality right
now and must not block these pages.

Everything here is static analysis + Flask test client with a fake /
failing Supabase client. No live Supabase, no Gemini, no NVD.
"""

import importlib
import re
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATES_DIR = ROOT / "riskGenie" / "templates"

# Templates actually reachable from a live route today (render_template()
# call sites in riskGenie/app.py and riskGenie/services/risk_routes.py).
ACTIVE_TEMPLATES = (
    "login.html",
    "admin_users.html",
    "index.html",
    "asset_add.html",
    "asset_summary.html",
    "asset_missing.html",
    "asset_edit.html",
    "asset_delete.html",
    "weight_setting.html",
    "risk_assessment.html",
    "risk_report.html",
    "ai_advice.html",
)

# Templates with no render_template() call site anywhere in the Flask
# app. Known to still contain broken bare url_for() calls - that is
# acceptable only because nothing serves them. If one of these ever
# gets wired back up, this list (and the BuildError it would start
# throwing) needs to be dealt with on purpose, not silently.
DEAD_TEMPLATES = (
    "risk_assessment_result.html",
    "asset_inventory_result.html",
    "setting.html",
)

URL_FOR_PATTERN = re.compile(r"url_for\(\s*'([a-zA-Z_.][a-zA-Z0-9_.]*)'")


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
        sess["company_id"] = company_id


def _valid_endpoints(app):
    return {rule.endpoint for rule in app.url_map.iter_rules()}


@pytest.mark.parametrize("template_name", ACTIVE_TEMPLATES)
def test_active_template_url_for_calls_resolve_to_real_endpoints(
    app, template_name
):
    valid_endpoints = _valid_endpoints(app)
    source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")

    referenced = set(URL_FOR_PATTERN.findall(source))
    # 'static' is always registered by Flask itself.
    referenced.discard("static")

    missing = sorted(referenced - valid_endpoints)

    assert not missing, (
        f"{template_name} 的 url_for() 指向不存在的 endpoint：{missing}。"
        "使用者點擊對應連結會得到 BuildError（500）。"
    )


@pytest.mark.parametrize("template_name", DEAD_TEMPLATES)
def test_dead_templates_stay_unreferenced_by_any_route(template_name):
    """確保本輪（以及未來）沒有不小心把死 template 重新接回任何
    render_template() 呼叫。這些 template 裡面還留著壞掉的
    url_for()，一旦被接回去就會立刻 BuildError。
    """
    app_source = (ROOT / "riskGenie" / "app.py").read_text(encoding="utf-8")
    routes_source = (
        ROOT / "riskGenie" / "services" / "risk_routes.py"
    ).read_text(encoding="utf-8")

    stem = template_name.replace(".html", "")
    needle_double = f'"{stem}.html"'
    needle_single = f"'{stem}.html'"

    assert needle_double not in app_source
    assert needle_single not in app_source
    assert needle_double not in routes_source
    assert needle_single not in routes_source


def test_risk_report_page_renders_200_without_admin_client(
    client, monkeypatch
):
    """/risk-report 做零 DB 存取，所以就算 privileged admin client
    完全壞掉（本機沒有 SUPABASE_SECRET_KEY 就是這個狀態），這頁也
    不該受影響。
    """
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    def fail_admin_client():
        raise SupabaseConfigError("Missing required environment variable: SUPABASE_SECRET_KEY")

    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )
    login_as(client)

    response = client.get("/risk-report")

    assert response.status_code == 200


def test_risk_assessment_page_renders_200_without_admin_client(
    client, monkeypatch
):
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    def fail_admin_client():
        raise SupabaseConfigError("Missing required environment variable: SUPABASE_SECRET_KEY")

    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )
    login_as(client)

    response = client.get("/risk_assessment")

    assert response.status_code == 200


def test_ai_advice_page_renders_200_even_when_admin_client_fails(
    client, monkeypatch
):
    """ai_advice_page() 會先用 anon client 查 assets、再用 admin
    client 查『目前公司是否已有評鑑紀錄』，但整段查詢包在
    try/except 裡，任何一邊失敗都只會把 has_assessment 設為
    False，頁面仍然要能正常顯示 —— 不可以因為 admin client 壞掉
    就連頁面都開不起來。anon client 這裡也給一個假的、回空清單的
    client，避免真的對外發出網路請求。
    """
    from riskGenie.services import risk_routes
    from riskGenie.services.supabase_client import SupabaseConfigError

    class _EmptyAssetsQuery:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[])

    class _EmptyAnonClient:
        def table(self, _name):
            return _EmptyAssetsQuery()

    def fail_admin_client():
        raise SupabaseConfigError("Missing required environment variable: SUPABASE_SECRET_KEY")

    monkeypatch.setattr(
        risk_routes, "get_supabase_client", lambda: _EmptyAnonClient()
    )
    monkeypatch.setattr(
        risk_routes, "get_supabase_admin_client", fail_admin_client
    )
    login_as(client)

    response = client.get("/ai-advice")

    assert response.status_code == 200


def test_login_page_renders_200_without_any_supabase_call(client):
    response = client.get("/login")

    assert response.status_code == 200
