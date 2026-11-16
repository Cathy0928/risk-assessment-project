# -*- coding: utf-8 -*-
"""
相容層:舊的 RiskEngine 介面。

風險公式已統一到 riskGenie/services/risk_formula.py(鎖定公式,0-100)。
本類別只保留舊介面並轉呼叫該模組,不再自行實作公式,
避免系統出現兩套互相矛盾的計算結果。
"""

try:
    from riskGenie.services import risk_formula
except ImportError:  # 從 riskGenie 目錄內直接執行時
    from services import risk_formula


class RiskEngine:
    @staticmethod
    def calculate_risk(c, i, a, cvss_score, formula_type=None, weights=None) -> float:
        """
        回傳 0-100 的最終風險分數。

        formula_type 參數保留以相容舊呼叫,已不影響結果;
        權重由 weights(例如 {'c': 0.4, 'i': 0.3, 'a': 0.3})決定,
        未提供時使用鎖定預設 0.4 / 0.3 / 0.3。
        輸入不合法時回傳 0.0。
        """
        try:
            result = risk_formula.calculate_risk(
                c, i, a, 0, cvss_score, weights=weights
            )
        except risk_formula.RiskInputError:
            return 0.0
        return result["risk_score"]

    @staticmethod
    def get_risk_level(risk_score: float) -> str:
        """0-30 低 / 31-60 中 / 61-80 高 / 81-100 極高(Critical)。"""
        try:
            score = float(risk_score)
        except (ValueError, TypeError):
            return risk_formula.LEVEL_LOW
        return risk_formula.level_for_score(score)
