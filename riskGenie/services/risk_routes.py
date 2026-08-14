# -*- coding: utf-8 -*-
"""
風險評鑑、權重設定與 AI Advisor 的 Flask Blueprint 路由。
"""

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for
)

import logging
import math
from datetime import datetime


# ============================================================
# Import
# ============================================================

try:
    from .risk_service import (
        InvalidFormulaTypeError,
        RiskService,
        RiskServiceValidationError,
        normalize_formula_type,
    )

    from .rag_service import generate_advice
    from .report import export_report
    from .supabase_client import get_supabase_client

except ImportError:
    from services.risk_service import (
        InvalidFormulaTypeError,
        RiskService,
        RiskServiceValidationError,
        normalize_formula_type,
    )

    from services.rag_service import generate_advice
    from services.report import export_report
    from services.supabase_client import get_supabase_client


# ============================================================
# Blueprint
# ============================================================

risk_bp = Blueprint("risk", __name__)
logger = logging.getLogger(__name__)

COMPANY_CONTEXT_ERROR = "帳號缺少公司識別資訊"


# ============================================================
# 共用錯誤處理
# ============================================================

class WeightValidationError(ValueError):

    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


def _json_error(message, code, status_code):
    return jsonify({
        "success": False,
        "error": message,
        "code": code
    }), status_code


# ============================================================
# Session Company ID
# ============================================================

def _session_company_id():

    company_id = session.get("company_id")

    # 有些 Flask / DB session 可能存成字串
    try:
        if company_id is None:
            return None

        company_id = int(company_id)

    except (TypeError, ValueError):
        return None

    if company_id <= 0:
        return None

    return company_id


def _company_context_required_response(api_request=False):

    if api_request:
        return _json_error(
            COMPANY_CONTEXT_ERROR,
            "COMPANY_CONTEXT_REQUIRED",
            403
        )

    return COMPANY_CONTEXT_ERROR, 403


# ============================================================
# 權重驗證
# ============================================================

def _coerce_weight(value, field_name):

    if isinstance(value, bool):
        raise WeightValidationError(
            f"{field_name} 必須是有限且非負的數值。",
            "INVALID_WEIGHT"
        )

    try:
        numeric_value = float(value)

    except (TypeError, ValueError):
        raise WeightValidationError(
            f"{field_name} 必須是有限且非負的數值。",
            "INVALID_WEIGHT"
        )

    if (
        not math.isfinite(numeric_value)
        or numeric_value < 0
    ):
        raise WeightValidationError(
            f"{field_name} 必須是有限且非負的數值。",
            "INVALID_WEIGHT"
        )

    return numeric_value


def _validate_weight_payload(data):

    formula_type = data.get(
        "formula_type",
        "max"
    )

    try:
        formula_type = normalize_formula_type(
            formula_type
        )

    except InvalidFormulaTypeError:
        raise WeightValidationError(
            "不支援的公式類型",
            "INVALID_FORMULA_TYPE"
        )

    weights = {
        "weight_c": data.get("weight_c", 0.3333),
        "weight_i": data.get("weight_i", 0.3333),
        "weight_a": data.get("weight_a", 0.3333)
    }

    weight_c = _coerce_weight(
        weights["weight_c"],
        "weight_c"
    )

    weight_i = _coerce_weight(
        weights["weight_i"],
        "weight_i"
    )

    weight_a = _coerce_weight(
        weights["weight_a"],
        "weight_a"
    )

    normalized = [
        weight_c,
        weight_i,
        weight_a
    ]

    # 如果輸入 0~100，轉成 0~1
    if any(weight > 1 for weight in normalized):
        normalized = [
            weight / 100.0
            for weight in normalized
        ]

    if any(
        weight < 0 or weight > 1
        for weight in normalized
    ):
        raise WeightValidationError(
            "權重值必須介於 0 與 1 之間，或以 0 到 100 的百分比表示。",
            "INVALID_WEIGHT"
        )

    total_weight = sum(normalized)

    if (
        formula_type == "weighted_average"
        and abs(total_weight - 1.0) > 0.001
    ):
        raise WeightValidationError(
            "加權平均法之 C、I、A 權重總和必須為 1.0。",
            "INVALID_WEIGHT_TOTAL"
        )

    return {
        "formula_type": formula_type,
        "weight_c": normalized[0],
        "weight_i": normalized[1],
        "weight_a": normalized[2]
    }


# ============================================================
# 權重設定頁面
# ============================================================

@risk_bp.route(
    "/weight_setting",
    methods=["GET", "POST"]
)
def weight_setting_page():

    if not session.get("logged_in"):
        return redirect(
            url_for("login")
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=False
        )

    if request.method == "POST":
        try:
            data = (
                request.get_json(silent=True)
                if request.is_json
                else request.form
            )

            validated = _validate_weight_payload(
                data or {}
            )

            RiskService.save_weight_settings(
                company_id=company_id,
                formula_type=validated["formula_type"],
                weight_c=validated["weight_c"],
                weight_i=validated["weight_i"],
                weight_a=validated["weight_a"]
            )

            return redirect(
                url_for(
                    "risk.weight_setting_page"
                )
            )

        except WeightValidationError as exc:
            return render_template(
                "weight_setting.html",
                error=exc.message
            ), 400

        except Exception:
            logger.exception(
                "權重設定頁面儲存失敗"
            )

            return render_template(
                "weight_setting.html",
                error="權重設定儲存失敗"
            ), 503

    return render_template(
        "weight_setting.html"
    )


# ============================================================
# 取得權重設定
# ============================================================

@risk_bp.route(
    "/api/weight-settings",
    methods=["GET"]
)
def get_weight_settings_api():

    if not session.get("logged_in"):
        return _json_error(
            "未登入系統",
            "UNAUTHORIZED",
            401
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    try:
        settings = RiskService.get_weight_settings(
            company_id
        )

        return jsonify(settings), 200

    except Exception:
        logger.exception(
            "取得權重設定失敗"
        )

        return _json_error(
            "暫時無法取得權重設定",
            "WEIGHT_SETTINGS_UNAVAILABLE",
            503
        )


# ============================================================
# 儲存權重設定
# ============================================================

@risk_bp.route(
    "/api/weight-settings",
    methods=["POST"]
)
def save_weight_settings_api():

    if not session.get("logged_in"):
        return _json_error(
            "未登入系統",
            "UNAUTHORIZED",
            401
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    try:
        data = request.get_json(
            silent=True
        )

        if not isinstance(data, dict):
            return _json_error(
                "未收到有效的 JSON 請求資料",
                "INVALID_JSON",
                400
            )

        validated = _validate_weight_payload(
            data
        )

        result = RiskService.save_weight_settings(
            company_id=company_id,
            formula_type=validated["formula_type"],
            weight_c=validated["weight_c"],
            weight_i=validated["weight_i"],
            weight_a=validated["weight_a"]
        )

        status_code = (
            200
            if result.get("success")
            else 503
        )

        return jsonify(
            result
        ), status_code

    except WeightValidationError as exc:
        return _json_error(
            exc.message,
            exc.code,
            400
        )

    except RiskServiceValidationError:
        logger.warning(
            "權重設定儲存驗證失敗"
        )

        return _json_error(
            "權重設定儲存失敗",
            "WEIGHT_SETTINGS_SAVE_FAILED",
            400
        )

    except Exception:
        logger.exception(
            "權重設定儲存失敗"
        )

        return _json_error(
            "權重設定儲存失敗",
            "WEIGHT_SETTINGS_SAVE_FAILED",
            503
        )


# ============================================================
# 風險評鑑頁面
# ============================================================

@risk_bp.route(
    "/risk_assessment",
    methods=["GET"]
)
def risk_assessment_page():

    if not session.get("logged_in"):
        return redirect(
            url_for("login")
        )

    return render_template(
        "risk_assessment.html"
    )


# ============================================================
# 取得目前公司的資產
#
# companies.id
#      ↓
# assets.company_id
# ============================================================

@risk_bp.route(
    "/api/risk-assessments/assets",
    methods=["GET"]
)
def get_assessment_assets_api():

    if not session.get("logged_in"):
        return _json_error(
            "未登入系統",
            "UNAUTHORIZED",
            401
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    try:
        supabase = get_supabase_client()

        response = (
            supabase
            .table("assets")
            .select(
                """
                id,
                asset_id_code,
                asset_name,
                description,
                asset_type,
                confidentiality,
                integrity,
                availability,
                legality,
                asset_value
                """
            )
            .eq(
                "company_id",
                company_id
            )
            .eq(
                "status",
                "active"
            )
            .execute()
        )

        return jsonify({
            "success": True,
            "assets": response.data or []
        }), 200

    except Exception as e:
        logger.exception(
            "取得資產列表失敗: %s",
            e
        )

        return _json_error(
            "無法載入資產列表",
            "FETCH_ASSETS_FAILED",
            500
        )


# ============================================================
# 計算風險
# ============================================================

@risk_bp.route(
    "/api/risk-assessments/calculate",
    methods=["POST"]
)
def calculate_risk_api():

    if not session.get("logged_in"):
        return _json_error(
            "未登入系統",
            "UNAUTHORIZED",
            401
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    data = request.get_json(
        silent=True
    ) or {}

    try:
        c = float(
            data.get(
                "confidentiality",
                0
            )
        )

        i = float(
            data.get(
                "integrity",
                0
            )
        )

        a = float(
            data.get(
                "availability",
                0
            )
        )

        l = float(
            data.get(
                "legality",
                0
            )
        )

        cvss = float(
            data.get(
                "cvss_score",
                0
            )
        )

        likelihood = float(
            data.get(
                "likelihood_score",
                1
            )
        )

        # --------------------------
        # 驗證
        # --------------------------

        if not 0 <= c <= 5:
            raise ValueError(
                "機密性必須介於 0~5"
            )

        if not 0 <= i <= 5:
            raise ValueError(
                "完整性必須介於 0~5"
            )

        if not 0 <= a <= 5:
            raise ValueError(
                "可用性必須介於 0~5"
            )

        if not 0 <= l <= 5:
            raise ValueError(
                "適法性必須介於 0~5"
            )

        if not 0 <= cvss <= 10:
            raise ValueError(
                "CVSS 必須介於 0~10"
            )

        if not 1 <= likelihood <= 5:
            raise ValueError(
                "發生機率必須介於 1~5"
            )

        # --------------------------
        # 取得公司權重
        # --------------------------

        weight_settings = (
            RiskService.get_weight_settings(
                company_id
            )
        )

        formula_type = (
            weight_settings.get(
                "formula_type",
                "max"
            )
        )

        wc = weight_settings.get(
            "weight_c",
            0.3333
        )

        wi = weight_settings.get(
            "weight_i",
            0.3333
        )

        wa = weight_settings.get(
            "weight_a",
            0.3333
        )

        # --------------------------
        # Impact
        # --------------------------

        if formula_type == "weighted_average":
            asset_value = round(
                (c * wc)
                + (i * wi)
                + (a * wa),
                2
            )

            impact_score = max(
                asset_value,
                l
            )

        else:
            impact_score = max(
                c,
                i,
                a,
                l
            )

        # --------------------------
        # Risk
        # --------------------------

        vulnerability_factor = (
            cvss / 10.0
            if cvss > 0
            else 1.0
        )

        risk_score = round(
            impact_score
            * likelihood
            * vulnerability_factor,
            2
        )

        # --------------------------
        # Risk Level
        # --------------------------

        if risk_score >= 12:
            risk_level = "極高風險"

        elif risk_score >= 8:
            risk_level = "高風險"

        elif risk_score >= 4:
            risk_level = "中風險"

        else:
            risk_level = "低風險"

        return jsonify({
            "success": True,
            "impact_score": impact_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "formula_used": formula_type
        }), 200

    except Exception as exc:
        logger.exception(
            "風險計算失敗: %s",
            exc
        )

        return _json_error(
            str(exc),
            "CALCULATION_ERROR",
            400
        )


# ============================================================
# 儲存風險評鑑
#
# 注意：
#
# risk_assessments 沒有 company_id
#
# 關係：
#
# companies.id
#       ↓
# assets.company_id
#
# assets.id
#       ↓
# risk_assessments.asset_id
# ============================================================

@risk_bp.route(
    "/api/risk-assessments/save",
    methods=["POST"]
)
def save_risk_assessment_api():

    if not session.get("logged_in"):
        return _json_error(
            "未登入系統",
            "UNAUTHORIZED",
            401
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    data = request.get_json(
        silent=True
    ) or {}

    asset_id = data.get(
        "asset_id"
    )

    threat_description = data.get(
        "threat_description",
        ""
    )

    impact_score = data.get(
        "impact_score"
    )

    likelihood_score = data.get(
        "likelihood_score"
    )

    cvss_score = data.get(
        "cvss_score"
    )

    risk_score = data.get(
        "risk_score"
    )

    risk_level = data.get(
        "risk_level"
    )

    if not asset_id:
        return _json_error(
            "缺少資產 ID",
            "MISSING_ASSET_ID",
            400
        )

    if risk_score is None:
        return _json_error(
            "缺少風險分數",
            "MISSING_RISK_SCORE",
            400
        )

    try:
        supabase = get_supabase_client()

        user_id = session.get(
            "user_id"
        )

        # ====================================================
        # 確認資產屬於目前公司
        # ====================================================

        asset_response = (
            supabase
            .table("assets")
            .select("id, company_id, asset_name")
            .eq(
                "id",
                asset_id
            )
            .eq(
                "company_id",
                company_id
            )
            .limit(1)
            .execute()
        )

        if not asset_response.data:
            return _json_error(
                "找不到此資產，或資產不屬於目前公司",
                "ASSET_NOT_FOUND",
                404
            )

        # ====================================================
        # risk_assessments 不寫 company_id
        # ====================================================

        assessment_payload = {
            "asset_id": asset_id,
            "threat_description": threat_description,
            "impact_score": impact_score,
            "likelihood_score": likelihood_score,
            "cvss_score": cvss_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "uploaded_by": user_id,
            "created_at": datetime.now().isoformat()
        }

        result = (
            supabase
            .table("risk_assessments")
            .insert(
                assessment_payload
            )
            .execute()
        )

        # ====================================================
        # Audit Log
        # ====================================================

        try:
            supabase.table(
                "audit_logs"
            ).insert({
                "user_id": user_id,
                "action": "進行風險評鑑",
                "asset_id": asset_id,
                "ip_address": request.remote_addr,
                "status": "成功",
                "log_time": datetime.now().isoformat()
            }).execute()

        except Exception:
            logger.warning(
                "評鑑稽核日誌寫入失敗"
            )

        return jsonify({
            "success": True,
            "data": result.data
        }), 201

    except Exception as exc:
        logger.exception(
            "儲存評鑑紀錄失敗: %s",
            exc
        )

        return _json_error(
            str(exc),
            "SAVE_ASSESSMENT_FAILED",
            500
        )


# ============================================================
# 歷史風險評鑑
#
# 不使用 risk_assessments.company_id
# ============================================================

@risk_bp.route(
    "/api/risk-assessments",
    methods=["GET"]
)
def get_historical_assessments_api():

    if not session.get("logged_in"):
        return _json_error(
            "未登入系統",
            "UNAUTHORIZED",
            401
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    try:
        supabase = get_supabase_client()

        # ====================================================
        # 先找目前公司的 assets
        # ====================================================

        assets_response = (
            supabase
            .table("assets")
            .select(
                "id, asset_name, asset_id_code"
            )
            .eq(
                "company_id",
                company_id
            )
            .execute()
        )

        assets = (
            assets_response.data
            or []
        )

        if not assets:
            return jsonify({
                "success": True,
                "assessments": []
            }), 200

        asset_ids = [
            asset["id"]
            for asset in assets
            if asset.get("id") is not None
        ]

        if not asset_ids:
            return jsonify({
                "success": True,
                "assessments": []
            }), 200

        # ====================================================
        # 查 risk_assessments
        # 只使用 asset_id
        # ====================================================

        response = (
            supabase
            .table("risk_assessments")
            .select("*")
            .in_(
                "asset_id",
                asset_ids
            )
            .order(
                "created_at",
                desc=True
            )
            .execute()
        )

        assessments = (
            response.data
            or []
        )

        # ====================================================
        # 補上資產資料
        # ====================================================

        asset_map = {
            asset["id"]: asset
            for asset in assets
        }

        for assessment in assessments:
            asset = asset_map.get(
                assessment.get(
                    "asset_id"
                ),
                {}
            )

            assessment["assets"] = {
                "asset_name": asset.get(
                    "asset_name",
                    ""
                ),
                "asset_id_code": asset.get(
                    "asset_id_code",
                    ""
                )
            }

        return jsonify({
            "success": True,
            "assessments": assessments
        }), 200

    except Exception as e:
        logger.exception(
            "取得評鑑紀錄失敗: %s",
            e
        )

        return _json_error(
            str(e),
            "FETCH_ASSESSMENTS_FAILED",
            500
        )


# ============================================================
# AI Advisor 頁面
# ============================================================

@risk_bp.route(
    "/ai-advice",
    methods=["GET"]
)
def ai_advice_page():

    if not session.get("logged_in"):
        return redirect(
            url_for("login")
        )

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=False
        )

    has_assessment = False

    try:
        supabase = get_supabase_client()

        # ====================================================
        # 找目前公司的 assets
        # ====================================================

        asset_response = (
            supabase
            .table("assets")
            .select("id")
            .eq(
                "company_id",
                company_id
            )
            .execute()
        )

        assets = (
            asset_response.data
            or []
        )

        if assets:
            asset_ids = [
                asset["id"]
                for asset in assets
                if asset.get("id") is not None
            ]

            if asset_ids:
                # ====================================================
                # 找是否有 risk_assessments
                # ====================================================

                response = (
                    supabase
                    .table("risk_assessments")
                    .select("id")
                    .in_(
                        "asset_id",
                        asset_ids
                    )
                    .limit(1)
                    .execute()
                )

                has_assessment = bool(
                    response.data
                )

    except Exception as e:
        logger.exception(
            "Check risk assessment failed: %s",
            e
        )

        has_assessment = False

    return render_template(
        "ai_advice.html",
        has_assessment=has_assessment
    )


# ============================================================
# AI Advisor API
# ============================================================

@risk_bp.route(
    "/api/ai-advice",
    methods=["POST"]
)
def ai_advice():

    """
    AI 風險建議 API

    資料關係：

    companies
        ↓
    assets.company_id
        ↓
    assets.id
        ↓
    risk_assessments.asset_id
        ↓
    RAG
        ↓
    Gemini
    """

    # ========================================================
    # 1. 登入驗證
    # ========================================================

    if not session.get("logged_in"):
        return jsonify({
            "success": False,
            "error": "Unauthorized",
            "code": "UNAUTHORIZED"
        }), 401

    # ========================================================
    # 2. 公司驗證
    # ========================================================

    company_id = _session_company_id()

    if company_id is None:
        return _company_context_required_response(
            api_request=True
        )

    try:
        supabase = get_supabase_client()

        # ====================================================
        # 3. 找目前公司的所有資產
        # ====================================================

        asset_response = (
            supabase
            .table("assets")
            .select(
                """
                id,
                company_id,
                asset_name,
                description,
                confidentiality,
                integrity,
                availability,
                legality,
                asset_type,
                asset_value
                """
            )
            .eq(
                "company_id",
                company_id
            )
            .execute()
        )

        assets = (
            asset_response.data
            or []
        )

        logger.info(
            "公司 %s 找到 %s 個資產",
            company_id,
            len(assets)
        )

        if not assets:
            return jsonify({
                "success": False,
                "error": "目前公司沒有資產",
                "code": "NO_ASSET"
            }), 400

        # ====================================================
        # 4. 建立 asset map
        # ====================================================

        asset_map = {
            asset["id"]: asset
            for asset in assets
            if asset.get("id") is not None
        }

        asset_ids = list(
            asset_map.keys()
        )

        if not asset_ids:
            return jsonify({
                "success": False,
                "error": "目前公司沒有有效的資產 ID",
                "code": "NO_VALID_ASSET"
            }), 400

        # ====================================================
        # 5. 查 risk_assessments
        #
        # 重要：
        # 不可以寫：
        #
        # .eq("company_id", company_id)
        #
        # 因為 risk_assessments 沒有 company_id
        # ====================================================

        assessment_response = (
            supabase
            .table("risk_assessments")
            .select("*")
            .in_(
                "asset_id",
                asset_ids
            )
            .order(
                "created_at",
                desc=True
            )
            .limit(1)
            .execute()
        )

        assessments = (
            assessment_response.data
            or []
        )

        logger.info(
            "公司 %s 找到 %s 筆風險評鑑",
            company_id,
            len(assessments)
        )

        # ====================================================
        # 6. 沒有風險評鑑
        # ====================================================

        if not assessments:
            return jsonify({
                "success": False,
                "error": "尚未完成風險評鑑，請先進行風險評鑑並儲存結果",
                "code": "NO_ASSESSMENT"
            }), 400

        # ====================================================
        # 7. 最新評鑑
        # ====================================================

        assessment = assessments[0]

        asset_id = assessment.get(
            "asset_id"
        )

        asset = asset_map.get(
            asset_id,
            {}
        )

        if not asset:
            return jsonify({
                "success": False,
                "error": "風險評鑑所對應的資產不存在",
                "code": "ASSET_NOT_FOUND"
            }), 400

        # ====================================================
        # 8. 資產資料
        # ====================================================

        asset_name = asset.get(
            "asset_name",
            "未知資產"
        )

        description = asset.get(
            "description",
            ""
        )

        confidentiality = asset.get(
            "confidentiality",
            0
        )

        integrity = asset.get(
            "integrity",
            0
        )

        availability = asset.get(
            "availability",
            0
        )

        legality = asset.get(
            "legality",
            0
        )

        # ====================================================
        # 9. 建立 RAG 查詢內容
        # ====================================================

        asset_info = f"""
資產名稱：
{asset_name}

資產類型：
{asset.get("asset_type", "")}

資產描述：
{description}

威脅與弱點描述：
{assessment.get("threat_description", "")}

CIA 評估：

機密性(C)：
{confidentiality}

完整性(I)：
{integrity}

可用性(A)：
{availability}

適法性(L)：
{legality}

CVSS：
{assessment.get("cvss_score", 0)}

發生機率：
{assessment.get("likelihood_score", 0)}

Impact Score：
{assessment.get("impact_score", 0)}

Risk Score：
{assessment.get("risk_score", 0)}

Risk Level：
{assessment.get("risk_level", "")}

請分析此資產可能存在的資安風險，
並參考相關 CVE 與 ISO 27002 控制措施，
提供具體、可執行的改善建議。

請使用繁體中文回答。
"""

        logger.info(
            "開始產生 AI 建議：%s",
            asset_name
        )

        # ====================================================
        # 10. 呼叫 RAG + Gemini
        # ====================================================

        advice = generate_advice(
            asset_info
        )

        # ====================================================
        # 11. 回傳 AI 建議
        # ====================================================

        return jsonify({
            "success": True,
            "asset_name": asset_name,
            "advice": advice
        }), 200

    except Exception as e:
        logger.exception(
            "AI Advisor failed: %s",
            e
        )

        return jsonify({
            "success": False,
            "error": str(e),
            "code": "AI_ADVISOR_FAILED"
        }), 503


# ============================================================
# 報表匯出
# ============================================================

@risk_bp.route(
    "/export",
    methods=["GET"]
)
def export():

    if not session.get("logged_in"):
        return jsonify({
            "success": False,
            "error": "Unauthorized",
            "code": "UNAUTHORIZED"
        }), 401

    if _session_company_id() is None:
        return _company_context_required_response(
            api_request=True
        )

    return export_report()