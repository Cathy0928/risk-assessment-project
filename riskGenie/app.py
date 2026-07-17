# -*- coding: utf-8 -*-
"""RiskGenie Flask application."""

from functools import wraps
import os
import webbrowser

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared in requirements.txt
    def load_dotenv():
        return False

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

try:
    from .models.mock_db import (
        MOCK_VULNERABILITIES,
        get_all_assets,
        get_all_risk_assessments,
    )
    from .services.risk_service import perform_asset_risk_assessment
    from .services.supabase_client import get_supabase_client
except Exception:  # Allows UC1 auth to run even if legacy risk modules are unavailable.
    try:
        from services.supabase_client import get_supabase_client
    except ImportError:
        from .services.supabase_client import get_supabase_client

    MOCK_VULNERABILITIES = []

    def get_all_assets():
        return []

    def get_all_risk_assessments():
        return []

    def perform_asset_risk_assessment(*args, **kwargs):
        raise RuntimeError("Risk assessment service is unavailable.")

try:
    from .services import admin_service
except ImportError:  # Allows `python app.py` from inside riskGenie/.
    from services import admin_service


REQUIRED_ENV_VARS = ("FLASK_SECRET_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY")
ROLE_REDIRECTS = {}
DEFAULT_LOGIN_ENDPOINT = "home"
LOGIN_ERROR_MESSAGE = "電子郵件或密碼錯誤"
ADMIN_ROLE_NAME = "系統管理員"


def _get_response_data(response):
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("data", response)
    return getattr(response, "data", response)


def _get_user_id(auth_response):
    user = getattr(auth_response, "user", None)
    if user is None and isinstance(auth_response, dict):
        user = auth_response.get("user")
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def _single_record(response):
    data = _get_response_data(response)
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _validate_runtime_config(app):
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing and not app.config.get("TESTING"):
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variables: {joined}")

    secret_key = app.config.get("SECRET_KEY") or os.getenv("FLASK_SECRET_KEY")
    if not secret_key and not app.config.get("TESTING"):
        raise RuntimeError("Missing required environment variable: FLASK_SECRET_KEY")
    app.secret_key = secret_key or "test-secret-key"


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        if session.get("role_name") != ADMIN_ROLE_NAME:
            return jsonify({"error": "Forbidden"}), 403
        return view_func(*args, **kwargs)

    return wrapped


def _load_profile(supabase, user_id):
    user_response = (
        supabase.table("users")
        .select("id, username, email, role_id, company_id")
        .eq("id", user_id)
        .single()
        .execute()
    )
    profile = _single_record(user_response)
    if not profile:
        raise LookupError("Supabase public.users did not return a profile.")

    role_name = None
    role_id = profile.get("role_id")
    if role_id is not None:
        role_response = (
            supabase.table("roles")
            .select("role_name")
            .eq("id", role_id)
            .single()
            .execute()
        )
        role = _single_record(role_response)
        if role:
            role_name = role.get("role_name")

    return {
        "id": profile.get("id"),
        "username": profile.get("username"),
        "email": profile.get("email"),
        "role_name": role_name,
        "company_id": profile.get("company_id"),
    }


def _store_login_session(profile):
    session.clear()
    session["user_id"] = profile["id"]
    session["username"] = profile["username"]
    session["email"] = profile["email"]
    session["role_name"] = profile["role_name"]
    session["company_id"] = profile["company_id"]
    session["logged_in"] = True


def _login_redirect_endpoint(role_name):
    return ROLE_REDIRECTS.get(role_name, DEFAULT_LOGIN_ENDPOINT)


def create_app(test_config=None):
    load_dotenv()

    app = Flask(__name__)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
    )
    if test_config:
        app.config.update(test_config)

    _validate_runtime_config(app)

    @app.route("/")
    @login_required
    def home():
        return render_template("index.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("logged_in"):
            return redirect(url_for(DEFAULT_LOGIN_ENDPOINT))

        error = None
        if request.method == "POST":
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            auth_succeeded = False

            if not email or not password:
                error = "請輸入電子郵件與密碼"
            else:
                try:
                    supabase = get_supabase_client()
                    auth_response = supabase.auth.sign_in_with_password(
                        {"email": email, "password": password}
                    )
                    user_id = _get_user_id(auth_response)
                    if not user_id:
                        raise ValueError("Supabase Auth did not return a user id.")

                    auth_succeeded = True
                    profile = _load_profile(supabase, user_id)
                    _store_login_session(profile)
                    endpoint = _login_redirect_endpoint(profile.get("role_name"))
                    return redirect(url_for(endpoint))
                except Exception:
                    if auth_succeeded:
                        error = "登入成功，但無法取得使用者資料，請確認 Supabase RLS policy。"
                    else:
                        error = LOGIN_ERROR_MESSAGE

        return render_template("login.html", error=error)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/api/auth/me", methods=["GET"])
    def api_auth_me():
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify(
            {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "email": session.get("email"),
                "role": session.get("role_name"),
                "company_id": session.get("company_id"),
            }
        )

    @app.route("/api/admin/roles", methods=["GET"])
    @admin_required
    def api_admin_roles():
        try:
            return jsonify({"roles": admin_service.list_roles()})
        except Exception:
            return jsonify({"error": "Unable to load roles"}), 503

    @app.route("/api/admin/users", methods=["GET"])
    @admin_required
    def api_admin_users():
        try:
            return jsonify({"users": admin_service.list_users()})
        except Exception:
            return jsonify({"error": "Unable to load users"}), 503

    @app.route("/summary")
    def asset_summary():
        mock_assets = [
            {
                "id": 1,
                "asset_name": "Asset A",
                "owner": "Team A",
                "confidentiality": "A",
                "integrity": "A",
            },
            {
                "id": 2,
                "asset_name": "Asset B",
                "owner": "Team B",
                "confidentiality": "B",
                "integrity": "B",
            },
        ]
        return render_template("asset_summary.html", assets=mock_assets)

    @app.route("/assess")
    def risk_assessment_page():
        assets = get_all_assets()
        vulnerabilities = MOCK_VULNERABILITIES
        return render_template(
            "risk_assessment.html", assets=assets, vulnerabilities=vulnerabilities
        )

    @app.route("/api/risk/assess", methods=["POST"])
    def api_assess_risk():
        try:
            data = request.get_json()
            if not data:
                return jsonify({"status": "error", "message": "Missing JSON body."}), 400

            asset_id = data.get("asset_id")
            vulnerability_id = data.get("vulnerability_id")
            if not asset_id or not vulnerability_id:
                return (
                    jsonify(
                        {
                            "status": "error",
                            "message": "Missing required fields: asset_id, vulnerability_id.",
                        }
                    ),
                    400,
                )

            result = perform_asset_risk_assessment(int(asset_id), int(vulnerability_id))
            return jsonify({"status": "success", "data": result}), 200
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        except Exception as exc:
            return jsonify({"status": "error", "message": f"Unexpected error: {exc}"}), 500

    @app.route("/api/risk/reports", methods=["GET"])
    def api_get_risk_reports():
        try:
            reports = get_all_risk_assessments()
            return jsonify({"status": "success", "data": reports}), 200
        except Exception as exc:
            return jsonify({"status": "error", "message": f"Unable to load reports: {exc}"}), 500

    return app


app = create_app()


if __name__ == "__main__":
    webbrowser.open("http://127.0.0.1:5000")
    app.run(debug=os.getenv("FLASK_ENV") == "development", port=5000)
