# -*- coding: utf-8 -*-
"""
風險評鑑與權重設定的 Flask Blueprint 路由。
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import logging
import math
from datetime import datetime

# 💡 修復相對與絕對匯入防呆
try:
    from .risk_service import (
        InvalidFormulaTypeError,
        RiskService,
        RiskServiceValidationError,
        normalize_formula_type,
    )
    # AI Advisor (RAG)
    from .rag_service import (
        generate_advice,
    )

    from .report import export_report
    from .supabase_client import get_supabase_client
except ImportError:
    from services.risk_service import (
        InvalidFormulaTypeError,
        RiskService,
        RiskServiceValidationError,
        normalize_formula_type,
    )
    # AI Advisor (RAG)
    from services.rag_service import (
        generate_advice,
    )

    from services.report import export_report
    from services.supabase_client import get_supabase_client

risk_bp = Blueprint('risk', __name__)
logger = logging.getLogger(__name__)
#AI_REQUIRED_FIELDS = ("asset_name", "cia", "cvss", "risk_score")
COMPANY_CONTEXT_ERROR = "帳號缺少公司識別資訊"


class WeightValidationError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


def _json_error(message, code, status_code):
    return jsonify({
        "success": False,
        "error": message,
        "code": code,
    }), status_code


def _session_company_id():
    company_id = session.get("company_id")
    if (
        not isinstance(company_id, int)
        or isinstance(company_id, bool)
        or company_id <= 0
    ):
        return None
    return company_id


def _company_context_required_response(api_request=False):
    if api_request:
        return _json_error(
            COMPANY_CONTEXT_ERROR,
            "COMPANY_CONTEXT_REQUIRED",
            403,
        )
    return COMPANY_CONTEXT_ERROR, 403


def _coerce_weight(value, field_name):
    if isinstance(value, bool):
        raise WeightValidationError(
            f"{field_name} 必須是有限且非負的數值。",
            "INVALID_WEIGHT",
        )
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        raise WeightValidationError(
            f"{field_name} 必須是有限且非負的數值。",
            "INVALID_WEIGHT",
        )
    if not math.isfinite(numeric_value) or numeric_value < 0:
        raise WeightValidationError(
            f"{field_name} 必須是有限且非負的數值。",
            "INVALID_WEIGHT",
        )
    return numeric_value


def _validate_weight_payload(data):
    formula_type = data.get("formula_type", "max")
    try:
        formula_type = normalize_formula_type(formula_type)
    except InvalidFormulaTypeError:
        raise WeightValidationError(
            "不支援的公式類型",
            "INVALID_FORMULA_TYPE",
        )

    weights = {
        "weight_c": data.get("weight_c", 0.3333),
        "weight_i": data.get("weight_i", 0.3333),
        "weight_a": data.get("weight_a", 0.3333),
    }
    weight_c = _coerce_weight(weights["weight_c"], "weight_c")
    weight_i = _coerce_weight(weights["weight_i"], "weight_i")
    weight_a = _coerce_weight(weights["weight_a"], "weight_a")

    normalized = [weight_c, weight_i, weight_a]
    if any(weight > 1 for weight in normalized):
        normalized = [weight / 100.0 for weight in normalized]

    if any(weight < 0 or weight > 1 for weight in normalized):
        raise WeightValidationError(
            "權重值必須介於 0 與 1 之間，或以 0 到 100 的百分比表示。",
            "INVALID_WEIGHT",
        )

    total_weight = sum(normalized)
    if formula_type == "weighted_average" and abs(total_weight - 1.0) > 0.001:
        raise WeightValidationError(
            "加權平均法之 C, I, A 權重總和必須為 1.0。",
            "INVALID_WEIGHT_TOTAL",
        )

    return {
        "formula_type": formula_type,
        "weight_c": normalized[0],
        "weight_i": normalized[1],
        "weight_a": normalized[2],
    }


# ==========================================
# 權重設定 相關頁面與 API
# ==========================================

@risk_bp.route('/weight_setting', methods=['GET', 'POST'])
def weight_setting_page():
    """渲染權重設定網頁頁面"""
    if not session.get("logged_in"):
        return redirect(url_for('login'))
    
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=False)

    if request.method == 'POST':
        try:
            data = request.get_json(silent=True) if request.is_json else request.form
            validated = _validate_weight_payload(data or {})

            RiskService.save_weight_settings(
                company_id=company_id,
                formula_type=validated["formula_type"],
                weight_c=validated["weight_c"],
                weight_i=validated["weight_i"],
                weight_a=validated["weight_a"]
            )
            return redirect(url_for('risk.weight_setting_page'))
        except WeightValidationError as exc:
            return render_template('weight_setting.html', error=exc.message), 400
        except Exception:
            logger.exception("權重設定頁面儲存失敗。")
            return render_template('weight_setting.html', error="權重設定儲存失敗"), 503

    return render_template('weight_setting.html')


@risk_bp.route('/api/weight-settings', methods=['GET'])
def get_weight_settings_api():
    """API：取得權重與公式設定"""
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=True)

    try:
        settings = RiskService.get_weight_settings(company_id)
        return jsonify(settings), 200
    except Exception:
        logger.exception("取得權重設定失敗。")
        return _json_error(
            "暫時無法取得權重設定",
            "WEIGHT_SETTINGS_UNAVAILABLE",
            503,
        )


@risk_bp.route('/api/weight-settings', methods=['POST'])
def save_weight_settings_api():
    """API：儲存或更新權重設定"""
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=True)
    
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _json_error(
                "未收到有效的 JSON 請求資料",
                "INVALID_JSON",
                400,
            )

        validated = _validate_weight_payload(data)

        result = RiskService.save_weight_settings(
            company_id=company_id,
            formula_type=validated["formula_type"],
            weight_c=validated["weight_c"],
            weight_i=validated["weight_i"],
            weight_a=validated["weight_a"]
        )
        status_code = 200 if result.get("success") else 503
        return jsonify(result), status_code
    except WeightValidationError as exc:
        return _json_error(exc.message, exc.code, 400)
    except RiskServiceValidationError:
        logger.warning("權重設定儲存驗證失敗。")
        return _json_error(
            "權重設定儲存失敗",
            "WEIGHT_SETTINGS_SAVE_FAILED",
            400,
        )
    except Exception:
        logger.exception("權重設定儲存失敗。")
        return _json_error(
            "權重設定儲存失敗",
            "WEIGHT_SETTINGS_SAVE_FAILED",
            503,
        )


# ==========================================
# 風險評鑑 相關頁面與 API
# ==========================================

@risk_bp.route('/risk_assessment')
def risk_assessment_page():
    """風險評鑑主頁面"""
    if not session.get("logged_in"):
        return redirect(url_for('login'))
    return render_template('risk_assessment.html')


@risk_bp.route('/api/risk-assessments/assets', methods=['GET'])
def get_assessment_assets_api():
    """API：取得適用評鑑的資產清單"""
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=True)

    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("assets")
            .select("id, asset_id_code, asset_name, asset_type, confidentiality, integrity, availability, legality, asset_value")
            .eq("company_id", company_id)
            .eq("status", "active")
            .execute()
        )
        return jsonify({"success": True, "assets": res.data or []}), 200
    except Exception as e:
        logger.exception("取得資產列表失敗: %s", e)
        return _json_error("無法載入資產列表", "FETCH_ASSETS_FAILED", 500)


@risk_bp.route('/api/risk-assessments/calculate', methods=['POST'])
def calculate_risk_api():
    """API：計算資產價值與風險分數"""
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=True)

    data = request.get_json(silent=True) or {}
    try:
        c = float(data.get("confidentiality", 0))
        i = float(data.get("integrity", 0))
        a = float(data.get("availability", 0))
        l = float(data.get("legality", 0))
        cvss = float(data.get("cvss_score", 0))
        likelihood = float(data.get("likelihood_score", 1))

        # 1. 取得公司權重設定
        weight_settings = RiskService.get_weight_settings(company_id)
        formula_type = weight_settings.get("formula_type", "max")
        wc = weight_settings.get("weight_c", 0.3333)
        wi = weight_settings.get("weight_i", 0.3333)
        wa = weight_settings.get("weight_a", 0.3333)

        # 2. 計算資產價值 (Impact Value / Asset Value)
        if formula_type == "weighted_average":
            asset_value = round((c * wc) + (i * wi) + (a * wa), 2)
            # 若包含適法性 L，可彈性取 max 或併入計算
            impact_score = max(asset_value, l)
        else: #預設 max
            impact_score = max(c, i, a, l)

        # 3. 計算風險值 (Risk Value = Asset Impact * Threat/Vulnerability Likelihood)
        # 簡易標準公式：Impact * Likelihood * (CVSS / 10)
        vulnerability_factor = (cvss / 10.0) if cvss > 0 else 1.0
        risk_score = round(impact_score * likelihood * vulnerability_factor, 2)

        # 4. 定義風險等級
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
        logger.exception("風險計算失敗: %s", exc)
        return _json_error("風險數值計算異常", "CALCULATION_ERROR", 400)


@risk_bp.route('/api/risk-assessments/save', methods=['POST'])
def save_risk_assessment_api():
    """API：儲存評鑑結果"""
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=True)

    data = request.get_json(silent=True) or {}
    asset_id = data.get("asset_id")
    vulnerability_name = data.get("vulnerability_name", "通用弱點")
    threat_description = data.get("threat_description", "")
    impact_score = data.get("impact_score")
    likelihood_score = data.get("likelihood_score")
    risk_score = data.get("risk_score")
    risk_level = data.get("risk_level")

    if not asset_id or risk_score is None:
        return _json_error("缺少必要的評鑑欄位", "MISSING_FIELDS", 400)

    try:
        supabase = get_supabase_client()
        user_id = session.get("user_id")

        assessment_payload = {
            "asset_id": asset_id,
            "threat_description": threat_description,
            "impact_score": impact_score,
            "likelihood_score": likelihood_score,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "uploaded_by": user_id,
            "company_id": company_id,
            "created_at": datetime.now().isoformat()
        }

        # 寫入 risk_assessments 資料表
        result = supabase.table("risk_assessments").insert(assessment_payload).execute()

        # 寫入 audit_logs 稽核紀錄
        try:
            supabase.table("audit_logs").insert({
                "user_id": user_id,
                "action": "進行風險評鑑",
                "asset_id": asset_id,
                "ip_address": request.remote_addr,
                "status": "成功",
                "log_time": datetime.now().isoformat()
            }).execute()
        except Exception:
            logger.warning("評鑑稽核日誌寫入失敗（不影響主程序）")

        return jsonify({"success": True, "data": result.data}), 201

    except Exception as exc:
        logger.exception("儲存評鑑紀錄失敗: %s", exc)
        return _json_error("無法儲存風險評鑑結果", "SAVE_ASSESSMENT_FAILED", 500)


@risk_bp.route('/api/risk-assessments', methods=['GET'])
def get_historical_assessments_api():
    """取得歷史評鑑紀錄 API"""
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    company_id = _session_company_id()
    if company_id is None:
        return _company_context_required_response(api_request=True)

    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("risk_assessments")
            .select("*, assets(asset_name, asset_id_code)")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .execute()
        )
        return jsonify({"success": True, "assessments": res.data or []}), 200
    except Exception as e:
        logger.exception("取得評鑑紀錄失敗: %s", e)
        return jsonify({"success": True, "assessments": []}), 200


# ==========================================
# 風險評鑑 AI Advisor API (RAG)
# ==========================================

@risk_bp.route("/ai-advice", methods=["POST"])
def ai_advice():

    """
    AI風險建議 API

    流程:
    1. 接收風險評鑑結果
    2. 組合資產資訊
    3. RAG搜尋CVE / ISO27002資料
    4. Gemini產生改善建議
    5. 回傳AI建議
    """


    # 登入驗證
    if not session.get("logged_in"):

        return jsonify({
            "success": False,
            "error": "Unauthorized",
            "code": "UNAUTHORIZED"
        }),401



    # 公司驗證
    if _session_company_id() is None:

        return _company_context_required_response(
            api_request=True
        )



    data = request.get_json(silent=True)


    if not isinstance(data,dict):

        return jsonify({

            "success":False,

            "error":"JSON格式錯誤",

            "code":"INVALID_JSON"

        }),400



    asset_name=data.get("asset_name")


    if not asset_name:

        return jsonify({

            "success":False,

            "error":"缺少資產名稱",

            "code":"MISSING_ASSET"

        }),400



    try:


        # ==========================
        # 建立 RAG 查詢內容
        # ==========================


        asset_info=f"""

資產名稱:
{asset_name}


資產描述:
{data.get('description','')}


CIA評估:

機密性(C):
{data.get('confidentiality',0)}


完整性(I):
{data.get('integrity',0)}


可用性(A):
{data.get('availability',0)}


漏洞資訊:

CVSS:
{data.get('cvss',0)}


目前風險分數:
{data.get('risk_score',0)}

請分析此資產可能存在的資安風險，
並提供改善建議。

"""



        # ==========================
        # 呼叫 RAG + Gemini
        # ==========================

        advice = generate_advice(
            asset_info
        )



        return jsonify({

            "success":True,

            "asset_name":asset_name,

            "advice":advice

        }),200




    except Exception as e:


        logger.exception(
            "AI Advisor failed:%s",
            e
        )


        return jsonify({

            "success":False,

            "error":"AI風險建議產生失敗",

            "code":"AI_ADVISOR_FAILED"

        }),503
    
@risk_bp.route("/ai-test")
def ai_test():

    return render_template(
        "risk_ai_test.html"
    )


# 報表匯出 API
@risk_bp.route("/export", methods=["GET"])
def export():
    if not session.get("logged_in"):
        return jsonify({
            "success": False,
            "error": "Unauthorized",
            "code": "UNAUTHORIZED",
        }), 401
    if _session_company_id() is None:
        return _company_context_required_response(api_request=True)

    return export_report()