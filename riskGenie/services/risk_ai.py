import os
import google.generativeai as genai
from dotenv import load_dotenv


load_dotenv()


genai.configure(
    api_key=os.getenv("GEMINI_API_KEY")
)


def generate_risk_advice(data):

    prompt = f"""
    你是一位資安風險評鑑顧問。

    資產：
    {data['asset_name']}

    CIA：
    {data['cia']}

    CVSS：
    {data['cvss']}

    風險值：
    {data['risk_score']}

    請提供：
    1.風險分析
    2.可能影響
    3.改善建議
    4.優先處理事項
    """

    model = genai.GenerativeModel(
        "gemini-2.5-flash"
    )

    response = model.generate_content(prompt)


    return {
        "advice": response.text
    }