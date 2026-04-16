#-----------------------
# 匯入模組
#-----------------------
from flask import Flask, render_template, request, jsonify

#-----------------------
# 匯入各個服務藍圖 (Blueprint)
#-----------------------
# 未來若有 api/ 或 utils/ 的功能，可在此匯入並註冊
# from api.risk_api import risk_bp 

#-------------------------
# 產生主程式, 加入主畫面
#-------------------------
app = Flask(__name__)
app.secret_key = 'your_unique_secret_key'  # 設置 secret_key

# 主畫面：渲染首頁 HTML
@app.route('/')
def index():
    # 這裡會去 templates 資料夾找 index.html
    # 如果還沒建立 HTML，可以先回傳簡單的文字
    return "<h1>資安風險評估 AI 顧問系統</h1><p>系統已啟動，等待前端介面串接...</p>"

# 風險評估 API：處理 AI 分析請求
@app.route('/api/evaluate', methods=['POST'])
def evaluate():
    data = request.json
    user_input = data.get('content', '')
    
    # 這裡未來會接 AI 模型或風險計算邏輯
    # 目前先回傳固定格式的 JSON 作為測試
    result = {
        "status": "success",
        "risk_level": "High",
        "suggestion": "根據初步評估，建議優先檢查身分驗證機制與資料備份策略。"
    }
    return jsonify(result)

#-------------------------
# 在主程式註冊各個服務
#-------------------------
# app.register_blueprint(risk_bp)

#-------------------------
# 啟動主程式
#-------------------------
if __name__ == '__main__':
    # debug=True 會在程式更改時自動重啟，方便開發
    app.run(debug=True, port=5000)

