# -*- coding: utf-8 -*-
"""
風險評鑑業務邏輯服務層，負責與 Supabase 資料庫進行資料讀寫互動。
     包含：權重設定的讀寫、資產與弱點資料讀取、風險評鑑結果儲存與歷史紀錄查詢。
"""

import json
import os
import logging

# 支援絕對與相對引入，確保單獨執行與專案執行皆正常
try:
    from .supabase_client import get_supabase_client
except ImportError:
    try:
        from services.supabase_client import get_supabase_client
    except ImportError:
        # 備用降級防護：若真的載入失敗，提供提示
        def get_supabase_client():
            raise RuntimeError("無法載入 supabase_client，請檢查檔案路徑。")

# 設定 Log 紀錄
logger = logging.getLogger(__name__)

class RiskService:
    @staticmethod
    def get_weight_settings(company_id: int) -> dict:
        """
        根據公司 ID 讀取權重與公式設定。
        若 Supabase 查詢失敗，會自動切換為讀取本地端 JSON 備用檔（或返回系統預設值），
        確保系統絕對不會因為網路異常或連線問題而崩潰。
        """
        default_settings = {
            "company_id": company_id,
            "formula_type": "max",
            "weight_c": 0.3333,
            "weight_i": 0.3333,
            "weight_a": 0.3333
        }

        if not company_id:
            return default_settings

        try:
            supabase = get_supabase_client()
            response = (
                supabase.table("weight_settings")
                .select("company_id, formula_type, weight_c, weight_i, weight_a")
                .eq("company_id", company_id)
                .maybe_single()
                .execute()
            )
            
            # 若有查到資料，直接回傳
            if response and getattr(response, "data", None):
                data = response.data
                # 確保數值型態正確
                return {
                    "company_id": int(data.get("company_id")),
                    "formula_type": str(data.get("formula_type", "max")),
                    "weight_c": float(data.get("weight_c", 0.3333)),
                    "weight_i": float(data.get("weight_i", 0.3333)),
                    "weight_a": float(data.get("weight_a", 0.3333))
                }
        except Exception as e:
            logger.warning(f"從 Supabase 讀取權重設定失敗 (公司 ID: {company_id}): {str(e)}。將嘗試備用方案。")

        # 【備用降級方案 A】：嘗試從本地 JSON 設定檔讀取
        fallback_path = os.path.join("data", "weight_settings.json")
        if os.path.exists(fallback_path):
            try:
                with open(fallback_path, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
                    # 尋找該公司設定
                    for item in local_data:
                        if int(item.get("company_id")) == int(company_id):
                            return {
                                "company_id": int(company_id),
                                "formula_type": str(item.get("formula_type", "max")),
                                "weight_c": float(item.get("weight_c", 0.3333)),
                                "weight_i": float(item.get("weight_i", 0.3333)),
                                "weight_a": float(item.get("weight_a", 0.3333))
                            }
            except Exception as le:
                logger.error(f"讀取本地備用設定檔失敗: {str(le)}")

        # 【備用降級方案 B】：返回預設最大值法
        return default_settings

    @staticmethod
    def save_weight_settings(company_id: int, formula_type: str, weight_c: float, weight_i: float, weight_a: float) -> bool:
        """
        儲存或更新公司的權重與公式設定（Upsert）。
        若 Supabase 儲存失敗，會自動同步寫入本地端 JSON 設定檔作為防禦性備份。
        """
        payload = {
            "company_id": int(company_id),
            "formula_type": str(formula_type).strip().lower(),
            "weight_c": float(weight_c),
            "weight_i": float(weight_i),
            "weight_a": float(weight_a)
        }

        db_success = False
        try:
            supabase = get_supabase_client()
            # 採用 Supabase Upsert 機制，當 company_id 重複時自動覆蓋更新
            response = (
                supabase.table("weight_settings")
                .upsert(payload, on_conflict="company_id")
                .execute()
            )
            if response and getattr(response, "data", None):
                db_success = True
        except Exception as e:
            logger.error(f"儲存權重設定到 Supabase 失敗: {str(e)}。將同步寫入本地備份。")

        # 同步寫入本地備份 JSON 檔做為保護機制
        try:
            os.makedirs("data", exist_ok=True)
            fallback_path = os.path.join("data", "weight_settings.json")
            local_list = []
            
            if os.path.exists(fallback_path):
                with open(fallback_path, "r", encoding="utf-8") as f:
                    try:
                        local_list = json.load(f)
                        if not isinstance(local_list, list):
                            local_list = []
                    except Exception:
                        local_list = []

            # 移除已存在該公司的舊設定
            local_list = [item for item in local_list if int(item.get("company_id", 0)) != int(company_id)]
            # 加入新設定
            local_list.append(payload)

            with open(fallback_path, "w", encoding="utf-8") as f:
                json.dump(local_list, f, ensure_ascii=False, indent=4)
                
            return True
        except Exception as le:
            logger.error(f"寫入本地備份檔失敗: {str(le)}")
            return db_success

    @staticmethod
    def get_asset_by_id(asset_id: int) -> dict:
        """
        根據資產 ID 讀取單筆資訊資產資料（包含 C, I, A 分數與公司 ID）。
        """
        try:
            supabase = get_supabase_client()
            response = (
                supabase.table("assets")
                .select("id, asset_id_code, asset_name, confidentiality, integrity, availability, company_id")
                .eq("id", asset_id)
                .single()
                .execute()
            )
            if response and getattr(response, "data", None):
                return response.data
        except Exception as e:
            logger.error(f"讀取資產資料失敗 (ID: {asset_id}): {str(e)}")
        return None

    @staticmethod
    def get_vulnerability_by_id(vulnerability_id: int) -> dict:
        """
        根據弱點 ID 讀取單筆弱點 CVE 資料（包含 CVSS 分數與描述）。
        """
        try:
            supabase = get_supabase_client()
            response = (
                supabase.table("vulnerabilities")
                .select("id, cve_id, cvss_score, description")
                .eq("id", vulnerability_id)
                .single()
                .execute()
            )
            if response and getattr(response, "data", None):
                return response.data
        except Exception as e:
            logger.error(f"讀取弱點資料失敗 (ID: {vulnerability_id}): {str(e)}")
        return None

    @staticmethod
    def save_risk_assessment(
        asset_id: int, 
        vulnerability_id: int, 
        vulnerability_description: str,
        risk_score: float, 
        status: str, 
        uploaded_by: str,
        threat_description: str = "由系統動態比對關聯漏洞進行評鑑"
    ) -> dict:
        """
        儲存風險評鑑計算結果至 risk_assessments 資料表。
        """
        payload = {
            "asset_id": int(asset_id),
            "vulnerability_id": int(vulnerability_id),
            "vulnerability_description": str(vulnerability_description),
            "risk_score": float(risk_score),
            "status": str(status),
            "uploaded_by": str(uploaded_by),
            "threat_description": str(threat_description)
        }
        try:
            supabase = get_supabase_client()
            response = (
                supabase.table("risk_assessments")
                .insert(payload)
                .execute()
            )
            if response and getattr(response, "data", None):
                data = response.data
                if isinstance(data, list) and len(data) > 0:
                    return data
                return data
        except Exception as e:
            logger.error(f"儲存風險評鑑結果失敗: {str(e)}")
        return None

    @staticmethod
    def get_historical_assessments(company_id: int) -> list:
        """
        查詢該公司專屬的所有歷史風險評鑑結果。
        利用 Supabase 聯表查詢 (SQL Inner Join) 合併 assets 與 vulnerabilities 表，
        撈出前端列表所需的中文資產名稱與 CVE 編號。
        """
        try:
            supabase = get_supabase_client()
            # 聯表查詢：篩選 assets 屬於當前公司的 risk_assessments
            response = (
                supabase.table("risk_assessments")
                .select(
                    "id, risk_score, status, created_at, threat_description, vulnerability_description, "
                    "assets!inner(id, asset_id_code, asset_name, company_id), "
                    "vulnerabilities(id, cve_id, cvss_score)"
                )
                .eq("assets.company_id", company_id)
                .order("created_at", desc=True)
                .execute()
            )
            
            if response and getattr(response, "data", None):
                raw_list = response.data
                formatted_list = []
                for item in raw_list:
                    asset_info = item.get("assets", {})
                    vuln_info = item.get("vulnerabilities", {}) or {}
                    
                    formatted_list.append({
                        "id": item.get("id"),
                        "risk_score": item.get("risk_score"),
                        "status": item.get("status"),
                        "created_at": item.get("created_at"),
                        "threat_description": item.get("threat_description"),
                        "vulnerability_description": item.get("vulnerability_description"),
                        "asset_id": asset_info.get("id"),
                        "asset_code": asset_info.get("asset_id_code", ""),
                        "asset_name": asset_info.get("asset_name", ""),
                        "vulnerability_id": vuln_info.get("id"),
                        "cve_id": vuln_info.get("cve_id", "未知 CVE"),
                        "cvss_score": vuln_info.get("cvss_score", 0.0)
                    })
                return formatted_list
        except Exception as e:
            logger.error(f"查詢歷史風險評鑑失敗 (公司 ID: {company_id}): {str(e)}")
            
        return []
