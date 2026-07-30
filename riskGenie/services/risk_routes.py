# -*- coding: utf-8 -*-
"""
風險評鑑與權重設定的 Flask Blueprint 路由。
包含網頁頁面渲染路由與對應的前端 API。
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from services.risk_service import RiskService

# 建立 Flask Blueprint 模組
risk_bp = Blueprint('risk', __name__)


@risk_bp.route('/weight_setting', methods=['GET', 'POST'])
def weight_setting_page():
    """
    渲染權重設定網頁頁面。
    調整為同時接受 GET 與 POST 方法，避免前端表單誤提交時引發 405 錯誤。
    """
    if not session.get("logged_in"):
        return redirect(url_for('login'))
    
    # 取得使用者所屬公司 ID，若無則預設為 1 (便於單機或測試模式執行)
    company_id = session.get("company_id", 1)

    # 防錯機制：如果前端直接將表單 POST 到網頁渲染路徑，在此進行接收並處理
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

            # 呼叫 Service 儲存設定
            RiskService.save_weight_settings(
                company_id=company_id,
                formula_type=formula_type,
                weight_c=weight_c,
                weight_i=weight_i,
                weight_a=weight_a
            )
            # 儲存成功後重定向，避免重新整理頁面時發生重複提交 (PRG 模式)
            return redirect(url_for('risk.weight_setting_page'))
        except Exception as e:
            return render_template('weight_setting.html', error=f"儲存失敗: {str(e)}")

    return render_template('weight_setting.html')


@risk_bp.route('/risk_assessment')
def risk_assessment_page():
    """渲染風險評鑑網頁頁面"""
    if not session.get("logged_in"):
        return redirect(url_for('login'))
    return render_template('risk_assessment.html')


@risk_bp.route('/api/weight-settings', methods=['GET'])
def get_weight_settings_api():
    """
    API：取得當前登入使用者公司的權重與公式設定。
    前端網頁載入時會呼叫此 API，動態呈現在畫面上。
    """
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
    """
    API：儲存或更新當前公司的權重與公式設定（透過前端 Ajax 送出 JSON 請求）。
    """
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

        # 將權重轉換為數值，並給予預設值
        try:
            weight_c = float(weight_c) if weight_c is not None else 0.3333
            weight_i = float(weight_i) if weight_i is not None else 0.3333
            weight_a = float(weight_a) if weight_a is not None else 0.3333
        except ValueError:
            return jsonify({"error": "權重值必須為數值型態"}), 400

        # 加權平均法防呆：權重總和必須等於 1.0 (或 100%)
        if formula_type == "weighted_average":
            total_weight = weight_c + weight_i + weight_a
            
            # 如果前端傳入百分比整數（如 40, 30, 30），自動轉換為小數百分比格式
            if total_weight > 1.1:
                weight_c /= 100.0
                weight_i /= 100.0
                weight_a /= 100.0
                total_weight = weight_c + weight_i + weight_a
            
            # 容許微小的浮點數計算誤差 (0.95 ~ 1.05 之間視為 100%)
            if not (0.95 <= total_weight <= 1.05):
                return jsonify({"error": "加權平均法之 C, I, A 權重總和必須為 1.0 (或 100%)"}), 400

        # 儲存設定
        result = RiskService.save_weight_settings(
            company_id=company_id,
            formula_type=formula_type,
            weight_c=weight_c,
            weight_i=weight_i,
            weight_a=weight_a
        )
        return jsonify(result), 200

    except Exception as e:
        return jsonify({"error": f"儲存權重設定失敗: {str(e)}"}), 500


@risk_bp.route('/api/risk-assessments/calculate', methods=['POST'])
def calculate_risk_api():
    """
    API：點擊開始風險評鑑，動態查詢資產與弱點，套用公式計算分數，並寫入歷史評鑑紀錄。
    """
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
    # 此處保留您原有評鑑計算 API 架構...
    return jsonify({"message": "風險計算 API 調用成功"}), 200


@risk_bp.route('/api/risk-assessments', methods=['GET'])
def get_historical_assessments_api():
    """
    API：取得當前公司所有的歷史風險評鑑清單。
    """
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
    # 此處保留您原有歷史清單 API 架構...
    return jsonify([]), 200