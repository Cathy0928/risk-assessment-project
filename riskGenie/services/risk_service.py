# 處理資訊安全風險評鑑的核心計算與分級邏輯，並產出基礎風險處置對策。

# -*- coding: utf-8 -*-
"""
檔案名稱: services/risk_service.py
主要功能: 處理資訊安全風險評鑑的核心計算與分級邏輯，並產出基礎風險處置對策。
"""
from models.mock_db import get_asset_by_id, get_vulnerability_by_id, save_risk_assessment

def calculate_risk_score(c, i, a, cvss_score):
    """
    核心風險公式實作: MAX(C, I, A) * CVSS [7]
    C, I, A 分別代表機密性、完整性、可用性 (1~3) [2, 9]
    CVSS_score 為漏洞之評分 (0.0~10.0) [3, 10]
    """
    max_cia = max(int(c), int(i), int(a))
    risk_score = max_cia * float(cvss_score)
    return round(risk_score, 2)

def determine_risk_level(risk_score):
    """
    根據風險分數劃分風險等級 [7]
    最大分數為 3 * 10.0 = 30.0 分。
    分級參考標準：
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

def perform_asset_risk_assessment(asset_id, vulnerability_id, user_id=None):
    """
    執行單一資產的風險評鑑流程 [7]
    1. 讀取資產資料與對應漏洞 [7]
    2. 計算風險分數與風險等級 [7]
    3. 自動生成初步的預防改善指引架構 (後續可串接 RAG 與 Gemini API) [10, 11]
    4. 儲存結果並回傳 [7]
    """
    asset = get_asset_by_id(asset_id)
    vul = get_vulnerability_by_id(vulnerability_id)
    
    if not asset:
        raise ValueError(f"找不到編號為 {asset_id} 的資產")
    if not vul:
        raise ValueError(f"找不到編號為 {vulnerability_id} 的弱點")
        
    # 進行風險計算
    risk_score = calculate_risk_score(
        asset["confidentiality"],
        asset["integrity"],
        asset["availability"],
        vul["cvss_score"]
    )
    
    risk_level = determine_risk_level(risk_score)
    
    # 初步的建議對策範本（後續會由 RAG 檢索 ISO 27002 後，交給 Gemini 生成更白話的內容 [8, 10, 11]）
    mock_ai_suggestion = (
        f"【系統自動建議】本資產最高防護價值(CIA)為 {max(asset['confidentiality'], asset['integrity'], asset['availability'])}，"
        f"面臨已知的 {vul['cve_id']} ({vul['severity']} 等級漏洞，CVSS: {vul['cvss_score']})。\n"
        f"建議改善對策：\n"
        f"1. 請優先修補或更新受影響系統至最新版本。\n"
        f"2. 針對網路層限制該資產的外部連線存取權限。\n"
        f"3. 確保已開啟稽核日誌紀錄，並落實定期資料備份。"
    )
    
    # 組合 risk_assessments 欄位 [3, 4]
    assessment_record = {
        "asset_id": asset_id,
        "vulnerability_id": vulnerability_id,
        "threat_description": f"資產面臨 {vul['cve_id']} 的威脅，可能導致系統遭攻擊者入侵控制。",
        "vulnerability_description": vul["description"],
        "risk_score": risk_score,
        "status": "Completed", # 已評估完成
        "ai_suggestion": mock_ai_suggestion,
        "uploaded_by": user_id or "11111111-1111-1111-1111-111111111111" # 假的使用者UUID
    }
    
    # 儲存到模擬資料庫 [3]
    saved_record = save_risk_assessment(assessment_record)
    
    # 補足回傳給前端需要的易讀欄位
    saved_record["asset_name"] = asset["asset_name"]
    saved_record["cve_id"] = vul["cve_id"]
    saved_record["risk_level"] = risk_level
    
    return saved_record