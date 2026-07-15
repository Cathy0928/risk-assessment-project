# (資料庫)先暫時用這個測試

# -*- coding: utf-8 -*-
"""
檔案路徑: models/mock_db.py
主要功能: 模擬 Supabase 雲端資料庫。定義符合系統手冊規範的表格結構與測試假資料，
         讓後端風險計算服務可以完全獨立進行開發。
"""

# 1. 模擬 assets (資訊資產表) - 欄位對齊系統手冊表 8-2-5 [4, 5]
MOCK_ASSETS = [
    {
        "id": 1,
        "asset_id_code": "AST-001",
        "asset_name": "核心資料庫伺服器",
        "asset_type": "SW",          # 軟體
        "confidentiality": 3,         # 機密性 (1~3)
        "integrity": 3,               # 完整性 (1~3)
        "availability": 3,            # 可用性 (1~3)
        "threat_level": "High"
    },
    {
        "id": 2,
        "asset_id_code": "AST-002",
        "asset_name": "公文管理系統",
        "asset_type": "SW",
        "confidentiality": 2,
        "integrity": 2,
        "availability": 3,
        "threat_level": "Medium"
    },
    {
        "id": 3,
        "asset_id_code": "AST-003",
        "asset_name": "部門共用網路硬碟",
        "asset_type": "DA",          # 資料
        "confidentiality": 1,
        "integrity": 1,
        "availability": 2,
        "threat_level": "Low"
    }
]

# 2. 模擬 vulnerabilities (CVE 弱點/漏洞表) - 欄位對齊系統手冊表 8-2-6 [4, 6]
MOCK_VULNERABILITIES = [
    {
        "id": 101,
        "cve_id": "CVE-2026-0001",
        "cvss_score": 9.8,            # CVSS 漏洞評分 (0.0 ~ 10.0)
        "severity": "CRITICAL",
        "description": "遠端代碼執行漏洞，允許未授權的外部攻擊者取得伺服器控制權。"
    },
    {
        "id": 102,
        "cve_id": "CVE-2026-0002",
        "cvss_score": 5.3,
        "severity": "MEDIUM",
        "description": "資訊外洩漏洞，可能導致敏感系統配置被外部惡意讀取。"
    },
    {
        "id": 103,
        "cve_id": "CVE-2026-0003",
        "cvss_score": 7.5,
        "severity": "HIGH",
        "description": "拒絕服務漏洞(DoS)，可能導致目標系統癱瘓，無法正常存取。"
    }
]

# 3. 模擬 risk_assessments (AI 風險評估結果表) - 欄位對齊系統手冊表 8-2-7 [4, 7]
MOCK_RISK_ASSESSMENTS = []


# --- 模擬資料庫操作 APIs (Repository functions) ---

def get_all_assets():
    """讀取所有資產"""
    return MOCK_ASSETS

def get_asset_by_id(asset_id):
    """根據 ID 查詢特定資產"""
    return next((asset for asset in MOCK_ASSETS if asset["id"] == asset_id), None)

def get_vulnerability_by_id(vulnerability_id):
    """根據 ID 查詢特定漏洞"""
    return next((vul for vul in MOCK_VULNERABILITIES if vul["id"] == vulnerability_id), None)

def save_risk_assessment(assessment_data):
    """模擬寫入風險評估結果"""
    new_id = len(MOCK_RISK_ASSESSMENTS) + 1
    assessment_data["id"] = new_id
    MOCK_RISK_ASSESSMENTS.append(assessment_data)
    return assessment_data

def get_all_risk_assessments():
    """取得所有歷史評估報告"""
    return MOCK_RISK_ASSESSMENTS