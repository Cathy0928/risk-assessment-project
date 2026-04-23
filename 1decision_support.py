import json
import os


def get_rag_advice(asset_name, asset_type, asset_description, risk_level):

    # =========================
    # 📚 1. 讀 ISO 知識庫
    # =========================
    file_path = os.path.join('data', 'iso_27001.json')

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            iso_data = json.load(f)
    except Exception as e:
        return {
            "status": "error",
            "message": f"❌ 知識庫讀取失敗：{str(e)}"
        }

    # =========================
    # 🔍 2. 找最相關條文
    # =========================
    matched_controls = [
        c for c in iso_data
        if asset_type and asset_type.lower() in str(c.get('category', '')).lower()
    ]

    selected_control = matched_controls[0] if matched_controls else iso_data[0]

    # =========================
    # 🧠 3. 本地 AI 顧問（不用 API）
    # =========================

    # 風險等級對應建議
    risk_map = {
        "Critical": "立即修補，限制存取權限並進行資安稽核",
        "High": "優先修補漏洞並加強監控",
        "Medium": "定期檢查與強化防護措施",
        "Low": "維持現有安全控管即可"
    }

    base_advice = risk_map.get(risk_level, "加強基本資安防護")

    # ISO 條文輔助建議
    iso_advice = selected_control.get("content", "")

    # =========================
    # 📄 4. 組合輸出（RAG 模擬）
    # =========================
    final_advice = f"""
💡 改善建議：
- {base_advice}
- 根據 ISO 建議：{iso_advice}

📉 降風險效果：
- 可降低約 20%~60% 風險（依實作情況）
- 建議優先處理高風險資產
"""

    return {
        "status": "success",
        "control_id": selected_control.get("id", ""),
        "title": selected_control.get("content", ""),
        "advice": final_advice
    }