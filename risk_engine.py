from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import pandas as pd


CIA_WEIGHTS = {
    "confidentiality": 0.4,
    "integrity": 0.3,
    "availability": 0.3,
}

LABEL_MULTIPLIERS = {
    "A": 1.1,
    "B": 1.0,
    "C": 0.9,
}

THREAT_LEVELS = {
    "Low": 1.0,
    "Medium": 1.2,
    "High": 1.5,
}


@dataclass
class RiskConfig:
    cia_weights: Dict[str, float] = None
    label_multipliers: Dict[str, float] = None
    threat_levels: Dict[str, float] = None

    def __post_init__(self) -> None:
        if self.cia_weights is None:
            self.cia_weights = CIA_WEIGHTS
        if self.label_multipliers is None:
            self.label_multipliers = LABEL_MULTIPLIERS
        if self.threat_levels is None:
            self.threat_levels = THREAT_LEVELS


def validate_columns(df: pd.DataFrame) -> None:
    required_columns = [
        "asset_id",
        "asset_name",
        "confidentiality",
        "integrity",
        "availability",
        "original_label",
        "threat_level",
    ]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"Excel 缺少必要欄位: {missing}")


def clamp_score(score: float, min_value: float = 0, max_value: float = 100) -> float:
    return max(min_value, min(score, max_value))


def classify_risk(score: float) -> str:
    if score <= 30:
        return "Low"
    if score <= 60:
        return "Medium"
    if score <= 80:
        return "High"
    return "Critical"


def calculate_impact_score(row: pd.Series, config: RiskConfig) -> float:
    c = float(row["confidentiality"])
    i = float(row["integrity"])
    a = float(row["availability"])

    impact = (
        config.cia_weights["confidentiality"] * c
        + config.cia_weights["integrity"] * i
        + config.cia_weights["availability"] * a
    ) * 10

    return round(impact, 2)


def calculate_final_score(row: pd.Series, config: RiskConfig) -> float:
    impact_score = calculate_impact_score(row, config)

    label = str(row["original_label"]).strip()
    threat_level = str(row["threat_level"]).strip()

    label_multiplier = config.label_multipliers.get(label, 1.0)
    threat_multiplier = config.threat_levels.get(threat_level, 1.0)

    final_score = impact_score * label_multiplier * threat_multiplier
    final_score = clamp_score(final_score)

    return round(final_score, 2)


def calculate_risk(df: pd.DataFrame, config: RiskConfig | None = None) -> pd.DataFrame:
    if config is None:
        config = RiskConfig()

    validate_columns(df)

    result_df = df.copy()

    result_df["impact_score"] = result_df.apply(
        lambda row: calculate_impact_score(row, config), axis=1
    )
    result_df["final_score"] = result_df.apply(
        lambda row: calculate_final_score(row, config), axis=1
    )
    result_df["risk_level"] = result_df["final_score"].apply(classify_risk)

    return result_df