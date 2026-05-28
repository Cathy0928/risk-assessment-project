import os
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")


def get_supabase() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("Supabase key missing")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================
# assets table
# =========================
def insert_assets(df: pd.DataFrame):
    supabase = get_supabase()

    data = df.copy()

    allowed_cols = [
        "asset_id_code",
        "asset_name",
        "asset_type",
        "description",
        "data_type",
        "owner",
        "system_dependency",
        "confidentiality",
        "integrity",
        "availability",
        "threat_level",
    ]

    data = data.reindex(columns=allowed_cols)

    # 🚨 防止空資料
    data = data.dropna(subset=["asset_id_code"])

    records = data.to_dict(orient="records")

    if not records:
        raise ValueError("❌ assets insert empty payload")

    return supabase.table("assets").upsert(
        records,
        on_conflict="asset_id_code"
    ).execute()


# =========================
# risk_assessments table
# =========================
def insert_risk_results(df: pd.DataFrame):
    supabase = get_supabase()

    data = df.copy()

    allowed_cols = [
        "asset_id_code",   # 🔥 統一欄位（避免 asset_id mismatch）
        "risk_score",
        "threat_description",
        "vulnerability_description",
        "ai_suggestion",
        "status"
    ]

    data = data.reindex(columns=allowed_cols)

    # 🚨 防 NaN / 空值
    data = data.dropna(subset=["asset_id_code", "risk_score"])

    records = data.to_dict(orient="records")

    # 🚨 防止 Supabase columns ()
    if not records:
        raise ValueError("❌ risk_assessments insert empty payload")

    return supabase.table("risk_assessments").insert(
        records
    ).execute()