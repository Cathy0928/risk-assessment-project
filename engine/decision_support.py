import json
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

def get_rag_advice(asset_name, asset_type, asset_description, risk_level):
    # 1. 讀取知識庫
    file_path = os.path.join('data', 'iso_27001.json')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            iso_data = json.load(f)
    except Exception as e:
        return f"無法讀取知識庫：{str(e)}"

    # 2. 檢索最相關條文 (簡單過濾)
    matched_controls = [c for c in iso_data if c.get('category') in asset_type]
    selected_control = matched_controls[0] if matched_controls else iso_data[0]

    # 3. 呼叫 Gemini (免費版)
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    model = genai.GenerativeModel('gemini-1.5-flash') # 使用最新的快速免費模型

    prompt = f"""
    你是一位資深資安顧問。請針對以下資產，參考 ISO 27001 給出「白話」建議。
    資產名稱：{asset_name} | 類型：{asset_type} | 風險：{risk_level}
    參考法規條號：{selected_control['id']} | 內容：{selected_control['content']}
    
    格式：
    💡 專家白話建議：(怎麼改進，不要用專業術語)
    📉 具體降險效益：(預計降低幾 % 風險及理由)
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 顧問忙線中，原始建議：{selected_control['content']}"