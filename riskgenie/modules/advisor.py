"""
AI Advisor (RAG)
----------------
針對高風險資產,從 ISO 27002:2022 控制措施知識庫檢索相關條文,
再由 Gemini 產出白話建議。

實作策略:
- 預設用 sentence-transformers + 餘弦相似度做語意檢索(輕量,免向量 DB)
- 若沒裝 sentence-transformers,fallback 用 TF-IDF
- 若連 sklearn 都沒有,fallback 用 keyword overlap
- LLM 部分:有 GEMINI_API_KEY 走 Gemini,沒有則用模板輸出
這樣不論評審環境裝什麼,demo 都跑得起來。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Dict

try:
    import google.generativeai as genai
    _HAS_GEMINI = True
except ImportError:
    _HAS_GEMINI = False

try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    _HAS_ST = True
except ImportError:
    _HAS_ST = False

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# =========================
# 知識庫載入
# =========================

_KB_PATH = Path(__file__).parent.parent / "data" / "iso_27002_kb.json"
_KB_CACHE: List[Dict] | None = None
_EMBEDDINGS_CACHE = None
_ST_MODEL = None
_TFIDF_VECTORIZER = None
_TFIDF_MATRIX = None


def _load_kb() -> List[Dict]:
    global _KB_CACHE
    if _KB_CACHE is not None:
        return _KB_CACHE
    if not _KB_PATH.exists():
        # 預設 fallback 知識庫,避免檔案缺失整個壞掉
        _KB_CACHE = []
        return _KB_CACHE
    with open(_KB_PATH, "r", encoding="utf-8") as f:
        _KB_CACHE = json.load(f)
    return _KB_CACHE


def _kb_to_text(item: Dict) -> str:
    return f"{item.get('title','')} {item.get('content','')} {item.get('category','')}"


# =========================
# 檢索層
# =========================

def _retrieve_st(query: str, top_k: int = 3) -> List[Dict]:
    """sentence-transformers 語意檢索"""
    global _ST_MODEL, _EMBEDDINGS_CACHE
    kb = _load_kb()
    if not kb:
        return []
    if _ST_MODEL is None:
        _ST_MODEL = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    if _EMBEDDINGS_CACHE is None:
        texts = [_kb_to_text(x) for x in kb]
        _EMBEDDINGS_CACHE = _ST_MODEL.encode(texts, convert_to_numpy=True)
    q_vec = _ST_MODEL.encode([query], convert_to_numpy=True)
    sims = (_EMBEDDINGS_CACHE @ q_vec.T).flatten() / (
        np.linalg.norm(_EMBEDDINGS_CACHE, axis=1) * np.linalg.norm(q_vec) + 1e-8
    )
    top_idx = sims.argsort()[::-1][:top_k]
    return [{**kb[i], "score": float(sims[i])} for i in top_idx]


def _retrieve_tfidf(query: str, top_k: int = 3) -> List[Dict]:
    """TF-IDF fallback"""
    global _TFIDF_VECTORIZER, _TFIDF_MATRIX
    kb = _load_kb()
    if not kb:
        return []
    if _TFIDF_VECTORIZER is None:
        # 中文建議用 char ngram
        _TFIDF_VECTORIZER = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
        texts = [_kb_to_text(x) for x in kb]
        _TFIDF_MATRIX = _TFIDF_VECTORIZER.fit_transform(texts)
    q_vec = _TFIDF_VECTORIZER.transform([query])
    sims = cosine_similarity(q_vec, _TFIDF_MATRIX).flatten()
    top_idx = sims.argsort()[::-1][:top_k]
    return [{**kb[i], "score": float(sims[i])} for i in top_idx]


def _retrieve_keyword(query: str, top_k: int = 3) -> List[Dict]:
    """最後備援:keyword overlap"""
    kb = _load_kb()
    if not kb:
        return []
    q_tokens = set(query.lower())
    scored = []
    for item in kb:
        text = _kb_to_text(item).lower()
        score = sum(1 for ch in q_tokens if ch in text)
        scored.append({**item, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def retrieve(query: str, top_k: int = 3) -> List[Dict]:
    """智慧選擇可用的檢索方式"""
    if _HAS_ST:
        try:
            return _retrieve_st(query, top_k)
        except Exception:
            pass
    if _HAS_SKLEARN:
        try:
            return _retrieve_tfidf(query, top_k)
        except Exception:
            pass
    return _retrieve_keyword(query, top_k)


# =========================
# 生成層
# =========================

_ADVICE_PROMPT = """你是資安顧問。針對下方資產與其風險,參考 ISO 27002:2022 條文,
給出三段式回覆:1) 白話風險說明 2) 具體改善動作(條列 3 點) 3) 預期效益。

資產:{asset_name}({asset_type})
風險等級:{risk_level}(分數 {score})
受影響威脅:{triggered_by}

ISO 27002 參考條文:
{controls}

請用繁體中文回答,不要用 markdown 標題符號,語氣務實簡潔。
"""


def _format_controls(controls: List[Dict]) -> str:
    return "\n".join(
        f"- A.{c.get('id','')} {c.get('title','')}:{c.get('content','')}"
        for c in controls
    )


def _llm_generate(asset_name: str, asset_type: str, risk_level: str,
                  score: float, triggered_by: str, controls: List[Dict]) -> str:
    if not _HAS_GEMINI or not os.getenv("GEMINI_API_KEY"):
        return None
    try:
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = _ADVICE_PROMPT.format(
            asset_name=asset_name,
            asset_type=asset_type,
            risk_level=risk_level,
            score=score,
            triggered_by=triggered_by or "無特定外部威脅",
            controls=_format_controls(controls),
        )
        resp = model.generate_content(prompt)
        return resp.text.strip()
    except Exception:
        return None


def _template_advice(asset_name: str, risk_level: str, controls: List[Dict]) -> str:
    """LLM 不可用時的模板輸出"""
    risk_map = {
        "Critical": "需立即處理,建議 24 小時內成立應變小組",
        "High": "優先處置,建議兩週內完成改善",
        "Medium": "排入季度資安改善計畫",
        "Low": "維持現有控制即可,定期複查",
    }
    lines = [
        f"【風險摘要】{asset_name} 經評估為 {risk_level} 級。{risk_map.get(risk_level,'')}",
        "",
        "【建議控制】",
    ]
    for c in controls:
        lines.append(f"- A.{c.get('id','')} {c.get('title','')}:{c.get('content','')}")
    lines.append("")
    lines.append("【預期效益】落實上述控制可顯著降低該資產之風險暴露面,並符合 ISO 27001 ISMS 要求。")
    return "\n".join(lines)


# =========================
# Public API
# =========================

def get_advice(asset_name: str, asset_type: str, risk_level: str,
               score: float = 0, triggered_by: str = "",
               top_k: int = 3) -> Dict:
    """
    回傳:
      {
        "controls": [...],  # 檢索到的 ISO 控制
        "advice":  "...",   # AI / 模板生成的建議文字
        "method":  "llm" | "template",
        "retrieval": "sentence-transformer" | "tfidf" | "keyword",
      }
    """
    query = f"{asset_name} {asset_type} {triggered_by} {risk_level}"
    controls = retrieve(query, top_k=top_k)

    advice_text = _llm_generate(asset_name, asset_type, risk_level, score, triggered_by, controls)
    if advice_text:
        method = "llm"
    else:
        advice_text = _template_advice(asset_name, risk_level, controls)
        method = "template"

    if _HAS_ST:
        retrieval = "sentence-transformer"
    elif _HAS_SKLEARN:
        retrieval = "tfidf"
    else:
        retrieval = "keyword"

    return {
        "controls": controls,
        "advice": advice_text,
        "method": method,
        "retrieval": retrieval,
    }
