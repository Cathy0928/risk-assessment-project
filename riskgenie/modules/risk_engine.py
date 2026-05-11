"""
Risk Engine
-----------
Rule-based scoring. AI 只負責建議 CIA / 威脅情資輸入,
最終分數由這支算,確保可解釋、可重算、可審計。

Final Score = clamp( ImpactScore × LabelMultiplier × ThreatMultiplier , 0, 100 )

ImpactScore = (0.4·C + 0.3·I + 0.3·A) × 10
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict
import pandas as pd


# =========================
# 預設權重 / 倍率
# =========================

CIA_WEIGHTS: Dict[str, float] = {
    "confidentiality": 0.4,
    "integrity": 0.3,
    "availability": 0.3,
}

# 同時支援中文 / A-B-C 兩種 label 寫法
LABEL_MULTIPLIERS: Dict[str, float] = {
    # 中文(對應 Excel 實際內容)
    "高": 1.1,
    "中": 1.0,
    "低": 0.9,
    # 英文 / 字母
    "A": 1.1,
    "B": 1.0,
    "C": 0.9,
    "HIGH": 1.1,
    "MEDIUM": 1.0,
    "LOW": 0.9,
}

THREAT_LEVELS: Dict[str, float] = {
    "LOW": 1.0,
    "MEDIUM": 1.2,
    "HIGH": 1.5,
    "CRITICAL": 1.8,
    # 中文
    "低": 1.0,
    "中": 1.2,
    "高": 1.5,
    "極高": 1.8,
}


@dataclass
class RiskConfig:
    cia_weights: Dict[str, float] = field(default_factory=lambda: CIA_WEIGHTS.copy())
    label_multipliers: Dict[str, float] = field(default_factory=lambda: LABEL_MULTIPLIERS.copy())
    threat_levels: Dict[str, float] = field(default_factory=lambda: THREAT_LEVELS.copy())


# =========================
# Helpers
# =========================

def _safe_float(x, default: float = 5.0) -> float:
    try:
        if pd.isna(x):
            return default
        return float(x)
    except (ValueError, TypeError):
        return default


def _normalize_key(x) -> str:
    """把 label / threat_level 統一成查表用 key:去空白 + upper(英文才會變,中文不變)"""
    if pd.isna(x):
        return ""
    return str(x).strip().upper()


def _clamp(score: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, score))


def classify_risk(score: float) -> str:
    if score <= 30:
        return "Low"
    if score <= 60:
        return "Medium"
    if score <= 80:
        return "High"
    return "Critical"


# =========================
# Validation
# =========================

REQUIRED_COLUMNS = [
    "asset_id",
    "asset_name",
    "confidentiality",
    "integrity",
    "availability",
    "original_label",
    "threat_level",
]


def validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少必要欄位:{missing}")


# =========================
# Scoring
# =========================

def calculate_impact_score(row: pd.Series, config: RiskConfig) -> float:
    c = _safe_float(row["confidentiality"])
    i = _safe_float(row["integrity"])
    a = _safe_float(row["availability"])
    impact = (
        config.cia_weights["confidentiality"] * c
        + config.cia_weights["integrity"] * i
        + config.cia_weights["availability"] * a
    ) * 10
    return round(impact, 2)


def calculate_final_score(row: pd.Series, config: RiskConfig) -> float:
    impact = calculate_impact_score(row, config)

    label_key = _normalize_key(row.get("original_label", ""))
    threat_key = _normalize_key(row.get("threat_level", ""))

    label_mult = config.label_multipliers.get(label_key, 1.0)
    threat_mult = config.threat_levels.get(threat_key, 1.0)

    score = impact * label_mult * threat_mult
    return round(_clamp(score), 2)


# =========================
# Public API
# =========================

def calculate_risk(df: pd.DataFrame, config: RiskConfig | None = None) -> pd.DataFrame:
    """回傳含 impact_score / final_score / risk_level 三欄的新 DataFrame。"""
    cfg = config or RiskConfig()
    validate_columns(df)

    out = df.copy()
    out["impact_score"] = out.apply(lambda r: calculate_impact_score(r, cfg), axis=1)
    out["final_score"] = out.apply(lambda r: calculate_final_score(r, cfg), axis=1)
    out["risk_level"] = out["final_score"].apply(classify_risk)
    return out
