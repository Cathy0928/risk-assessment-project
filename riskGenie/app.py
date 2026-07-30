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
import sys
import webbrowser
import pandas as pd

from dotenv import load_dotenv
from supabase import create_client


# ===============================
# 環境與路徑設定 (解決 ModuleNotFoundError)
# ===============================
load_dotenv()

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
except ImportError:
    from services.risk_routes import risk_bp


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



# ===============================
# Flask App
# ===============================

def create_app():

    app = Flask(__name__)

    # 💡 每次重啟伺服器時生成全新金鑰，讓所有舊 Session 立刻失效（強制回到登入頁面）
    app.secret_key = os.urandom(24)


    # ===============================
    # Supabase 連線
    # ===============================

    SUPABASE_URL = os.getenv(
        "SUPABASE_URL"
    )

    SUPABASE_KEY = os.getenv(
        "SUPABASE_ANON_KEY"
    )


    supabase = create_client(
        SUPABASE_URL,
        SUPABASE_KEY
    )


    # ===============================
    # 註冊 Blueprint 模組
    # ===============================
    app.register_blueprint(risk_bp)


    # ===============================
    # 登入
    # ===============================

    @app.route("/login", methods=["GET", "POST"])
    def login():

        error = None

        if request.method == "POST":

            email = request.form.get(
                "email"
            )

            password = request.form.get(
                "password"
            )


            try:

                auth_response = (
                    supabase
                    .auth
                    .sign_in_with_password(
                        {
                            "email": email,
                            "password": password
                        }
                    )
                )


                user = auth_response.user

                session["user_id"] = user.id

                session["username"] = email

                session["logged_in"] = True

                session["company_id"] = getattr(user, "company_id", 1) or 1


                return redirect(
                    url_for("home")
                )


            except Exception:

                error = "電子郵件或密碼錯誤"



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

    @app.route("/asset_add", methods=["GET", "POST"])
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

    @app.route("/asset_edit/<int:id>", methods=["GET", "POST"])
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



        return render_template("asset_edit.html", asset=asset, asset_id=id)



    # ===============================
    # 刪除資產
    # ===============================

    @app.route("/asset_delete/<int:id>", methods=["GET", "POST"])
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