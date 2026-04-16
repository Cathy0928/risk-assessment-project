import json
import os

def get_rag_advice(user_text, risk_level):
    """
    根據使用者描述與風險等級，檢索知識庫並產出專業建議。
    """
    file_path = os.path.join('data', 'iso_27001.json')
    
    if not os.path.exists(file_path):
        return "系統錯誤：無法讀取安全性標準知識庫。"

    with open(file_path, 'r', encoding='utf-8') as f:
        iso_data = json.load(f)

    # 關鍵字檢索邏輯
    keywords_map = {
        "雲端": "5.23", "cloud": "5.23",
        "權限": "5.15", "登入": "5.15", "帳號": "5.15",
        "情資": "5.7", "威脅": "5.7",
        "實體": "7.4", "監視": "7.4", "監控": "7.4",
        "加密": "8.11", "遮蔽": "8.11", "敏感": "8.11",
        "外洩": "8.12", "洩漏": "8.12",
        "程式": "8.28", "開發": "8.28", "寫code": "8.28"
    }

    matched_controls = [c for word, iso_id in keywords_map.items() 
                        if word in user_text 
                        for c in iso_data if c['id'] == iso_id]
    
    # 防呆與預設處理
    if not matched_controls:
        selected_control = next((c for c in iso_data if c['id'] == "5.15"), iso_data[0])
    else:
        selected_control = matched_controls[0]
    
    # 正式輸出格式
    advice = (
        f"【資安顧問決策報告】\n"
        f"評估風險等級：{risk_level}\n"
        f"參考國際標準：ISO 27001:2022 條款 {selected_control['id']} ({selected_control['title']})\n\n"
        f"【專業建議事項】：\n"
        f"1. 標準規範要求：「{selected_control['content']}」。\n"
        f"2. 應針對受評估資產實施相對應的安全控制措施。\n"
        f"3. 建議建立定期稽核機制，確保控制措施之有效性。"
    )
    
    return advice