#-----------------------
# 匯入模組
#-----------------------
from flask import Flask, render_template, request, jsonify
from engine.decision_support import get_rag_advice # 這裡修正了！

#-----------------------
# 產生主程式
#-----------------------
app = Flask(__name__)
app.secret_key = 'your_unique_secret_key'

@app.route('/')
def index():
    return render_template('index.html')

# 成員 D 的核心 API
@app.route('/api/evaluate', methods=['POST'])
def evaluate():
    data = request.json
    content = data.get('content', '')

    # 這裡 Mock B 與 C 的結果 (假設他們已經算好了)
    mock_risk_level = "High" if len(content) > 10 else "Low"
    
    # 呼叫 D 的 RAG 邏輯
    final_advice = get_rag_advice(content, mock_risk_level)

    return jsonify({
        "status": "success",
        "risk_level": mock_risk_level,
        "suggestion": final_advice
    })

#-------------------------
# 啟動主程式
#-------------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)