# -*- coding: utf-8 -*-
"""
負責風險值的核心計算邏輯與風險等級判定。
     支援四大評鑑公式：最大值法、總合法、平均法、加權平均法。
"""

class RiskEngine:
    @staticmethod
    def calculate_risk(c: int, i: int, a: int, cvss_score: float, formula_type: str, weights: dict = None) -> float:
        """
        計算風險分數的核心方法。
        
        計算公式說明：
        1. 最大值法 (max)         : Risk = MAX(C, I, A) * CVSS
        2. 總合法 (sum)           : Risk = (C + I + A) * CVSS
        3. 平均法 (avg)           : Risk = ((C + I + A) / 3) * CVSS
        4. 加權平均法 (weighted_average/weighted_avg): Risk = (W_C * C + W_I * I + W_A * A) * CVSS
        
        :param c: 機密性等級 (Confidentiality, 通常為 1-3 或 1-5 的整數) [1]
        :param i: 完整性等級 (Integrity, 通常為 1-3 或 1-5 的整數) [1]
        :param a: 可用性等級 (Availability, 通常為 1-3 或 1-5 的整數) [1]
        :param cvss_score: 弱點的 CVSS 分數 (通常為 0.0 ~ 10.0 的浮點數) [2]
        :param formula_type: 公式類型，支援 'max'、'sum'、'avg'、'weighted_average'、'weighted_avg'
        :param weights: 自訂權重比例字典，僅在加權平均法時使用。
                        例如：{'c': 0.4, 'i': 0.3, 'a': 0.3} (相加需為 1.0)
        :return: 計算出的風險分數（四捨五入至小數點後兩位）
        """
        # 防呆處理：如果沒有漏洞 CVSS 分數，預設為 0.0
        if cvss_score is None:
            cvss_score = 0.0
            
        try:
            c_val = int(c)
            i_val = int(i)
            a_val = int(a)
            cvss_val = float(cvss_score)
        except (ValueError, TypeError):
            # 若輸入轉換失敗，預設回傳 0.0，避免系統崩潰
            return 0.0

        formula = str(formula_type).strip().lower()

        # 1. 最大值法
        if formula == 'max':
            base_value = max(c_val, i_val, a_val)
            
        # 2. 總合法
        elif formula == 'sum':
            base_value = c_val + i_val + a_val
            
        # 3. 平均法
        elif formula == 'avg':
            base_value = (c_val + i_val + a_val) / 3.0
            
        # 4. 加權平均法
        elif formula in ('weighted_average', 'weighted_avg'):
            if not weights or not isinstance(weights, dict):
                # 若未提供權重，降級使用平均法，避免程式中斷
                base_value = (c_val + i_val + a_val) / 3.0
            else:
                # 取得 C, I, A 權重，若無則預設為等比 (1/3)
                w_c = float(weights.get('c', 1.0 / 3.0))
                w_i = float(weights.get('i', 1.0 / 3.0))
                w_a = float(weights.get('a', 1.0 / 3.0))
                
                # 計算加權基礎值
                base_value = (w_c * c_val) + (w_i * i_val) + (w_a * a_val)
                
        # 預設降級公式（最大值法）
        else:
            base_value = max(c_val, i_val, a_val)

        # 計算最終風險分數：基本分 * 弱點分數 [2]
        risk_score = base_value * cvss_val
        return round(risk_score, 2)

    @staticmethod
    def get_risk_level(risk_score: float) -> str:
        """
        根據計算出的風險分數，判定風險等級。
        
        等級對照標準：
        - >= 20.0        : 極高 (Critical)
        - >= 12.0 且 < 20 : 高 (High)
        - >= 6.0  且 < 12 : 中 (Medium)
        - < 6.0          : 低 (Low)
        """
        try:
            score = float(risk_score)
        except (ValueError, TypeError):
            return "低 (Low)"

        if score >= 20.0:
            return "極高 (Critical)"
        elif score >= 12.0:
            return "高 (High)"
        elif score >= 6.0:
            return "中 (Medium)"
        else:
            return "低 (Low)"
