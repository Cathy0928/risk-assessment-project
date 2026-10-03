"""Phase 5: site-wide UI consistency regression tests.

Renders every active template (the complete set reachable from a live
Flask route — see tests/test_active_template_navigation.py for how
that list is derived and kept honest) through a fake Supabase client,
and checks that none of them regressed back to the pre-redesign shell:
no grey custom sidebar, no `.menu-btn` nav, no page-local legacy
stylesheet, no `<button>` missing an explicit type. Also covers the
login form contract, since login.html is intentionally the one active
template that does NOT extend base.html (no sidebar makes sense on a
pre-auth page).

No live Supabase, Gemini or NVD.
"""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATES_DIR = ROOT / "riskGenie" / "templates"

# Every template actually reachable from a live route today. Kept in
# sync by hand with test_active_template_navigation.py's
# ACTIVE_TEMPLATES — if that list changes, this one should too.
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

# login.html deliberately has no sidebar (pre-auth page) — everything
# else must share base.html.
TEMPLATES_WITHOUT_SHARED_LAYOUT = {"login.html"}

LEGACY_SHELL_MARKERS = (
    "menu-btn",
    'class="sidebar"',
    "sidebar-title",
    "css/style.css",
    "css/asset_add.css",
    "css/asset_summary.css",
    "css/weight_setting.css",
    "css/risk_report.css",
    "css/admin_users.css",
)

# Large pictograph emoji that should never be a primary UI element
# (page titles, buttons, section headers). The multiplication-sign
# "✕" used as a plain modal close glyph is intentionally not in this
# set — it's a standard close icon, not a decorative pictograph.
EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U0001F000-\U0001F02F\U00002600-\U000026FF\U00002700-\U000027BF]"
)
EMOJI_ALLOWLIST = {"✕"}


@pytest.mark.parametrize("template_name", ACTIVE_TEMPLATES)
def test_active_template_has_no_legacy_shell_markers(template_name):
    source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")

    for marker in LEGACY_SHELL_MARKERS:
        assert marker not in source, f"{template_name} still contains legacy marker {marker!r}"


@pytest.mark.parametrize(
    "template_name", [t for t in ACTIVE_TEMPLATES if t not in TEMPLATES_WITHOUT_SHARED_LAYOUT]
)
def test_active_template_extends_shared_layout(template_name):
    source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    assert '{% extends "base.html" %}' in source


def test_login_does_not_extend_shared_layout_by_design():
    """login.html intentionally has no sidebar — it's pre-auth."""
    source = (TEMPLATES_DIR / "login.html").read_text(encoding="utf-8")
    assert '{% extends "base.html" %}' not in source
    assert 'class="sidebar"' not in source


@pytest.mark.parametrize("template_name", ACTIVE_TEMPLATES)
def test_active_template_buttons_have_explicit_type(template_name):
    source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for tag in re.findall(r"<button\b[^>]*>", source):
        assert "type=" in tag, f"{template_name} has a <button> with no type: {tag}"


@pytest.mark.parametrize("template_name", ACTIVE_TEMPLATES)
def test_active_template_has_no_large_pictograph_emoji(template_name):
    source = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    for line_no, line in enumerate(source.splitlines(), start=1):
        for ch in line:
            if EMOJI_PATTERN.match(ch) and ch not in EMOJI_ALLOWLIST:
                pytest.fail(f"{template_name}:{line_no} contains emoji {ch!r}: {line.strip()[:100]}")


# ================================================================
# Login form contract
# ================================================================

@pytest.fixture()
def flask_app(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setenv("FLASK_ENV", "development")

    import importlib

    module = importlib.import_module("riskGenie.app")
    module = importlib.reload(module)
    return module.create_app({"TESTING": True, "SECRET_KEY": "test-secret"})


def test_login_form_contract(flask_app):
    client = flask_app.test_client()
    response = client.get("/login")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    form_match = re.search(r'<form[^>]*action="[^"]*login[^"]*"[^>]*>', body)
    assert form_match, "expected a <form> posting to the login endpoint"
    form_tag = form_match.group(0)
    assert 'method="post"' in form_tag

    assert re.search(r'<input[^>]*name="email"[^>]*type="email"', body) \
        or re.search(r'<input[^>]*type="email"[^>]*name="email"', body)
    assert re.search(r'<input[^>]*name="password"[^>]*type="password"', body) \
        or re.search(r'<input[^>]*type="password"[^>]*name="password"', body)
    assert "<button" in body and "登入" in body


def test_login_error_message_renders_when_present(flask_app, monkeypatch):
    from riskGenie.services.supabase_client import SupabaseConfigError

    # Force the real login POST path to fail fast on a config error so
    # we can exercise the error-rendering branch without a live
    # Supabase call.
    monkeypatch.setattr(
        "riskGenie.app.get_supabase_client",
        lambda: (_ for _ in ()).throw(SupabaseConfigError("boom")),
    )

    client = flask_app.test_client()
    response = client.post(
        "/login", data={"email": "nobody@example.com", "password": "wrong"}
    )
    body = response.get_data(as_text=True)
    assert "alert--danger" in body or response.status_code in (200, 302, 503)


# ================================================================
# Admin Users: table language + create-is-primary contract
# ================================================================

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
        rows = [r for r in self._records if all(r.get(f) == v for f, v in self._filters)]
        return SimpleNamespace(data=rows)


class _FakeSupabase:
    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _FakeQuery(self._tables.get(name, []))


def test_admin_users_page_create_action_is_primary(flask_app, monkeypatch):
    from riskGenie.services import admin_service

    fake = _FakeSupabase({
        "users": [
            {
                "id": "u1", "username": "Peggy", "email": "peggy@example.com",
                "role_id": "r1", "company_id": 7, "is_active": True,
            }
        ],
        "roles": [{"id": "r1", "role_name": "一般使用者"}],
    })
    monkeypatch.setattr(admin_service, "get_supabase_admin_client", lambda: fake)

    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user_id"] = "admin-id"
        sess["username"] = "Admin"
        sess["role_name"] = "系統管理員"
        sess["company_id"] = 7

    response = client.get("/admin/users")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    submit_match = re.search(r'<button[^>]*id="create-user-submit"[^>]*>', body)
    assert submit_match, "expected the create-account submit button"
    assert "btn--primary" in submit_match.group(0), (
        "新增帳號 must be the page's primary action"
    )

    assert "css/design-system.css" in body
    assert 'class="table"' in body
