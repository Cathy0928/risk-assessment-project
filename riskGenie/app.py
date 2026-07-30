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

import os
import re
import webbrowser
import pandas as pd

try:
    from .services import admin_service, backup_service
    from .services.supabase_client import SupabaseConfigError, get_supabase_client
except ImportError:  # Allows `python app.py` from inside riskGenie/.
    from services import admin_service, backup_service
    from services.supabase_client import SupabaseConfigError, get_supabase_client


REQUIRED_ENV_VARS = ("FLASK_SECRET_KEY", "SUPABASE_URL", "SUPABASE_ANON_KEY")
LOGIN_ERROR_MESSAGE = "電子郵件或密碼錯誤"
ACCOUNT_DISABLED_MESSAGE = "帳號已停用，請聯絡系統管理員"
ADMIN_ROLE_NAME = "系統管理員"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CREATE_USER_FIELDS = {"username", "email", "password", "role_id", "company_id"}
UPDATE_USER_FIELDS = {"username", "role_id", "company_id"}


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
    session["role_name"] = profile["role_name"]
    session["company_id"] = profile["company_id"]
    session["logged_in"] = True


def _validation_error(field, message):
    return {"error": "Validation failed", "field": field, "message": message}


def _validate_company_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_create_user_payload(payload):
    if not isinstance(payload, dict):
        return None, _validation_error("body", "A JSON object is required.")

    missing = [field for field in CREATE_USER_FIELDS if field not in payload]
    if missing:
        return None, _validation_error(
            missing[0], f"Missing required field: {missing[0]}"
        )

    username = payload.get("username")
    email = payload.get("email")
    password = payload.get("password")
    role_id = payload.get("role_id")
    company_id = payload.get("company_id")

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
    if not _validate_company_id(company_id):
        return None, _validation_error(
            "company_id", "company_id must be a positive integer."
        )

    return {
        "username": username.strip(),
        "email": email.strip().lower(),
        "password": password,
        "role_id": role_id.strip(),
        "company_id": company_id,
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

    if "company_id" in payload:
        company_id = payload["company_id"]
        if not _validate_company_id(company_id):
            return None, _validation_error(
                "company_id", "company_id must be a positive integer."
            )
        changes["company_id"] = company_id

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
        if session.get("role_name") != ADMIN_ROLE_NAME:
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

        return render_template("login.html", error=error)



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
                "role": session.get("role_name"),
                "company_id": session.get("company_id"),
            }
        )

    @app.route("/admin/users", methods=["GET"])
    @admin_required
    def admin_users_page():
        return render_template("admin_users.html")

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

    @app.route("/api/admin/users", methods=["POST"])
    @admin_required
    def api_admin_create_user():
        action = "CREATE_USER"
        payload, validation_error = _validate_create_user_payload(
            request.get_json(silent=True)
        )
        if validation_error:
            _audit_admin_action(action, "failed")
            return jsonify(validation_error), 400

        try:
            user = admin_service.create_user(**payload)
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
        changes, validation_error = _validate_update_user_payload(
            request.get_json(silent=True)
        )
        if validation_error:
            _audit_admin_action(action, "failed")
            return jsonify(validation_error), 400

        try:
            user = admin_service.update_user(user_id, changes)
        except admin_service.UserNotFoundError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "User not found."}), 404
        except admin_service.CompanyNotFoundError:
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Company does not exist."}), 400
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
        if user_id == session.get("user_id"):
            _audit_admin_action(action, "failed")
            return jsonify({"error": "Administrators cannot disable themselves."}), 400

        try:
            user, already_disabled = admin_service.disable_user(user_id)
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
        company_id = session.get("company_id")
        if company_id is None:
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

            "asset_code": asset_code,

            "ip_address": request.remote_addr,

            "status": status,

            "log_time": datetime.now().isoformat()

        }


        supabase.table(
            "audit_logs"
        ).insert(
            log
        ).execute()

    # ===============================
    # 首頁
    # ===============================

    @app.route("/")
    @login_required
    def home():

        result = (
            supabase
            .table("assets")
            .select("*")
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


        file = request.files["file"]


        if file and file.filename.endswith(".xlsx"):


            df = pd.read_excel(file)



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
                        row["資產描述"],



                    "department":
                        row["權責單位"],



                    "risk_owner":
                        row["保管單位(風險擁有者)"],



                    "use_department":
                        row["使用單位"],



                    "location":
                        row["放置地點"],



                    "confidentiality":
                        str(row["機密性"]),



                    "integrity":
                        str(row["完整性"]),



                    "availability":
                        str(row["可用性"]),



                    "legality":
                        str(row["適法性"]),



                    "asset_value":
                        max(
                            int(row["機密性"]),
                            int(row["完整性"]),
                            int(row["可用性"]),
                            int(row["適法性"])
                        ),



                    "upload_user":
                        session.get(
                            "username",
                            "admin"
                        ),



                    "created_at":
                        datetime.now().isoformat()

                }



                # 檢查資產代碼重複

                exist = (
                    supabase
                    .table("assets")
                    .select("id")
                    .eq(
                        "asset_id_code",
                        asset["asset_id_code"]
                    )
                    .execute()
                )



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

    @app.route("/asset_add",methods=["GET","POST"])
    @login_required
    def asset_add():


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
                    datetime.now().isoformat()

            }



            # 檢查重複資產代碼

            exist = (
                supabase
                .table("assets")
                .select("id")
                .eq(
                    "asset_id_code",
                    asset["asset_id_code"]
                )
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
                in a["asset_id_code"].lower()
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
                in a["department"].lower()
            ]



        if risk_owner:

            result = [
                a for a in result
                if risk_owner.lower()
                in a["risk_owner"].lower()
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



    # ===============================
    # 修改資產
    # ===============================

    @app.route("/asset_edit/<int:id>",methods=["GET","POST"])
    @login_required
    def asset_edit(id):


        asset = (
            supabase
            .table("assets")
            .select("*")
            .eq("id", id)
            .single()
            .execute()
        ).data



        if request.method == "POST":
            
            new_code = request.form["asset_id_code"]

            exist = (
                supabase
                .table("assets")
                .select("id")
                .eq("asset_id_code", new_code)
                .execute()
            )

            if exist.data:

                # 如果找到的是別筆資料
                if exist.data[0]["id"] != id:

                    asset.update(request.form)

                    return render_template(
                        "asset_edit.html",
                        asset=asset,
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
            ).execute()



            create_log(
                action="修改資產",
                asset_id=id
            )



            return redirect(
                url_for("asset_summary")
            )



        return render_template("asset_edit.html",asset=asset,asset_id=id)



    # ===============================
    # 刪除資產
    # ===============================

    @app.route("/asset_delete/<int:id>",methods=["GET","POST"])
    @login_required
    def asset_delete(id):


        asset = (
            supabase
            .table("assets")
            .select("*")
            .eq("id", id)
            .single()
            .execute()
        ).data



        if request.method == "POST":


            try:


                create_log(
                    action="刪除資產",
                    asset_id=id,
                    asset_code=asset["asset_id_code"]
                )


                supabase.table(
                    "assets"
                ).delete().eq(
                    "id",
                    id
                ).execute()



            except Exception:


                create_log(
                    action="刪除資產",
                    asset_id=id,
                    status="失敗"
                )


                raise



            return redirect(
                url_for("asset_summary")
            )



        return render_template(
            "asset_delete.html",
            asset=asset
        )


    # ===============================
    # 權重設定 (💡 已移至 create_app 內部，並補上 @login_required)
    # ===============================
    @app.route('/weight-setting')
    @login_required
    def weight_setting():
        return render_template('weight_setting.html')


    return app


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    app = create_app()
    webbrowser.open(
        "http://127.0.0.1:5000"
    )

    app.run(
        debug=True,
        port=5000
    )
else:
    app = create_app()
