# -*- coding: utf-8 -*-
"""
Module: riskGenie.models.supabase_db
Description: 數據持久與存取層。
             封裝與實際 Supabase 關聯式資料庫之資料查詢、寫入及更新操作。
"""

from typing import List, Dict, Any, Optional
from riskGenie.services.supabase_client import get_supabase_client

def get_all_assets() -> List[Dict[str, Any]]:
    """
    自 assets 資料表檢索所有資訊資產紀錄。
    """
    supabase = get_supabase_client()
    response = supabase.table("assets").select("*").execute()
    return response.data if response.data else []

def get_all_vulnerabilities() -> List[Dict[str, Any]]:
    """
    自 vulnerabilities 資料表檢索所有漏洞紀錄。
    """
    supabase = get_supabase_client()
    response = supabase.table("vulnerabilities").select("*").execute()
    return response.data if response.data else []

def get_asset_by_id(asset_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    依據主鍵 ID 自 assets 資料表查詢特定資訊資產。
    """
    supabase = get_supabase_client()
    response = supabase.table("assets").select("*").eq("id", asset_id).execute()
    return response.data if response.data else None

def get_vulnerability_by_id(vulnerability_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    依據主鍵 ID 自 vulnerabilities 資料表查詢特定漏洞資訊。
    """
    supabase = get_supabase_client()
    response = supabase.table("vulnerabilities").select("*").eq("id", vulnerability_id).execute()
    return response.data if response.data else None

def save_risk_assessment(assessment_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    將風險評估結果儲存至 risk_assessments 資料表中。
    """
    supabase = get_supabase_client()
    response = supabase.table("risk_assessments").insert(assessment_data).execute()
    return response.data if response.data else []

def get_all_risk_assessments() -> List[Dict[str, Any]]:
    """
    自 risk_assessments 資料表取得所有歷史已評鑑之風險報告紀錄。
    """
    supabase = get_supabase_client()
    response = supabase.table("risk_assessments").select("*").execute()
    return response.data if response.data else []