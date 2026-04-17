from flask import Flask, render_template, request, jsonify
from engine.decision_support import get_rag_advice

app = Flask(__name__)
app.secret_key = "your_unique_secret_key"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/evaluate", methods=["POST"])
def evaluate():
    data = request.json
    content = data.get("content", "")

    risk_level = "高 (High)" if len(content) > 10 else "中 (Medium)"
    final_advice = get_rag_advice(content, risk_level)

    return jsonify({
        "status": "success",
        "risk_level": risk_level,
        "suggestion": final_advice
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)