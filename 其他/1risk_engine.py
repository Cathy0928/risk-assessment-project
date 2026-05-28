from __future__ import annotations
from dataclasses import dataclass
from typing import Dict
import pandas as pd

# =========================
# config
# =========================

CIA_WEIGHTS = {
    "confidentiality": 0.4,
    "integrity": 0.3,
    "availability": 0.3,
}

LABEL_MULTIPLIERS = {"A": 1.1, "B": 1.0, "C": 0.9}
THREAT_LEVELS = {"Low": 1.0, "Medium": 1.2, "High": 1.5}


@dataclass
class RiskConfig:
    cia_weights: Dict[str, float] = None
    label_multipliers: Dict[str, float] = None
    threat_levels: Dict[str, float] = None

    def __post_init__(self):
        self.cia_weights = self.cia_weights or CIA_WEIGHTS
        self.label_multipliers = self.label_multipliers or LABEL_MULTIPLIERS
        self.threat_levels = self.threat_levels or THREAT_LEVELS


# =========================
# utils
# =========================

def safe_float(x):
    try:
        if pd.isna(x):
            return 5.0
        return float(x)
    except:
        return 5.0


def normalize(x):
    if pd.isna(x):
        return ""
    return str(x).strip().title()


def clamp(x):
    return max(0, min(100, x))


# =========================
# validate
# =========================

def validate_columns(df):
    required = [
        "asset_id_code",
        "asset_name",
        "confidentiality",
        "integrity",
        "availability",
        "original_label",
        "threat_level",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")


# =========================
# scoring
# =========================

def impact(row, cfg):
    c = safe_float(row["confidentiality"])
    i = safe_float(row["integrity"])
    a = safe_float(row["availability"])

    return (c * cfg.cia_weights["confidentiality"]
            + i * cfg.cia_weights["integrity"]
            + a * cfg.cia_weights["availability"]) * 10


def final(row, cfg):
    base = impact(row, cfg)

    label = normalize(row["original_label"])
    threat = normalize(row["threat_level"])

    score = base * cfg.label_multipliers.get(label, 1.0) * cfg.threat_levels.get(threat, 1.2)

    if pd.isna(score):
        score = 50

    return round(clamp(score), 2)


def classify(score):
    if score <= 30:
        return "Low"
    if score <= 60:
        return "Medium"
    if score <= 80:
        return "High"
    return "Critical"


# =========================
# main
# =========================

def calculate_risk(df, cfg=None):
    cfg = cfg or RiskConfig()

    validate_columns(df)
    df = df.copy()

    df["impact_score"] = df.apply(lambda r: impact(r, cfg), axis=1)
    df["final_score"] = df.apply(lambda r: final(r, cfg), axis=1)

    df["risk_level"] = df["final_score"].apply(classify)

    df["asset_id_code"] = df["asset_id_code"].fillna("UNKNOWN")

    return df