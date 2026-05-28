"""
AI Tagger
---------
給每筆資產「建議」CIA 與資產類型,並提供建議理由。
不會覆蓋使用者原本填的 CIA 值,而是另存到 ai_c / ai_i / ai_a 三欄,
方便主題一展示「人工 vs AI 差異」這個賣點。

優先順序:
  1. 有 GEMINI_API_KEY → 走 LLM
  2. 沒 key 或失敗 → 走 rule-based fallback(demo 永遠不會炸)
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Tuple

import pandas as pd

try:
    import google.generativeai as genai
    _HAS_GEMINI = True
except ImportError:
    _HAS_GEMINI = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# =========================
# Rule-based fallback
# =========================

# 關鍵字對應 (C, I, A, asset_type, reason)
_KEYWORD_RULES = [
    # 客戶 / 個資類
    (["客戶", "customer", "個資", "personal", "會員"],
     (9, 8, 7, "資料庫", "包含個人資料,機密性極高")),
    # 財務 / 交易類
    (["財務", "finance", "交易", "transaction", "payment", "金流", "erp"],
     (9, 9, 8, "資料庫", "涉及財務資料完整性與機密性要求極高")),
    # 員工 / 人事
    (["員工", "employee", "人事", "薪資", "hr"],
     (8, 8, 6, "資料庫", "包含個資,洩漏會造成法遵風險")),
    # 訂單
    (["訂單", "order"],
     (7, 8, 7, "應用系統", "訂單完整性影響營運")),
    # 郵件
    (["郵件", "email", "mail"],
     (7, 7, 8, "應用系統", "可用性影響溝通,內容含敏感資訊")),
    # 網站 / 對外
    (["網站", "website", "官網", "web"],
     (5, 7, 9, "應用系統", "對外服務,可用性最重要")),
    # 雲端 / 主機 / 伺服器
    (["雲端", "cloud", "server", "主機", "伺服器"],
     (7, 7, 9, "硬體", "基礎設施,可用性是關鍵")),
    # 備份
    (["備份", "backup"],
     (8, 9, 6, "硬體", "完整性最重要,是復原依據")),
    # 端點 / 筆電
    (["筆電", "laptop", "endpoint", "端點", "電腦"],
     (6, 6, 6, "硬體", "一般使用者端設備")),
    # 政策 / 文件
    (["政策", "policy", "規範"],
     (5, 8, 5, "文件", "文件完整性影響合規")),
    # 合約
    (["合約", "contract"],
     (8, 9, 6, "文件", "法律效力,完整性與機密性高")),
]

_DEFAULT = (6, 6, 6, "其他", "預設中度評估,建議人工複核")


def _rule_based_suggest(asset_name: str, description: str, asset_type_hint: str = "") -> Dict:
    text = f"{asset_name} {description} {asset_type_hint}".lower()
    for keywords, (c, i, a, atype, reason) in _KEYWORD_RULES:
        if any(k.lower() in text for k in keywords):
            return {
                "ai_c": c, "ai_i": i, "ai_a": a,
                "ai_asset_type": atype,
                "ai_reason": reason,
                "ai_source": "rule",
            }
    c, i, a, atype, reason = _DEFAULT
    return {
        "ai_c": c, "ai_i": i, "ai_a": a,
        "ai_asset_type": atype,
        "ai_reason": reason,
        "ai_source": "rule",
    }


# =========================
# LLM-based (Gemini)
# =========================

_LLM_PROMPT = """你是資安顧問,協助 ISMS 資產盤點。
請依以下資產資訊,以 ISO 27001 思維給出 C / I / A 建議分數(1-10)及資產類型。

資產名稱:{name}
描述:{desc}
資料類型:{data_type}
擁有者:{owner}
系統依賴:{dep}

請只回傳 JSON,格式如下:
{{
  "asset_type": "資料庫 / 應用系統 / 硬體 / 文件 / 服務 / 其他",
  "confidentiality": 1-10 整數,
  "integrity": 1-10 整數,
  "availability": 1-10 整數,
  "reason": "一句話理由(50 字內)"
}}
"""


def _llm_suggest(row: pd.Series) -> Dict | None:
    """成功回 dict,失敗回 None 讓上層 fallback。"""
    if not _HAS_GEMINI:
        return None
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = _LLM_PROMPT.format(
            name=row.get("asset_name", ""),
            desc=row.get("description", ""),
            data_type=row.get("data_type", ""),
            owner=row.get("owner", ""),
            dep=row.get("system_dependency", ""),
        )
        resp = model.generate_content(prompt)
        text = resp.text.strip()
        # 拔掉 ```json``` 包裝
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        data = json.loads(text)
        return {
            "ai_c": int(data.get("confidentiality", 5)),
            "ai_i": int(data.get("integrity", 5)),
            "ai_a": int(data.get("availability", 5)),
            "ai_asset_type": data.get("asset_type", "其他"),
            "ai_reason": data.get("reason", ""),
            "ai_source": "llm",
        }
    except Exception as e:
        # 安靜失敗,讓 fallback 接手
        return None


# =========================
# Public API
# =========================

def apply_ai_tagger(df: pd.DataFrame, prefer_llm: bool = True) -> pd.DataFrame:
    """
    為每筆資產加上 AI 建議的 CIA + asset_type + reason。
    不會覆蓋使用者原本的 confidentiality / integrity / availability。
    """
    df = df.copy()
    for col in ["description", "data_type", "owner", "system_dependency"]:
        if col not in df.columns:
            df[col] = ""

    suggestions = []
    for _, row in df.iterrows():
        result = None
        if prefer_llm:
            result = _llm_suggest(row)
        if result is None:
            result = _rule_based_suggest(
                str(row.get("asset_name", "")),
                str(row.get("description", "")),
                str(row.get("asset_type", "")),
            )
        suggestions.append(result)

    sug_df = pd.DataFrame(suggestions, index=df.index)
    df = pd.concat([df, sug_df], axis=1)

    # 計算人工 vs AI 差異(主題一賣點)
    if "confidentiality" in df.columns:
        df["cia_delta"] = (
            (df["ai_c"] - df["confidentiality"].fillna(5)).abs()
            + (df["ai_i"] - df["integrity"].fillna(5)).abs()
            + (df["ai_a"] - df["availability"].fillna(5)).abs()
        )
    return df
