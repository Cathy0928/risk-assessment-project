import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# ===============================
# 載入 .env
# ===============================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
env_path = PROJECT_ROOT / ".env"
load_dotenv(env_path)


class GeminiConfigurationError(RuntimeError):
    """Raised when Gemini cannot be called because local configuration is incomplete."""


class GeminiServiceError(RuntimeError):
    """Raised when Gemini is configured but the service call fails."""


def is_gemini_configured():
    return bool(os.getenv("GEMINI_API_KEY"))


def _load_genai():
    try:
        import google.generativeai as genai
    except ImportError as exc:
        raise GeminiConfigurationError(
            "google-generativeai is not installed."
        ) from exc
    return genai


def _build_prompt(data):
    return f"""
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


# ===============================
# AI 風險建議
# ===============================

def generate_risk_advice(data):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

    prompt = _build_prompt(data)

    try:
        genai = _load_genai()
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-2.0-flash-lite",
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 1000,
            },
        )
        response = model.generate_content(prompt)
        return {
            "success": True,
            "advice": getattr(response, "text", ""),
        }
    except GeminiConfigurationError:
        raise
    except Exception as e:
        logger.exception("Gemini advice generation failed.")
        raise GeminiServiceError("Gemini service request failed.") from e


# ===============================
# 測試
# ===============================

if __name__ == "__main__":
    test_data = {
        "asset_name": "Web Server",
        "cia": "C:5 I:4 A:5",
        "cvss": 9.8,
        "risk_score": 49,
    }

    result = generate_risk_advice(test_data)
    print(result)
