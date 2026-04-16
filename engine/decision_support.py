import json
import os

def get_rag_advice(asset_info, risk_level):
    # 1. 載入 ISO 知識庫
    file_path = os.path.join('data', 'iso_27001.json')
    with open(file_path, 'r', encoding='utf-8') as f:
        iso_data = json.load(f)

    # 2. 簡單的檢索邏輯 (D 的核心：根據關鍵字找條文)
    # 這裡未來可以升級成 OpenAI Embedding 向量搜尋
    related_controls = []
    if "資料" in asset_info or "外洩" in asset_info:
        related_controls = [c for c in iso_data if c['id'] == "A.8.11"]
    elif "權限" in asset_info or "密碼" in asset_info:
        related_controls = [c for c in iso_data if c['id'] == "A.8.3"]
    else:
        related_controls = [iso_data[0]] # 預設給第一條

    # 3. 組合 AI 建議 (Mock LLM 輸出)
    # 這裡暫時手寫，之後你只要串接 OpenAI API 把 iso_data 餵進去即可
    control = related_controls[0]
    advice = f"【AI 顧問建議】\n針對風險等級「{risk_level}」，參考 ISO 27001 規範 {control['id']} ({control['title']})：\n{control['content']}\n\n具體作法：應立即強化該資產的稽核紀錄，並實施適當的技術控制措施。"
    
    return advice