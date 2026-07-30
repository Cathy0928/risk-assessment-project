# -*- coding: utf-8 -*-
"""
風險評鑑業務邏輯服務層，負責與 Supabase 資料庫進行資料讀寫互動。
包含：權重設定的讀寫、資產與弱點資料讀取、風險評鑑結果儲存與歷史紀錄查詢。
"""
import json
import os
import logging
from datetime import datetime

### 支援絕對與相對引入，確保單獨執行與專案執行皆正常
try:
    from .supabase_client import get_supabase_client
except ImportError:
    try:
        from services.supabase_client import get_supabase_client
    except ImportError:
        def get_supabase_client():
            raise RuntimeError("無法載入 supabase_client，請檢查檔案路徑。")

logger = logging.getLogger(__name__)

# 本地降級備份檔路徑
FALLBACK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FALLBACK_FILE = os.path.join(FALLBACK_DIR, "weight_settings_fallback.json")


class RiskService:
    @staticmethod
    def get_weight_settings(company_id: int) -> dict:
        """
        根據公司 ID 讀取權重與公式設定。
        """
        default_settings = {
            "company_id": company_id,
            "formula_type": "max",
            "weight_c": 0.3333,
            "weight_i": 0.3333,
            "weight_a": 0.3333
        }

        try:
            supabase = get_supabase_client()
            response = supabase.table("weight_settings").select("*").eq("company_id", company_id).execute()
            
            if response.data and len(response.data) > 0:
                data = response.data[0]
                return {
                    "company_id": int(data.get("company_id", company_id)),
                    "formula_type": str(data.get("formula_type", "max")),
                    "weight_c": float(data.get("weight_c", 0.3333)),
                    "weight_i": float(data.get("weight_i", 0.3333)),
                    "weight_a": float(data.get("weight_a", 0.3333))
                }
            else:
                # 主動建立一筆預設值
                try:
                    insert_data = {
                        "company_id": company_id,
                        "formula_type": "max",
                        "weight_c": 0.3333,
                        "weight_i": 0.3333,
                        "weight_a": 0.3333,
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    supabase.table("weight_settings").insert(insert_data).execute()
                except Exception as ins_err:
                    logger.warning(f"無法在資料庫建立預設權重設定: {ins_err}")
                return default_settings

        except Exception as e:
            logger.error(f"Supabase 查詢 weight_settings 失敗，啟用降級機制: {e}")
            
            if os.path.exists(FALLBACK_FILE):
                try:
                    with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
                        fallback_data = json.load(f)
                        company_key = str(company_id)
                        if company_key in fallback_data:
                            return fallback_data[company_key]
                except Exception as json_err:
                    logger.error(f"讀取本地權重備份檔失敗: {json_err}")
            
            return default_settings

    @staticmethod
    def save_weight_settings(company_id: int, formula_type: str, weight_c: float, weight_i: float, weight_a: float) -> dict:
        """
        儲存或更新給定公司 ID 的權重公式設定（使用 Supabase Upsert 確保寫入成功）。
        """
        weight_c = float(weight_c)
        weight_i = float(weight_i)
        weight_a = float(weight_a)

        settings_dict = {
            "company_id": company_id,
            "formula_type": formula_type,
            "weight_c": weight_c,
            "weight_i": weight_i,
            "weight_a": weight_a
        }

        supabase_success = False
        
        # 1. 寫入雲端 Supabase 資料庫
        try:
            supabase = get_supabase_client()
            now_str = datetime.utcnow().isoformat()
            
            # 查詢該公司是否已有紀錄
            check_res = supabase.table("weight_settings").select("id").eq("company_id", company_id).execute()
            
            if check_res.data and len(check_res.data) > 0:
                record_id = check_res.data[0]["id"]
                # UPDATE 既有紀錄
                update_res = supabase.table("weight_settings").update({
                    "formula_type": formula_type,
                    "weight_c": weight_c,
                    "weight_i": weight_i,
                    "weight_a": weight_a,
                    "updated_at": now_str
                }).eq("id", record_id).execute()
                print("Supabase Update 結果:", update_res)
            else:
                # INSERT 新紀錄
                insert_res = supabase.table("weight_settings").insert({
                    "company_id": company_id,
                    "formula_type": formula_type,
                    "weight_c": weight_c,
                    "weight_i": weight_i,
                    "weight_a": weight_a,
                    "created_at": now_str,
                    "updated_at": now_str
                }).execute()
                print("Supabase Insert 結果:", insert_res)
                
            supabase_success = True
            logger.info(f"公司 {company_id} 權重設定已成功儲存至 Supabase。")
        except Exception as e:
            print(f"❌ 寫入 Supabase 失敗詳情: {e}")
            logger.error(f"無法儲存權重設定到 Supabase: {e}")

        # 2. 同步寫入本地 JSON 備份檔
        try:
            os.makedirs(os.path.dirname(FALLBACK_FILE), exist_ok=True)
            fallback_data = {}
            if os.path.exists(FALLBACK_FILE):
                try:
                    with open(FALLBACK_FILE, "r", encoding="utf-8") as f:
                        fallback_data = json.load(f)
                except Exception:
                    fallback_data = {}
            
            fallback_data[str(company_id)] = settings_dict
            with open(FALLBACK_FILE, "w", encoding="utf-8") as f:
                json.dump(fallback_data, f, ensure_ascii=False, indent=4)
        except Exception as json_err:
            logger.error(f"無法將權重設定寫入本地備份檔: {json_err}")

        return {
            "success": True,
            "supabase_synced": supabase_success,
            "settings": settings_dict
        }