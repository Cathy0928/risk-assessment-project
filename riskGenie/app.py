# app.py
from flask import Flask, render_template

app = Flask(__name__)

# 🏠 路由 1：首頁 (http://127.0.0.1:5000/)
@app.route('/')
def home():
    return render_template('index.html')

# 📋 路由 2：資產總表 (http://127.0.0.1:5000/summary)
@app.route('/summary')
def asset_summary():
    # 前端測試用的七筆假資料
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

if __name__ == '__main__':
    import webbrowser
    # 啟動時自動用外部瀏覽器開啟首頁
    webbrowser.open("http://127.0.0.1:5000")
    app.run(debug=True, port=5000)