# -*- coding: utf-8 -*-
"""
風險評鑑與權重設定的 Flask Blueprint 路由。
     包含網頁頁面渲染路由與對應的前端 API。
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from services.risk_service import RiskService
from riskgenie.modules.risk_engine import RiskEngine

# 建立 Flask Blueprint 模組
risk_bp = Blueprint('risk', __name__)

@risk_bp.route('/weight_setting')
def weight_setting_page():
    """渲染權重設定網頁頁面"""
    if not session.get("logged_in"):
        return redirect(url_for('login'))
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
    API：取得當前登入使用者公司的權重與公式設定
    """
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
        
    company_id = session.get("company_id")
    if not company_id:
        return jsonify({"error": "Session 中無有效的公司 ID"}), 400

    settings = RiskService.get_weight_settings(company_id)
    return jsonify(settings), 200

@risk_bp.route('/api/weight-settings', methods=['POST'])
def save_weight_settings_api():
    """
    API：儲存或更新當前公司的權重與公式設定
    """
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
        
    company_id = session.get("company_id")
    if not company_id:
        return jsonify({"error": "Session 中無有效的公司 ID"}), 400

    data = request.get_json() or {}
    formula_type = data.get("formula_type", "max").strip().lower()
    
    try:
        weight_c = float(data.get("weight_c", 0.3333))
        weight_i = float(data.get("weight_i", 0.3333))
        weight_a = float(data.get("weight_a", 0.3333))
    except (ValueError, TypeError):
        return jsonify({"error": "權重參數必須為數值"}), 400

    # 驗證：如果是加權平均法，權重相加應大致等於 1.0 (容許浮點數誤差)
    if formula_type == "weighted_avg":
        total_weight = weight_c + weight_i + weight_a
        if not (0.99 <= total_weight <= 1.01):
            return jsonify({"error": "加權平均公式之 C、I、A 權重相加必須等於 1.0 (100%)"}), 400

    success = RiskService.save_weight_settings(
        company_id=company_id,
        formula_type=formula_type,
        weight_c=weight_c,
        weight_i=weight_i,
        weight_a=weight_a
    )

    if success:
        return jsonify({"message": "權重與公式設定已成功儲存"}), 200
    else:
        return jsonify({"error": "寫入設定失敗"}), 500

@risk_bp.route('/api/risk-assessments/calculate', methods=['POST'])
def calculate_risk_api():
    """
    API：點擊開始風險評鑑，動態查詢資產與弱點，套用公式計算分數，並寫入歷史評鑑紀錄
    """
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
        
    user_id = session.get("user_id")
    company_id = session.get("company_id")
    
    data = request.get_json() or {}
    asset_id = data.get("asset_id")
    vulnerability_id = data.get("vulnerability_id")

    if not asset_id or not vulnerability_id:
        return jsonify({"error": "缺少 asset_id 或 vulnerability_id"}), 400

    # 1. 讀取該資產的 C, I, A 分級
    asset = RiskService.get_asset_by_id(asset_id)
    if not asset:
        return jsonify({"error": f"找不到 ID 為 {asset_id} 的資產項目"}), 404
        
    # 安全性檢查：確保使用者只能評估自己公司的資產
    if int(asset.get("company_id")) != int(company_id):
        return jsonify({"error": "權限不足，無法評估他司資產"}), 403

    # 2. 讀取弱點的 CVSS 分數與描述
    vulnerability = RiskService.get_vulnerability_by_id(vulnerability_id)
    if not vulnerability:
        return jsonify({"error": f"找不到 ID 為 {vulnerability_id} 的弱點項目"}), 404

    # 3. 讀取當前公司儲存的權重公式設定
    settings = RiskService.get_weight_settings(company_id)
    formula_type = settings.get("formula_type", "max")
    weights = {
        "c": settings.get("weight_c", 0.3333),
        "i": settings.get("weight_i", 0.3333),
        "a": settings.get("weight_a", 0.3333)
    }

    # 4. 提取數值並進行計算
    c_val = asset.get("confidentiality", 1)
    i_val = asset.get("integrity", 1)
    a_val = asset.get("availability", 1)
    cvss_score = vulnerability.get("cvss_score", 0.0)

    risk_score = RiskEngine.calculate_risk(
        c=c_val,
        i=i_val,
        a=a_val,
        cvss_score=cvss_score,
        formula_type=formula_type,
        weights=weights
    )
    
    risk_level = RiskEngine.get_risk_level(risk_score)

    # 5. 組裝詳細威脅描述資訊
    cve_id = vulnerability.get("cve_id", "未知 CVE")
    threat_description = (
        f"資產評分 C:{c_val}/I:{i_val}/A:{a_val}。關聯 CVE: {cve_id} (CVSS: {cvss_score})。 "
        f"套用「{formula_type}」公式計算所得之風險值為 {risk_score}，判定等級為 {risk_level}。"
    )

    # 6. 儲存結果至資料庫
    result = RiskService.save_risk_assessment(
        asset_id=asset_id,
        vulnerability_id=vulnerability_id,
        vulnerability_description=vulnerability.get("description", ""),
        risk_score=risk_score,
        status=risk_level,  # 直接儲存等級（如：高 (High)）作為當前狀態
        uploaded_by=user_id,
        threat_description=threat_description
    )

    if result:
        return jsonify({
            "message": "評鑑計算與記錄儲存成功",
            "asset_name": asset.get("asset_name"),
            "cve_id": cve_id,
            "risk_score": risk_score,
            "risk_level": risk_level
        }), 200
    else:
        return jsonify({"error": "評鑑結果寫入資料庫失敗"}), 500

@risk_bp.route('/api/risk-assessments', methods=['GET'])
def get_historical_assessments_api():
    """
    API：取得當前公司所有的歷史風險評鑑清單
    """
    if not session.get("logged_in"):
        return jsonify({"error": "未登入系統"}), 401
        
    company_id = session.get("company_id")
    if not company_id:
        return jsonify({"error": "Session 中無有效的公司 ID"}), 400

    history = RiskService.get_historical_assessments(company_id)
    return jsonify(history), 200