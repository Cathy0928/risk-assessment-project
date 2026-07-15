# 處理資訊安全風險評鑑的核心計算與分級邏輯，並產出基礎風險處置對策。

# -*- coding: utf-8 -*-
"""
Module: services.risk_service
Description: 實作資訊安全風險評鑑核心運算邏輯，並對接數據持久層與 AI 分析模組。
"""

from typing import Dict, Any, Optional
from models.mock_db import get_asset_by_id, get_vulnerability_by_id, save_risk_assessment


def calculate_risk_score(c: int, i: int, a: int, cvss_score: float) -> float:
    """
    計算風險分數。
    公式: MAX(C, I, A) * CVSS_Score
    """
    max_cia = max(int(c), int(i), int(a))
    risk_score = max_cia * float(cvss_score)
    return round(risk_score, 2)


def determine_risk_level(risk_score: float) -> str:
    """
    依據風險評估分數判定安全風險等級。
    分類標準（最高 30.0 分）：
    - >= 25.0: Critical (極高風險)
    - >= 18.0: High (高風險)
    - >= 10.0: Medium (中風險)
    - < 10.0: Low (低風險)
    """
    if risk_score >= 25.0:
        return "Critical"
    elif risk_score >= 18.0:
        return "High"
    elif risk_score >= 10.0:
        return "Medium"
    else:
        return "Low"


def sanitize_sensitive_information(text: str) -> str:
    """
    去識別化防護層 (De-identification Layer)。
    在將資產描述送往外部 API 前進行去敏感化處理，保障組織隱私。
    """
    # 遮蔽關鍵字或敏感命名
    return text.replace("核心資料庫", "DATASERVER-PRD").replace("公文管理", "DOC-SYSTEM")


def generate_ai_mitigation_plan(asset_name: str, cve_id: str, cvss_score: float, severity: str) -> str:
    """
    整合 ISO 27002 知識庫之控制措施建議生成介面 (RAG 預留插槽)。
    """
    safe_name = sanitize_sensitive_information(asset_name)
    
    # 預設本地靜態對策範本（當 API 未連線時之降級防禦措施）
    fallback_recommendation = (
        f"【ISO 27002:2022 控制措施指引】\n"
        f"受評資產 [{safe_name}] 目前面臨已公佈之弱點 {cve_id} (CVSS: {cvss_score} - {severity})。\n"
        f"建議配置以下控制措施：\n"
        f"1. 漏洞管理 (Control 5.7 / 8.19)：請於測試環境驗證後，立即安排部署對應之安全補丁。\n"
        f"2. 網段隔離 (Control 8.20 / 8.22)：應將此主機移至獨立非軍事區 (DMZ)，限制外網直連存取。\n"
        f"3. 監視日誌 (Control 8.16)：確認稽核日誌 (Audit Logs) 正確啟用，並將日誌定期異地儲存。"
    )
    return fallback_recommendation


def perform_asset_risk_assessment(asset_id: int, vulnerability_id: int, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    執行單一資訊資產之風險評鑑、寫入持久化資料庫並回傳完整 JSON 資料。
    """
    asset = get_asset_by_id(asset_id)
    vul = get_vulnerability_by_id(vulnerability_id)
    
    if not asset:
        raise ValueError(f"指定之資產識別碼不存在: {asset_id}")
    if not vul:
        raise ValueError(f"指定之漏洞識別碼不存在: {vulnerability_id}")
        
    # 核心公式計算與分級
    risk_score = calculate_risk_score(
        asset["confidentiality"],
        asset["integrity"],
        asset["availability"],
        vul["cvss_score"]
    )
    risk_level = determine_risk_level(risk_score)
    
    # 生成對應之控制措施建議
    ai_suggestion = generate_ai_mitigation_plan(
        asset["asset_name"],
        vul["cve_id"],
        vul["cvss_score"],
        vul["severity"]
    )
    
    # 建置寫入 risk_assessments 資料表之結構，對齊系統手冊 8-2-7 欄位定義 [5]
    assessment_record = {
        "asset_id": asset_id,
        "vulnerability_id": vulnerability_id,
        "threat_description": f"資產面臨 {vul['cve_id']} 漏洞威脅，可能導致系統機密性或完整性受損。",
        "vulnerability_description": vul["description"],
        "risk_score": risk_score,
        "status": "Completed",
        "ai_suggestion": ai_suggestion,
        "uploaded_by": user_id or "00000000-0000-0000-0000-000000000000"
    }
    
    saved_record = save_risk_assessment(assessment_record)
    
    # 補足前端渲染渲染所需之關聯屬性
    saved_record["asset_name"] = asset["asset_name"]
    saved_record["cve_id"] = vul["cve_id"]
    saved_record["risk_level"] = risk_level
    
    return saved_record