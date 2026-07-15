# (資料庫)先暫時用這個測試

# -*- coding: utf-8 -*-
"""
檔案名稱: models/mock_db.py
主要功能: 模擬 Supabase 雲端資料庫。在實際資料庫建立前，提供記憶體內的假資料與 CRUD 操作，
         以確保後端風險評鑑邏輯可以獨立開發與測試。
"""

# 1. 模擬 assets (資訊資產表) [1, 2]
MOCK_ASSETS = [
    {
        "id": 1,
        "asset_id_code": "AST-001",
        "asset_name": "核心資料庫伺服器",
        "asset_type": "HW",
        "description": "存放客戶機敏資料之實體伺服器",
        "confidentiality": 3,  # C 等級 (1~3)
        "integrity": 3,        # I 等級 (1~3)
        "availability": 3,     # A 等級 (1~3)
        "threat_level": "High",
        "company_id": 1,
        "department_id": 1
    },
    {
        "id": 2,
        "asset_id_code": "AST-002",
        "asset_name": "公文管理系統",
        "asset_type": "SW",
        "description": "一般公文傳遞與簽核系統",
        "confidentiality": 2,
        "integrity": 2,
        "availability": 3,
        "threat_level": "Medium",
        "company_id": 1,
        "department_id": 2
    },
    {
        "id": 3,
        "asset_id_code": "AST-003",
        "asset_name": "部門共用網路硬碟",
        "asset_type": "SW",
        "description": "部門內部分享一般資料用",
        "confidentiality": 1,
        "integrity": 1,
        "availability": 2,
        "threat_level": "Low",
        "company_id": 1,
        "department_id": 2
    }
]

# 2. 模擬 vulnerabilities (CVE 弱點/漏洞表) [2, 3]
MOCK_VULNERABILITIES = [
    {
        "id": 101,
        "cve_id": "CVE-2026-0001",
        "cvss_score": 9.8,       # CVSS 漏洞評分 (0.0 ~ 10.0)
        "severity": "CRITICAL",
        "description": "遠端代碼執行漏洞，允許攻擊者未授權控制伺服器。"
    },
    {
        "id": 102,
        "cve_id": "CVE-2026-0002",
        "cvss_score": 5.3,
        "severity": "MEDIUM",
        "description": "資訊洩漏弱點，可能導致敏感配置被讀取。"
    },
    {
        "id": 103,
        "cve_id": "CVE-2026-0003",
        "cvss_score": 7.5,
        "severity": "HIGH",
        "description": "阻斷服務漏洞(DoS)，可能導致系統無法存取。"
    }
]

# 3. 模擬 risk_assessments (風險評鑑結果表) [3, 4]
MOCK_RISK_ASSESSMENTS = []


# --- 資料操作 API (模擬 Repository 函式) ---

def get_all_assets():
    """模擬讀取所有資產"""
    return MOCK_ASSETS

def get_asset_by_id(asset_id):
    """模擬根據 ID 尋找特定資產"""
    return next((asset for asset in MOCK_ASSETS if asset["id"] == asset_id), None)

def get_vulnerability_by_id(vulnerability_id):
    """模擬根據 ID 尋找特定弱點"""
    return next((vul for vul in MOCK_VULNERABILITIES if vul["id"] == vulnerability_id), None)

def save_risk_assessment(assessment_data):
    """
    模擬儲存風險評估結果 [3]
    """
    new_id = len(MOCK_RISK_ASSESSMENTS) + 1
    assessment_data["id"] = new_id
    MOCK_RISK_ASSESSMENTS.append(assessment_data)
    return assessment_data

def get_all_risk_assessments():
    """取得所有已評估之結果 (用於產生風險報表) [3, 5]"""
    return MOCK_RISK_ASSESSMENTS