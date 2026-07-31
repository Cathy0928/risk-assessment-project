import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types


# ===============================
# 載入 .env
# ===============================

BASE_DIR = Path(__file__).resolve().parent.parent

env_path = BASE_DIR / ".env"

load_dotenv(env_path)


api_key = os.getenv("GEMINI_API_KEY")


if not api_key:

    raise RuntimeError(
        "找不到 GEMINI_API_KEY，請確認 riskGenie/.env"
    )



# ===============================
# Gemini Client
# ===============================

client = genai.Client(
    api_key=api_key
)



# ===============================
# AI 風險建議
# ===============================

def generate_risk_advice(data):


    prompt = f"""
你是一位資安風險評鑑顧問。

請根據以下資訊進行風險分析，
並提出改善建議。


【資產名稱】
{data.get('asset_name')}


【CIA評估】
{data.get('cia')}


【CVSS漏洞分數】
{data.get('cvss')}


【風險值】
{data.get('risk_score')}



請依照以下格式回答：

一、風險分析
說明目前可能存在的安全風險。


二、可能影響
說明遭受攻擊後可能造成的影響。


三、改善建議
提供具體資安控制措施。


四、優先處理事項
依照風險程度排序建議處理順序。
"""



    try:


        response = client.models.generate_content(

            model="gemini-2.0-flash-lite",

            contents=prompt,

            config=types.GenerateContentConfig(

                temperature=0.3,

                max_output_tokens=1000

            )

        )


        return {

            "success": True,

            "advice": response.text

        }



    except Exception as e:


        return {

            "success": False,

            "error": str(e)

        }




# ===============================
# 測試
# ===============================

if __name__ == "__main__":


    test_data = {

        "asset_name": "Web Server",

        "cia": "C:5 I:4 A:5",

        "cvss": 9.8,

        "risk_score": 49

    }


    result = generate_risk_advice(test_data)


    print(result)