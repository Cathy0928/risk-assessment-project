# -*- coding: utf-8 -*-
"""
風險評鑑與權重設定的 Flask Blueprint 路由。
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import logging
import math

# 💡 修復相對與絕對匯入防呆
try:
    from .risk_service import (
        InvalidFormulaTypeError,
        RiskService,
        RiskServiceValidationError,
        normalize_formula_type,
    )
    from .risk_ai import (
        GeminiConfigurationError,
        GeminiServiceError,
        generate_risk_advice,
        is_gemini_configured,
    )
    from .report import export_report
except ImportError:
    from services.risk_service import (
        InvalidFormulaTypeError,
        RiskService,
        RiskServiceValidationError,
        normalize_formula_type,
    )
    from services.risk_ai import (
        GeminiConfigurationError,
        GeminiServiceError,
        generate_risk_advice,
        is_gemini_configured,
    )
    from services.report import export_report

risk_bp = Blueprint('risk', __name__)
logger = logging.getLogger(__name__)
AI_REQUIRED_FIELDS = ("asset_name", "cia", "cvss", "risk_score")
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


@risk_bp.route('/risk_assessment')
def risk_assessment_page():
    if not session.get("logged_in"):
        return redirect(url_for('login'))
    return render_template('risk_assessment.html')


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


@risk_bp.route('/api/risk-assessments/calculate', methods=['POST'])
def calculate_risk_api():
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    if _session_company_id() is None:
        return _company_context_required_response(api_request=True)
    return jsonify({"message": "風險計算 API 調用成功"}), 200


@risk_bp.route('/api/risk-assessments', methods=['GET'])
def get_historical_assessments_api():
    if not session.get("logged_in"):
        return _json_error("未登入系統", "UNAUTHORIZED", 401)
    if _session_company_id() is None:
        return _company_context_required_response(api_request=True)
    return jsonify([]), 200

#風險評鑑AI建議
@risk_bp.route("/ai-advice", methods=["POST"])
def ai_advice():
    if not session.get("logged_in"):
        return jsonify({
            "success": False,
            "error": "Unauthorized",
            "code": "UNAUTHORIZED",
        }), 401
    if _session_company_id() is None:
        return _company_context_required_response(api_request=True)

    if not request.is_json:
        return jsonify({
            "success": False,
            "error": "請使用 JSON 格式送出 AI 建議請求。",
            "code": "INVALID_JSON",
        }), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "error": "JSON 內容無效。",
            "code": "INVALID_JSON",
        }), 400

    missing_fields = [
        field for field in AI_REQUIRED_FIELDS
        if data.get(field) is None or data.get(field) == ""
    ]
    if missing_fields:
        return jsonify({
            "success": False,
            "error": "缺少必要欄位。",
            "code": "MISSING_FIELDS",
            "missing_fields": missing_fields,
        }), 400

    if not is_gemini_configured():
        return jsonify({
            "success": False,
            "error": "Gemini API Key 尚未設定，請在環境變數設定 GEMINI_API_KEY。",
            "code": "GEMINI_NOT_CONFIGURED",
        }), 503

    try:
        result = generate_risk_advice(data)
        return jsonify(result), 200
    except GeminiConfigurationError:
        logger.exception("Gemini configuration error.")
        return jsonify({
            "success": False,
            "error": "Gemini 服務尚未正確設定。",
            "code": "GEMINI_NOT_CONFIGURED",
        }), 503
    except GeminiServiceError:
        return jsonify({
            "success": False,
            "error": "Gemini 服務暫時無法產生建議，請稍後再試。",
            "code": "GEMINI_SERVICE_FAILED",
        }), 503

#報表
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
