# -*- coding: utf-8 -*-
"""
Module: riskGenie.models.supabase_db
Description: 數據持久與存取層。
             封裝與實際 Supabase 關聯式資料庫之資料查詢、寫入及更新操作。
"""

from typing import List, Dict, Any, Optional
from riskGenie.services.supabase_client import get_supabase_client


class InvalidCompanyContextError(ValueError):
    """Raised when a database operation lacks a valid tenant company id."""


class InvalidRiskAssessmentError(ValueError):
    """Raised when risk assessment input is invalid."""


class RiskAssessmentAssetNotFoundError(LookupError):
    """Raised without disclosing whether an asset exists in another tenant."""


def validate_company_id(company_id: int) -> int:
    """Return a valid positive integer company id or reject the operation."""
    if (
        not isinstance(company_id, int)
        or isinstance(company_id, bool)
        or company_id <= 0
    ):
        raise InvalidCompanyContextError(
            "company_id must be a positive integer."
        )
    return company_id


def _validate_positive_integer(value: Any, field_name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
    ):
        raise InvalidRiskAssessmentError(
            f"{field_name} must be a positive integer."
        )
    return value


def _response_data(response: Any) -> Any:
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("data", response)
    return getattr(response, "data", response)


def _ensure_asset_in_company(supabase: Any, asset_id: int, company_id: int) -> None:
    response = (
        supabase
        .table("assets")
        .select("id")
        .eq("id", asset_id)
        .eq("company_id", company_id)
        .eq("is_deleted", False)
        .execute()
    )
    if not _response_data(response):
        raise RiskAssessmentAssetNotFoundError("Asset not found.")

def get_all_assets(company_id: int) -> List[Dict[str, Any]]:
    """
    自 assets 資料表檢索指定公司的資訊資產紀錄。
    """
    supabase = get_supabase_client()
    response = (
        supabase
        .table("assets")
        .select("*")
        .eq("company_id", company_id)
        .execute()
    )
    return response.data if response.data else []

def get_all_vulnerabilities() -> List[Dict[str, Any]]:
    """
    自 vulnerabilities 資料表檢索所有漏洞紀錄。
    """
    supabase = get_supabase_client()
    response = supabase.table("vulnerabilities").select("*").execute()
    return response.data if response.data else []

def get_asset_by_id(asset_id: int, company_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    依據主鍵 ID 與公司 ID 自 assets 資料表查詢特定資訊資產。
    """
    supabase = get_supabase_client()
    response = (
        supabase
        .table("assets")
        .select("*")
        .eq("id", asset_id)
        .eq("company_id", company_id)
        .execute()
    )
    return response.data if response.data else None

def get_vulnerability_by_id(vulnerability_id: int) -> Optional[List[Dict[str, Any]]]:
    """
    依據主鍵 ID 自 vulnerabilities 資料表查詢特定漏洞資訊。
    """
    supabase = get_supabase_client()
    response = supabase.table("vulnerabilities").select("*").eq("id", vulnerability_id).execute()
    return response.data if response.data else None

def save_risk_assessment(
    assessment_data: Dict[str, Any],
    company_id: int,
) -> List[Dict[str, Any]]:
    """
    將風險評估結果儲存至指定公司的 risk_assessments 資料表中。
    """
    company_id = validate_company_id(company_id)
    if not isinstance(assessment_data, dict):
        raise InvalidRiskAssessmentError("assessment_data must be a dict.")

    payload = assessment_data.copy()
    payload.pop("id", None)
    payload["company_id"] = company_id

    if "asset_id" not in payload:
        raise InvalidRiskAssessmentError("asset_id is required.")
    asset_id = _validate_positive_integer(payload["asset_id"], "asset_id")

    supabase = get_supabase_client()
    _ensure_asset_in_company(supabase, asset_id, company_id)

    response = supabase.table("risk_assessments").insert(payload).execute()
    data = _response_data(response)
    return data if data else []


def get_all_risk_assessments(company_id: int) -> List[Dict[str, Any]]:
    """
    取得指定公司的歷史風險評鑑紀錄。
    """
    company_id = validate_company_id(company_id)
    supabase = get_supabase_client()
    response = (
        supabase
        .table("risk_assessments")
        .select("*")
        .eq("company_id", company_id)
        .execute()
    )
    data = _response_data(response)
    return data if data else []


def update_risk_assessment(
    assessment_id: int,
    assessment_data: Dict[str, Any],
    company_id: int,
) -> List[Dict[str, Any]]:
    """Update one assessment within the server-provided tenant scope."""
    company_id = validate_company_id(company_id)
    assessment_id = _validate_positive_integer(assessment_id, "assessment_id")
    if not isinstance(assessment_data, dict):
        raise InvalidRiskAssessmentError("assessment_data must be a dict.")

    payload = assessment_data.copy()
    payload.pop("id", None)
    payload["company_id"] = company_id

    supabase = get_supabase_client()
    if "asset_id" in payload:
        asset_id = _validate_positive_integer(payload["asset_id"], "asset_id")
        _ensure_asset_in_company(supabase, asset_id, company_id)

    response = (
        supabase
        .table("risk_assessments")
        .update(payload)
        .eq("id", assessment_id)
        .eq("company_id", company_id)
        .execute()
    )
    data = _response_data(response)
    return data if data else []


def delete_risk_assessment(
    assessment_id: int,
    company_id: int,
) -> List[Dict[str, Any]]:
    """Delete one assessment within the server-provided tenant scope."""
    company_id = validate_company_id(company_id)
    assessment_id = _validate_positive_integer(assessment_id, "assessment_id")

    supabase = get_supabase_client()
    response = (
        supabase
        .table("risk_assessments")
        .delete()
        .eq("id", assessment_id)
        .eq("company_id", company_id)
        .execute()
    )
    data = _response_data(response)
    return data if data else []
