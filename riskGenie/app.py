# -*- coding: utf-8 -*-

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
    session,
    jsonify
)

from functools import wraps
from datetime import datetime
from pathlib import Path

import os
import re
import sys
import webbrowser
import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


# ===============================
# 環境與路徑設定 (解決 ModuleNotFoundError)
# ===============================

# 將專案根目錄與 riskGenie 目錄加入 sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RISKGENIE_DIR = os.path.join(BASE_DIR, "riskGenie")

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
if RISKGENIE_DIR not in sys.path:
    sys.path.insert(0, RISKGENIE_DIR)

# 載入 Blueprint
try:
    from riskGenie.services.risk_routes import risk_bp
    from riskGenie.services import admin_service, backup_service
    from riskGenie.services.supabase_client import (
        SupabaseConfigError,
        get_supabase_client,
    )
    from riskGenie.services.asset_service import get_assets

except ImportError:
    from services.risk_routes import risk_bp
    from services import admin_service, backup_service
    from services.supabase_client import (
        SupabaseConfigError,
        get_supabase_client
    )
    from services.asset_service import get_assets

REQUIRED_ENV_VARS = ("FLASK_SECRET_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY")
LOGIN_ERROR_MESSAGE = "電子郵件或密碼錯誤"
ACCOUNT_DISABLED_MESSAGE = "帳號已停用，請聯絡系統管理員"
ADMIN_ROLE_NAME = "系統管理員"
COMPANY_CONTEXT_ERROR = "帳號缺少公司識別資訊"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CREATE_USER_FIELDS = {"username", "email", "password", "role_id"}
UPDATE_USER_FIELDS = {"username", "role_id"}


class AccountDisabledError(RuntimeError):
    """Raised when an authenticated user has a disabled public profile."""


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


class _LazySupabaseClient:
    def __init__(self):
        self._client = None

    def __getattr__(self, name):
        if self._client is None:
            self._client = get_supabase_client()
        return getattr(self._client, name)


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

    try:
        active_response = (
            supabase.table("users")
            .select("is_active")
            .eq("id", user_id)
            .single()
            .execute()
        )
        active_profile = _single_record(active_response)
        if active_profile and active_profile.get("is_active") is False:
            raise AccountDisabledError("The user account is disabled.")
    except AccountDisabledError:
        raise
    except Exception as exc:
        message = str(exc).lower()
        missing_column = "is_active" in message and any(
            token in message for token in ("column", "schema", "field", "pgrst")
        )
        if not missing_column:
            raise

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
    session["role"] = profile["role_name"]
    session["role_name"] = profile["role_name"]
    session["company_id"] = profile["company_id"]
    session["logged_in"] = True


def _validation_error(field, message):
    return {"error": "Validation failed", "field": field, "message": message}


def _validate_company_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _session_company_id():
    company_id = session.get("company_id")
    return company_id if _validate_company_id(company_id) else None


def _session_role_name():
    return session.get("role") or session.get("role_name")


def _wants_json_response():
    accept = request.headers.get("Accept", "")
    return request.is_json or "application/json" in accept


def _company_context_error_response(json_response=False):
    if json_response:
        return jsonify({"success": False, "error": COMPANY_CONTEXT_ERROR}), 403
    return COMPANY_CONTEXT_ERROR, 403


def _validate_create_user_payload(payload):
    if not isinstance(payload, dict):
        return None, _validation_error("body", "A JSON object is required.")

    if "company_id" in payload:
        return None, _validation_error(
            "company_id", "company_id cannot be specified by the client."
        )

    missing = [field for field in CREATE_USER_FIELDS if field not in payload]
    if missing:
        return None, _validation_error(
            missing[0], f"Missing required field: {missing[0]}"
        )

    username = payload.get("username")
    email = payload.get("email")
    password = payload.get("password")
    role_id = payload.get("role_id")

    if not isinstance(username, str) or not username.strip():
        return None, _validation_error("username", "username must not be blank.")
    if not isinstance(email, str) or not EMAIL_PATTERN.fullmatch(email.strip()):
        return None, _validation_error("email", "email format is invalid.")
    if not isinstance(password, str) or len(password) < 8:
        return None, _validation_error(
            "password", "password must be at least 8 characters."
        )
    if not isinstance(role_id, str) or not role_id.strip():
        return None, _validation_error("role_id", "role_id must not be blank.")

    return {
        "username": username.strip(),
        "email": email.strip().lower(),
        "password": password,
        "role_id": role_id.strip(),
    }, None


def _validate_update_user_payload(payload):
    if not isinstance(payload, dict):
        return None, _validation_error("body", "A JSON object is required.")
    if not payload:
        return None, _validation_error("body", "At least one field is required.")

    unsupported = set(payload) - UPDATE_USER_FIELDS
    if unsupported:
        field = sorted(unsupported)[0]
        return None, _validation_error(field, f"{field} cannot be changed.")

    changes = {}
    if "username" in payload:
        username = payload["username"]
        if not isinstance(username, str) or not username.strip():
            return None, _validation_error(
                "username", "username must not be blank."
            )
        changes["username"] = username.strip()

    if "role_id" in payload:
        role_id = payload["role_id"]
        if not isinstance(role_id, str) or not role_id.strip():
            return None, _validation_error("role_id", "role_id must not be blank.")
        changes["role_id"] = role_id.strip()

    return changes, None


def _audit_admin_action(action, status):
    try:
        admin_service.write_audit_log(
            operator_id=session.get("user_id"),
            action=action,
            ip_address=request.remote_addr,
            status=status,
        )
    except Exception:
        pass


# ===============================
# 登入驗證裝飾器
# ===============================

def login_required(view_func):

    @wraps(view_func)
    def wrapped(*args, **kwargs):

        if not session.get("logged_in"):

            return redirect(
                url_for("login")
            )

        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        if _session_role_name() != ADMIN_ROLE_NAME:
            return jsonify({"error": "Forbidden"}), 403
        return view_func(*args, **kwargs)

    return wrapped


# ===============================
# Flask App
# ===============================

def create_app(test_config=None):

    app = Flask(__name__)

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
    )
    if test_config:
        app.config.update(test_config)
    _validate_runtime_config(app)

    supabase = _LazySupabaseClient()


    # ===============================
    # 註冊 Blueprint 模組
    # ===============================
    app.register_blueprint(risk_bp)


    # ===============================
    # 登入
    # ===============================

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("logged_in"):
            return redirect(url_for("home"))
        error = None

        if request.method == "POST":
            email = request.form.get("email", "").strip()
            password = request.form.get("password", "")
            auth_succeeded = False

            if not email or not password:
                error = "請輸入電子郵件與密碼"
            else:
                try:
                    auth_client = get_supabase_client()
                    auth_response = auth_client.auth.sign_in_with_password(
                        {"email": email, "password": password}
                    )
                    user_id = _get_user_id(auth_response)
                    if not user_id:
                        raise ValueError("Supabase Auth did not return a user id.")

                    auth_succeeded = True
                    profile = _load_profile(auth_client, user_id)
                    _store_login_session(profile)
                    return redirect(url_for("home"))
                except AccountDisabledError:
                    session.clear()
                    try:
                        auth_client.auth.sign_out()
                    except Exception:
                        pass
                    error = ACCOUNT_DISABLED_MESSAGE
                except Exception:
                    if auth_succeeded:
                        error = (
                            "登入成功，但無法取得使用者資料，"
                            "請確認 Supabase RLS policy。"
                        )
                    else:
                        error = LOGIN_ERROR_MESSAGE

        return render_template(
            "login.html",
            error=error
        )



    # ===============================
    # 登出
    # ===============================

    @app.route("/logout", methods=["POST"])
    def logout():

        session.clear()


        return redirect(
            url_for("login")
        )

    @app.route("/api/auth/me", methods=["GET"])
    def api_auth_me():
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify(
            {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "email": session.get("email"),
                "role": _session_role_name(),
                "company_id": session.get("company_id"),
            }
        )

    @app.route("/admin/users", methods=["GET"])
    @admin_required
    def admin_users_page():
        if _session_company_id() is None:
            return jsonify({"error": "Company context is required."}), 403
        return render_template("admin_users.html")

    @app.route("/api/admin/roles", methods=["GET"])
    @admin_required
    def api_admin_roles():
        action = "LIST_ROLES"
        if _session_company_id() is None:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company context is required."}), 403
        try:
            roles = admin_service.list_roles()
        except Exception:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Unable to load roles"}), 503
        _audit_admin_action(action, "success")
        return jsonify({"roles": roles})

    @app.route("/api/admin/users", methods=["GET"])
    @admin_required
    def api_admin_users():
        action = "LIST_USERS"
        company_id = _session_company_id()
        if company_id is None:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company context is required."}), 403
        try:
            users = admin_service.list_users(company_id)
        except Exception:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Unable to load users"}), 503
        _audit_admin_action(action, "success")
        return jsonify({"users": users})

    @app.route("/api/admin/users", methods=["POST"])
    @admin_required
    def api_admin_create_user():
        action = "CREATE_USER"
        company_id = _session_company_id()
        if company_id is None:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company context is required."}), 403

        payload, validation_error = _validate_create_user_payload(
            request.get_json(silent=True)
        )
        if validation_error:
            _audit_admin_action(action, "failed")
            return jsonify(validation_error), 400

        try:
            user = admin_service.create_user(company_id=company_id, **payload)
        except admin_service.DuplicateEmailError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Email is already in use."}), 409
        except admin_service.CompanyNotFoundError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company does not exist."}), 400
        except SupabaseConfigError as exc:
            _audit_admin_action(action, "failed")
            return jsonify({"error": str(exc)}), 503
        except admin_service.ProfileCreationError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Unable to create user profile."}), 503
        except Exception:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Unable to create user."}), 503

        _audit_admin_action(action, "success")
        return jsonify({"user": user}), 201

    @app.route("/api/admin/users/<user_id>", methods=["PATCH"])
    @admin_required
    def api_admin_update_user(user_id):
        action = "UPDATE_USER"
        company_id = _session_company_id()
        if company_id is None:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company context is required."}), 403

        changes, validation_error = _validate_update_user_payload(
            request.get_json(silent=True)
        )
        if validation_error:
            _audit_admin_action(action, "failed")
            return jsonify(validation_error), 400

        try:
            user = admin_service.update_user(user_id, changes, company_id)
        except admin_service.UserNotFoundError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "User not found."}), 404
        except SupabaseConfigError as exc:
            _audit_admin_action(action, "failed")
            return jsonify({"error": str(exc)}), 503
        except Exception:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Unable to update user."}), 503

        _audit_admin_action(action, "success")
        return jsonify({"user": user})

    @app.route("/api/admin/users/<user_id>/disable", methods=["POST"])
    @admin_required
    def api_admin_disable_user(user_id):
        action = "DISABLE_USER"
        company_id = _session_company_id()
        if company_id is None:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company context is required."}), 403

        if user_id == session.get("user_id"):
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Administrators cannot disable themselves."}), 400

        try:
            user, already_disabled = admin_service.disable_user(user_id, company_id)
        except admin_service.UserNotFoundError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "User not found."}), 404
        except admin_service.UserStatusConfigError as exc:
            _audit_admin_action(action, "failed")
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "code": "USER_STATUS_COLUMN_MISSING",
                    }
                ),
                503,
            )
        except SupabaseConfigError as exc:
            _audit_admin_action(action, "failed")
            return jsonify({"error": str(exc)}), 503
        except Exception:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Unable to disable user."}), 503

        _audit_admin_action(action, "success")
        return jsonify(
            {
                "user": user,
                "already_disabled": already_disabled,
            }
        )

    def write_backup_audit(status):
        try:
            admin_service.write_audit_log(
                operator_id=session.get("user_id"),
                action="EXPORT_BACKUP",
                ip_address=request.remote_addr,
                status=status,
            )
        except Exception as exc:
            app.logger.warning(
                "Unable to write backup audit log (%s).",
                type(exc).__name__,
            )

    @app.route("/api/admin/backups/export", methods=["POST"])
    @admin_required
    def api_admin_export_backup():
        company_id = _session_company_id()
        if company_id is None:
            write_backup_audit("failed")
            return jsonify({"error": "Company scope is required."}), 403

        try:
            export = backup_service.create_backup_archive(
                generated_by=session.get("user_id"),
                company_id=company_id,
            )
            response = send_file(
                export["stream"],
                mimetype="application/zip",
                as_attachment=True,
                download_name=export["filename"],
                max_age=0,
            )
        except backup_service.BackupUnavailableError:
            write_backup_audit("failed")
            return jsonify({"error": "Unable to export backup."}), 503
        except Exception:
            write_backup_audit("failed")
            return jsonify({"error": "Unable to export backup."}), 503

        write_backup_audit("success")
        return response

    # ===============================
    # 稽核紀錄
    # ===============================

    def create_log(
        action,
        asset_id=None,
        asset_code=None,
        status="成功"
    ):

        log = {

            "user_id": session.get(
                "user_id"
            ),

            "action": action,

            "asset_id": asset_id,

            "ip_address": request.remote_addr,

            "status": status,

            "log_time": datetime.now().isoformat()

        }

        if asset_code:
            app.logger.info(
                "Audit asset_code context for %s: %s",
                action,
                asset_code,
            )

        try:
            supabase.table(
                "audit_logs"
            ).insert(
                log
            ).execute()
            return True
        except Exception:
            app.logger.exception("Unable to write audit log for %s.", action)
            return False

    # ===============================
    # 首頁
    # ===============================

    @app.route("/")
    @login_required
    def home():
        company_id = _session_company_id()
        if company_id is None:
            return _company_context_error_response(_wants_json_response())

        result = (
            supabase
            .table("assets")
            .select("*")
            .eq("company_id", company_id)
            .eq("status", "active")
            .order("id", desc=True)
            .limit(10)
            .execute()
        )


        assets = result.data
        print("首頁資產:", assets)

        for asset in assets:

            if asset.get("created_at"):

                asset["created_at"] = (
                    asset["created_at"]
                    .replace("T", " ")
                    .split("+")[0]
                )


        return render_template(
            "index.html",
            assets=assets
        )



    # ===============================
    # 下載 Excel 範本
    # ===============================

    @app.route("/download_template")
    @login_required
    def download_template():

        path = os.path.join(
            os.path.dirname(__file__),
            "資產匯入範本.xlsx"
        )


        return send_file(
            path,
            as_attachment=True
        )



    # ===============================
    # Excel 匯入資產
    # ===============================

    @app.route(
        "/upload_excel",
        methods=["POST"]
    )
    @login_required
    def upload_excel():
        company_id = _session_company_id()
        if company_id is None:
            return _company_context_error_response(_wants_json_response())


        file = request.files.get("file")

        if not file:
            return "沒有選擇檔案",400


        if file and file.filename.endswith(".xlsx"):


                df = pd.read_excel(file)


                # 必要欄位檢查
                required_columns = [
                    "資產代碼",
                    "資產類型",
                    "資料類型",
                    "資產名稱"
                ]


                for col in required_columns:
                    if col not in df.columns:
                        return f"缺少欄位:{col}", 400



                # 開始逐筆匯入
                for _, row in df.iterrows():


                    asset = {

                        "asset_id_code":
                            str(row["資產代碼"]),


                        "asset_type":
                            row["資產類型"],


                        "data_type":
                            row["資料類型"],


                        "asset_name":
                            row["資產名稱"],


                        "description":
                            row.get("資產描述", ""),


                        "department":
                            row.get("權責單位", ""),


                        "risk_owner":
                            row.get("保管單位(風險擁有者)", ""),


                        "use_department":
                            row.get("使用單位", ""),


                        "location":
                            row.get("放置地點", ""),


                        "confidentiality":
                            str(row.get("機密性", 0)),


                        "integrity":
                            str(row.get("完整性", 0)),


                        "availability":
                            str(row.get("可用性", 0)),


                        "legality":
                            str(row.get("適法性", 0)),


                        "asset_value":
                            max(
                                int(row.get("機密性", 0)),
                                int(row.get("完整性", 0)),
                                int(row.get("可用性", 0)),
                                int(row.get("適法性", 0))
                            ),


                        "upload_user":
                            session.get(
                                "username",
                                "admin"
                            ),


                        "created_at":
                            datetime.now().isoformat(),


                        "company_id":
                            company_id,
                        "status":
                            "active"

                    }



                    # 檢查資產代碼是否重複
                    exist = (
                        supabase.table("assets")
                        .select("id")
                        .eq("company_id", company_id)
                        .eq("asset_id_code", asset["asset_id_code"])
                        .eq("status", "active")
                        .eq("is_deleted", False)
                        .execute()
                    )



                    # 不存在才新增
                    if not exist.data:


                        result = (
                            supabase
                            .table("assets")
                            .insert(asset)
                            .execute()
                        )


                        if result.data:

                            create_log(
                                action="Excel匯入資產",
                                asset_id=result.data[0]["id"]
                            )


                return redirect(
                    url_for("home")
                )



    # ===============================
    # 新增資產
    # ===============================

    @app.route("/asset_add", methods=["GET", "POST"])
    @login_required
    def asset_add():
        company_id = _session_company_id()
        if company_id is None:
            return _company_context_error_response(_wants_json_response())


        if request.method == "POST":


            asset = {


                "asset_id_code":
                    request.form["asset_id_code"],


                "asset_type":
                    request.form["asset_type"],


                "data_type":
                    request.form["data_type"],


                "asset_name":
                    request.form["asset_name"],


                "description":
                    request.form["description"],



                "department":
                    request.form["department"],



                "risk_owner":
                    request.form["risk_owner"],



                "use_department":
                    request.form["use_department"],



                "location":
                    request.form["location"],



                "confidentiality":
                    request.form["confidentiality"],



                "integrity":
                    request.form["integrity"],



                "availability":
                    request.form["availability"],



                "legality":
                    request.form["legality"],



                "asset_value":
                    max(
                        int(request.form["confidentiality"]),
                        int(request.form["integrity"]),
                        int(request.form["availability"]),
                        int(request.form["legality"])
                    ),



                "upload_user":
                    session.get(
                        "username",
                        "admin"
                    ),



                "created_at":
                    datetime.now().isoformat(),


                "company_id":
                    company_id,
                
                "status":
                    "active"

            }



            # 檢查重複資產代碼

            exist = (
                supabase.table("assets")
                .select("id")
                .eq("company_id", company_id)
                .eq("asset_id_code", asset["asset_id_code"])
                .eq("status", "active")
                .eq("is_deleted", False)
                .execute()
            )



            if exist.data:

                return render_template(
                    "asset_add.html",
                    error="❌ 資產代碼已存在",
                    form=request.form
                )



            result = (
                supabase
                .table("assets")
                .insert(asset)
                .execute()
            )



            new_asset = result.data[0]


            create_log(
                action="新增資產",
                asset_id=new_asset["id"]
            )



            return redirect(
                url_for("home")
            )



        return render_template(
            "asset_add.html"
        )

    # ===============================
    # 資產總表
    # ===============================

    @app.route("/summary")
    @login_required
    def asset_summary():
        company_id = _session_company_id()
        if company_id is None:
            return _company_context_error_response(_wants_json_response())


        asset_id_code = request.args.get(
            "asset_id_code",
            ""
        )

        asset_name = request.args.get(
            "asset_name",
            ""
        )

        asset_type = request.args.get(
            "asset_type",
            ""
        )

        department = request.args.get(
            "department",
            ""
        )

        risk_owner = request.args.get(
            "risk_owner",
            ""
        )

        asset_value = request.args.get(
            "asset_value",
            ""
        )



        result = (
            supabase
            .table("assets")
            .select("*")
            .eq("company_id", company_id)
            .eq("status", "active")
            .execute()
            .data
        )



        for asset in result:

            if asset.get("created_at"):

                asset["created_at"] = (
                    asset["created_at"]
                    .replace("T", " ")
                    .split("+")[0]
                )



        if asset_id_code:

            result = [
                a for a in result
                if asset_id_code.lower()
                in (a.get("asset_id_code") or "").lower()
            ]



        if asset_name:

            result = [
                a for a in result
                if asset_name.lower()
                in a["asset_name"].lower()
            ]



        if asset_type:

            result = [
                a for a in result
                if a["asset_type"] == asset_type
            ]



        if department:

            result = [
                a for a in result
                if department.lower()
                in (a.get("department") or "").lower()
            ]



        if risk_owner:

            result = [
                a for a in result
                if risk_owner.lower()
                in (a.get("risk_owner") or "").lower()
            ]



        if asset_value:

            result = [
                a for a in result
                if str(a["asset_value"])
                == asset_value
            ]



        return render_template(
            "asset_summary.html",
            assets=result
        )

    @app.route("/asset_missing")
    @login_required
    def asset_missing():

        company_id = _session_company_id()

        if company_id is None:
            return _company_context_error_response(
                _wants_json_response()
            )


        assets = (
                    supabase
                    .table("assets")
                    .select("*")
                    .eq(
                        "company_id",
                        company_id
                    )
                    .eq(
                        "status",
                        "active"
                    )
                    .execute()
                    .data
                )


        missing_assets = []


        for asset in assets:

            errors = []


            check_fields = {

                "asset_id_code": "缺少資產代碼",

                "asset_name": "缺少資產名稱",

                "asset_type": "缺少資產類型",

                "data_type": "缺少資料類型",

                "department": "缺少權責單位",

                "risk_owner": "缺少保管單位(風險擁有者)",

                "use_department": "缺少使用單位",

                "location": "缺少放置地點",

                "description": "缺少資產描述",

                "confidentiality": "缺少機密性(C)",

                "integrity": "缺少完整性(I)",

                "availability": "缺少可用性(A)",

                "legality": "缺少適法性(L)"

            }

            for field, message in check_fields.items():

                value = asset.get(field)


                if value is None or str(value).strip()=="":
                    errors.append(message)



            if errors:

                missing_assets.append({

                    "asset": asset,

                    "errors": errors

                })



        return render_template(

            "asset_missing.html",

            missing_assets=missing_assets,

            total=len(assets)

        )


       

    # ===============================
    # 修改資產
    # ===============================

    @app.route("/asset_edit/<int:id>", methods=["GET", "POST"])
    @login_required
    def asset_edit(id):
        company_id = _session_company_id()
        if company_id is None:
            return _company_context_error_response(_wants_json_response())


        asset_response = (
            supabase
            .table("assets")
            .select("*")
            .eq("id", id)
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        asset = _single_record(asset_response)
        if not asset:
            return "找不到資產", 404



        if request.method == "POST":

            new_code = request.form["asset_id_code"]

            exist = (
                supabase.table("assets")
                .select("id")
                .eq("company_id", company_id)
                .eq("asset_id_code", new_code)
                .eq("status", "active")
                .eq("is_deleted", False)
                .execute()
            )

            for item in exist.data:
                if item["id"] != id:
                    return render_template(
                        "asset_edit.html",
                        asset={
                            **asset,
                            **request.form.to_dict()
                        },
                        error="❌ 資產代碼已存在"
                    )
            update_data = {


                "asset_id_code":
                    request.form["asset_id_code"],


                "asset_name":
                    request.form["asset_name"],


                "asset_type":
                    request.form["asset_type"],


                "data_type":
                    request.form["data_type"],


                "description":
                    request.form["description"],



                "department":
                    request.form["department"],



                "risk_owner":
                    request.form["risk_owner"],



                "use_department":
                    request.form["use_department"],



                "location":
                    request.form["location"],



                "confidentiality":
                    int(request.form["confidentiality"]),


                "integrity":
                    int(request.form["integrity"]),


                "availability":
                    int(request.form["availability"]),


                "legality":
                    int(request.form["legality"]),



                "asset_value":
                    max(
                        int(request.form["confidentiality"]),
                        int(request.form["integrity"]),
                        int(request.form["availability"]),
                        int(request.form["legality"])
                    )

            }



            supabase.table(
                "assets"
            ).update(
                update_data
            ).eq(
                "id",
                id
            ).eq(
                "company_id",
                company_id
            ).execute()



            create_log(
                action="修改資產",
                asset_id=id
            )



            return redirect(
                url_for("asset_summary")
            )



        return render_template("asset_edit.html", asset=asset, asset_id=id)



    # ===============================
    # 刪除資產
    # ===============================

    @app.route("/asset_delete/<int:id>", methods=["GET", "POST"])
    @login_required
    def asset_delete(id):
        company_id = _session_company_id()
        if company_id is None:
            return _company_context_error_response(_wants_json_response())


        asset_response = (
            supabase
            .table("assets")
            .select("*")
            .eq("id", id)
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
        asset = _single_record(asset_response)
        if not asset:
            return "找不到資產", 404



        if request.method == "POST":


            try:

                update_result = (
                    supabase
                    .table("assets")
                    .update(
                        {
                            "status": "inactive","is_deleted": True
                        }
                    )
                    .eq(
                        "id",
                        id
                    )
                    .eq(
                        "company_id",
                        company_id
                    )
                    .execute()
                )


                print("停用結果:", update_result.data)


                if not update_result.data:

                    create_log(
                        action="刪除資產",
                        asset_id=None,
                        asset_code=asset.get("asset_id_code"),
                        status="失敗"
                    )

                    return "停用資產失敗",500



                create_log(
                    action="刪除資產",
                    asset_id=id,
                    asset_code=asset.get("asset_id_code")
                )


            except Exception as e:
                print("刪除錯誤：", e)

                create_log(
                    action="刪除資產",
                    asset_id=None,
                    asset_code=asset.get("asset_id_code"),
                    status="失敗"
                )

                return f"刪除資產失敗：{e}", 500
                        



            return redirect(
                url_for("asset_summary")
            )



        return render_template(
            "asset_delete.html",
            asset=asset
        )


    # ===============================
    # 權重設定（相容舊有 url_for('weight_setting') 指向 Blueprint）
    # ===============================
    @app.route('/weight-setting', endpoint='weight_setting')
    @login_required
    def weight_setting():
        return redirect(url_for('risk.weight_setting_page'))


    return app


app = create_app()
print(app.url_map)

if __name__ == "__main__":
    import os
    # 避免 Flask debug reloader 重複開啟瀏覽器
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        webbrowser.open("http://127.0.0.1:5000")

    app.run(
        debug=True,
        port=5000
    )
