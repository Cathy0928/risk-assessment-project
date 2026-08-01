# -*- coding: utf-8 -*-
"""
風險評鑑與權重設定的 Flask Blueprint 路由。
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import logging

# 💡 修復相對與絕對匯入防呆
try:
    from .risk_service import RiskService
    from .risk_ai import (
        GeminiConfigurationError,
        GeminiServiceError,
        generate_risk_advice,
        is_gemini_configured,
    )
    from .report import export_report
except ImportError:
    from services.risk_service import RiskService
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


@risk_bp.route('/weight_setting', methods=['GET', 'POST'])
def weight_setting_page():
    """渲染權重設定網頁頁面"""
    if not session.get("logged_in"):
        return redirect(url_for('login'))
    
    company_id = session.get("company_id", 1)

    if request.method == 'POST':
        try:
            if request.is_json:
                data = request.get_json()
            else:
                data = request.form

            formula_type = data.get("formula_type", "max")
            weight_c = float(data.get("weight_c", 0.3333))
            weight_i = float(data.get("weight_i", 0.3333))
            weight_a = float(data.get("weight_a", 0.3333))

            RiskService.save_weight_settings(
                company_id=company_id,
                formula_type=formula_type,
                weight_c=weight_c,
                weight_i=weight_i,
                weight_a=weight_a
            )
            return redirect(url_for('risk.weight_setting_page'))
        except Exception as e:
            return render_template('weight_setting.html', error=f"儲存失敗: {str(e)}")

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
        return jsonify({"error": "未登入系統"}), 401
    
    company_id = session.get("company_id", 1)
    try:
        settings = RiskService.get_weight_settings(company_id)
        return jsonify(settings), 200
    except Exception as e:
        return jsonify({"error": f"取得權重設定失敗: {str(e)}"}), 500


@risk_bp.route('/api/weight-settings', methods=['POST'])
def save_weight_settings_api():
    """API：儲存或更新權重設定"""
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
    
    company_id = session.get("company_id", 1)
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "未收到有效的 JSON 請求資料"}), 400

        formula_type = data.get("formula_type")
        weight_c = data.get("weight_c")
        weight_i = data.get("weight_i")
        weight_a = data.get("weight_a")

        if not formula_type:
            return jsonify({"error": "缺少必要欄位: formula_type"}), 400
        formula_type = str(formula_type).strip().lower()

        try:
            weight_c = float(weight_c) if weight_c is not None else 0.3333
            weight_i = float(weight_i) if weight_i is not None else 0.3333
            weight_a = float(weight_a) if weight_a is not None else 0.3333
        except ValueError:
            return jsonify({"error": "權重值必須為數值型態"}), 400

        if formula_type in ("weighted_average", "weighted_avg"):
            total_weight = weight_c + weight_i + weight_a
            
            if total_weight > 1.1:
                weight_c /= 100.0
                weight_i /= 100.0
                weight_a /= 100.0
                total_weight = weight_c + weight_i + weight_a
            
            if not (0.95 <= total_weight <= 1.05):
                return jsonify({"error": "加權平均法之 C, I, A 權重總和必須為 1.0 (或 100%)"}), 400

        result = RiskService.save_weight_settings(
            company_id=company_id,
            formula_type=formula_type,
            weight_c=weight_c,
            weight_i=weight_i,
            weight_a=weight_a
        )
        status_code = 200 if result.get("success") else 503
        return jsonify(result), status_code

    except Exception as e:
        return jsonify({"error": f"儲存權重設定失敗: {str(e)}"}), 500


@risk_bp.route('/api/risk-assessments/calculate', methods=['POST'])
def calculate_risk_api():
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
    return jsonify({"message": "風險計算 API 調用成功"}), 200


@risk_bp.route('/api/risk-assessments', methods=['GET'])
def get_historical_assessments_api():
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
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

    return export_report()
