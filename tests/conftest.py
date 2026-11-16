"""Shared test doubles for request-time authorization refreshes."""

import pytest
from flask import session


@pytest.fixture(autouse=True)
def current_authorization_profile_stub(request, monkeypatch):
    """Keep existing route tests isolated from live Supabase authorization."""
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-secret-key")
    if "app_module" not in request.fixturenames:
        return

    app_module = request.getfixturevalue("app_module")

    def load_current_profile(user_id):
        return {
            "id": user_id,
            "username": session.get("username", "Test User"),
            "email": session.get("email", "test@example.com"),
            "role_name": session.get("role_name") or session.get("role"),
            "company_id": session.get("company_id"),
            "is_active": True,
        }

    monkeypatch.setattr(
        app_module,
        "_load_current_authorization_profile",
        load_current_profile,
    )
