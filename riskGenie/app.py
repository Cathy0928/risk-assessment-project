# -*- coding: utf-8 -*-
"""
Module: app
Description: Flask Web 應用程式進入點。
             負責註冊系統路由，維持首頁與資產總表之相容性，並整合風險評鑑模組。
"""

import webbrowser
from flask import Flask, render_template, jsonify, request
from models.mock_db import get_all_assets, get_all_risk_assessments, MOCK_VULNERABILITIES
from services.risk_service import perform_asset_risk_assessment

app = Flask(__name__)


# 🏠 路由 1：首頁 (http://127.0.0.1:5000/)
@app.route('/')
def home():
    """
    渲染首頁範本。
    """
    return render_template('index.html')


# 📋 路由 2：資產總表 (http://127.0.0.1:5000/summary)
@app.route('/summary')
def asset_summary():
    """
    渲染資產總表頁面，並提供前端測試用之測試資料。
    """
    mock_assets = [
        {"編號": 1, "類別": "A", "名稱": "甲", "負責單位": "A", "保管單位": "A"},
        {"編號": 2, "類別": "A", "名稱": "乙", "負責單位": "A", "保管單位": "A"},
        {"編號": 3, "類別": "B", "名稱": "丙", "負責單位": "B", "保管單位": "B"},
        {"編號": 4, "類別": "B", "名稱": "丁", "負責單位": "B", "保管單位": "B"},
        {"編號": 5, "類別": "C", "名稱": "戊", "負責單位": "C", "保管單位": "C"},
        {"編號": 6, "類別": "D", "名稱": "己", "負責單位": "D", "保管單位": "D"},
        {"編號": 7, "類別": "D", "名稱": "庚", "負責單位": "D", "保管單位": "D"}
    ]
    return render_template('asset_summary.html', assets=mock_assets)


# ⚡ 路由 3：風險評鑑介面 (http://127.0.0.1:5000/assess)
@app.route('/assess')
def risk_assessment_page():
    """
    渲染風險評鑑頁面，並傳入數據庫層之資產與弱點清單以供下拉選單使用。
    """
    assets = get_all_assets()
    vulnerabilities = MOCK_VULNERABILITIES
    return render_template('risk_assessment.html', assets=assets, vulnerabilities=vulnerabilities)


# --- RESTful API 路由端點 ---

@app.route('/api/risk/assess', methods=['POST'])
def api_assess_risk():
    """
    API 1: 執行風險評鑑計算 (POST)
    請求資料格式: JSON { "asset_id": int, "vulnerability_id": int }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "無效的 JSON 請求資料。"}), 400
            
        asset_id = data.get("asset_id")
        vulnerability_id = data.get("vulnerability_id")
        
        if not asset_id or not vulnerability_id:
            return jsonify({"status": "error", "message": "遺漏必要參數: asset_id 或 vulnerability_id。"}), 400
            
        # 呼叫業務邏輯服務層執行風險評鑑與 AI 對策分析
        result = perform_asset_risk_assessment(int(asset_id), int(vulnerability_id))
        
        return jsonify({
            "status": "success",
            "message": "風險評鑑計算完成。",
            "data": result
        }), 200
        
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"伺服器發生異常: {str(e)}"}), 500


@app.route('/api/risk/reports', methods=['GET'])
def api_get_risk_reports():
    """
    API 2: 取得歷史風險評估結果報表 (GET)
    """
    try:
        reports = get_all_risk_assessments()
        return jsonify({
            "status": "success",
            "data": reports
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"無法載入歷史風險報表: {str(e)}"}), 500


if __name__ == '__main__':
    # 啟動時自動用外部瀏覽器開啟首頁
    webbrowser.open("http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
