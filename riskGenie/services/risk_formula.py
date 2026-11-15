# -*- coding: utf-8 -*-
"""
RiskGenie 風險計算:唯一權威實作(鎖定公式)。

    Final Risk Score = clamp(Impact x LabelMult x ThreatMult, 0, 100)
    Impact           = (Wc*C + Wi*I + Wa*A) x 10        (預設權重 0.4 / 0.3 / 0.3)
    LabelMult        = 高 1.1 / 中 1.0 / 低 0.9          (由資產價值分級)
    ThreatMult       = low 1.0 / medium 1.2 / high 1.5 / critical 1.8  (由 CVSS 分級)

風險等級:0-30 低 / 31-60 中 / 61-80 高 / 81-100 極高(= Critical)

計算、儲存、AI 建議、報表都必須呼叫本模組,不得各自實作公式。
AI 不參與分數計算(可解釋、可重算、可審計)。
"""

DEFAULT_WEIGHTS = {"c": 0.4, "i": 0.3, "a": 0.3}

LABEL_MULT = {"高": 1.1, "中": 1.0, "低": 0.9}
THREAT_MULT = {"low": 1.0, "medium": 1.2, "high": 1.5, "critical": 1.8}

# 對應既有 UI / 報表使用的中文等級字串
LEVEL_LOW = "低風險"
LEVEL_MEDIUM = "中風險"
LEVEL_HIGH = "高風險"
LEVEL_CRITICAL = "極高風險"


class RiskInputError(ValueError):
    """輸入不合法(超出 1~5、非數字、CVSS 超出 0~10 等)。"""


def _as_number(value, name):
    if isinstance(value, bool) or value is None:
        raise RiskInputError(f"{name} 必須是數字")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise RiskInputError(f"{name} 必須是數字")
    if number != number or number in (float("inf"), float("-inf")):
        raise RiskInputError(f"{name} 必須是有限數字")
    return number


def validate_cia(value, name):
    number = _as_number(value, name)
    if not 1 <= number <= 5:
        raise RiskInputError(f"{name} 必須介於 1~5")
    return number


def validate_legality(value):
    number = _as_number(value, "適法性")
    if not 0 <= number <= 5:
        raise RiskInputError("適法性必須介於 0~5")
    return number


def validate_cvss(value):
    if value is None or value == "":
        return 0.0
    number = _as_number(value, "CVSS")
    if not 0 <= number <= 10:
        raise RiskInputError("CVSS 必須介於 0~10")
    return number


def normalize_weights(weights=None):
    """回傳總和為 1 的 (Wc, Wi, Wa);缺值或不合法時用鎖定預設 0.4/0.3/0.3。"""
    if not isinstance(weights, dict):
        return dict(DEFAULT_WEIGHTS)
    try:
        wc = float(weights.get("c"))
        wi = float(weights.get("i"))
        wa = float(weights.get("a"))
    except (TypeError, ValueError):
        return dict(DEFAULT_WEIGHTS)
    if min(wc, wi, wa) < 0:
        return dict(DEFAULT_WEIGHTS)
    total = wc + wi + wa
    if total <= 0 or total != total:
        return dict(DEFAULT_WEIGHTS)
    return {"c": wc / total, "i": wi / total, "a": wa / total}


def weights_from_settings(settings):
    """
    由公司權重設定取得權重。
    只有公司明確選擇 weighted_average 才使用自訂權重;
    其餘(含舊的 max/sum/avg 設定)一律使用鎖定預設 0.4/0.3/0.3。
    """
    if not isinstance(settings, dict):
        return dict(DEFAULT_WEIGHTS)
    if settings.get("formula_type") != "weighted_average":
        return dict(DEFAULT_WEIGHTS)
    return normalize_weights({
        "c": settings.get("weight_c"),
        "i": settings.get("weight_i"),
        "a": settings.get("weight_a"),
    })


def label_for_asset_value(asset_value):
    """資產價值 4~5 -> 高;3 -> 中;其餘 -> 低。"""
    if asset_value >= 4:
        return "高"
    if asset_value >= 3:
        return "中"
    return "低"


def threat_level_for_cvss(cvss):
    """CVSS v3.1 嚴重度分級;沒有弱點(0)視為 low。"""
    if cvss >= 9.0:
        return "critical"
    if cvss >= 7.0:
        return "high"
    if cvss >= 4.0:
        return "medium"
    return "low"


def level_for_score(score):
    if score <= 30:
        return LEVEL_LOW
    if score <= 60:
        return LEVEL_MEDIUM
    if score <= 80:
        return LEVEL_HIGH
    return LEVEL_CRITICAL


def calculate_risk(c, i, a, legality, cvss, weights=None):
    """
    由原始輸入計算風險,回傳完整可審計的結果。
    c, i, a 必須 1~5;legality 0~5(只用於資產價值分級);cvss 0~10。
    """
    c = validate_cia(c, "機密性")
    i = validate_cia(i, "完整性")
    a = validate_cia(a, "可用性")
    legality = validate_legality(legality if legality not in (None, "") else 0)
    cvss = validate_cvss(cvss)
    w = normalize_weights(weights)

    impact = (w["c"] * c + w["i"] * i + w["a"] * a) * 10
    asset_value = max(c, i, a, legality)
    label = label_for_asset_value(asset_value)
    label_mult = LABEL_MULT[label]
    threat = threat_level_for_cvss(cvss)
    threat_mult = THREAT_MULT[threat]

    raw = impact * label_mult * threat_mult
    score = round(min(max(raw, 0.0), 100.0), 2)

    return {
        "impact_score": round(impact, 2),
        "asset_value": asset_value,
        "label": label,
        "label_mult": label_mult,
        "threat_level": threat,
        "threat_mult": threat_mult,
        "risk_score": score,
        "risk_level": level_for_score(score),
        "weights": {k: round(v, 4) for k, v in w.items()},
        "formula_used": "weighted_average",
    }
