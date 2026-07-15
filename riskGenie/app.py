# -*- coding: utf-8 -*-
"""
核心入口檔案: app.py
"""
import webbrowser
from flask import Flask, render_template, jsonify, request
# 匯入模擬資料庫與風險評鑑服務
from models.mock_db import get_all_assets, get_all_risk_assessments
from services.risk_service import perform_asset_risk_assessment

app = Flask(__name__)

# 🏠 路由 1：首頁 (http://127.0.0.1:5000/)
@app.route('/')
def home():
    return render_template('index.html')

# 📋 路由 2：資產總表 (http://127.0.0.1:5000/summary)
@app.route('/summary')
def asset_summary():
    # 原先的假資料可以直接用 models.mock_db 裡面的結構讀取，達到前後端分離
    assets_data = get_all_assets()
    return render_template('asset_summary.html', assets=assets_data)


# --- 🆕 以下為林敬芬負責新增之風險評鑑後端 API 路由 [14, 15] ---

@app.route('/api/risk/assess', methods=['POST'])
def api_assess_risk():
    """
    API 路由：執行風險評鑑
    前端傳送 JSON 格式: { "asset_id": 1, "vulnerability_id": 101 }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "請提供 JSON 評估請求資料"}), 400
            
        asset_id = data.get("asset_id")
        vulnerability_id = data.get("vulnerability_id")
        
        if not asset_id or not vulnerability_id:
            return jsonify({"status": "error", "message": "缺少 asset_id 或 vulnerability_id"}), 400
            
        # 執行核心服務運算 [7]
        result = perform_asset_risk_assessment(int(asset_id), int(vulnerability_id))
        
        return jsonify({
            "status": "success",
            "message": "風險評鑑計算完成",
            "data": result
        }), 200
        
    except ValueError as ve:
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": f"伺服器錯誤: {str(e)}"}), 500


@app.route('/api/risk/reports', methods=['GET'])
def api_get_risk_reports():
    """
    API 路由：取得所有風險評估結果（用於顯示風險報表） [3, 5]
    """
    try:
        reports = get_all_risk_assessments()
        return jsonify({
            "status": "success",
            "count": len(reports),
            "data": reports
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": f"讀取報表失敗: {str(e)}"}), 500


if __name__ == '__main__':
    # 啟動時自動用外部瀏覽器開啟首頁
    webbrowser.open("http://127.0.0.1:5000")
    app.run(debug=True, port=5000)