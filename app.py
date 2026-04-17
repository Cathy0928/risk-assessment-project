#-----------------------
# 模組匯入
#-----------------------
from flask import Flask, render_template, request, jsonify
from engine.decision_support import get_rag_advice

#-----------------------
# 系統初始化
#-----------------------
app = Flask(__name__)
app.secret_key = 'your_unique_secret_key'

@app.route('/')
def index():
    return render_template('index.html')

# 核心評估 API
@app.route('/api/evaluate', methods=['POST'])
def evaluate():
    data = request.json
    content = data.get('content', '')

    # 基礎風險判定邏輯
    risk_level = "高 (High)" if len(content) > 10 else "中 (Medium)"
    
    # 產出決策建議
    final_advice = get_rag_advice(content, risk_level)

    return jsonify({
        "status": "success",
        "risk_level": risk_level,
        "suggestion": final_advice
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)