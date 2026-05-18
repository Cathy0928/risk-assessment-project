import streamlit as st
import pandas as pd
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from services.asset_service import get_assets

st.set_page_config(page_title="資產盤點", layout="wide")
st.title("📊 ISMS 資產盤點中心")

# =========================
# 讀取資料
# =========================
assets = get_assets()

if not assets:
    st.warning("目前沒有資產資料")
    st.stop()

df = pd.DataFrame(assets)

# =========================
# 基本統計
# =========================
st.subheader("📌 資產概況")

col1, col2, col3 = st.columns(3)

col1.metric("資產總數", len(df))
col2.metric("資產類型數", df["asset_type"].nunique())
col3.metric("負責人數量", df["owner"].nunique())

st.divider()

# =========================
# 欄位完整性檢查（純資料）
# =========================
st.subheader("⚠ 資料完整性檢查")

check_fields = ["owner", "description", "system_dependency"]

def check_missing(row):
    missing = []
    for f in check_fields:
        if not row.get(f):
            missing.append(f)
    return ", ".join(missing)

df["缺漏欄位"] = df.apply(check_missing, axis=1)

missing_df = df[df["缺漏欄位"] != ""]

if not missing_df.empty:
    st.error(f"⚠ 有 {len(missing_df)} 筆資料不完整")

    st.dataframe(
        missing_df[["asset_id_code", "asset_name", "缺漏欄位"]],
        use_container_width=True,
        hide_index=True
    )
else:
    st.success("✅ 所有資產資料完整")

st.divider()

# =========================
# 建立時間排序（純展示）
# =========================
st.subheader("📋 資產清單（依建立時間）")

if "created_at" in df.columns:
    df = df.sort_values("created_at", ascending=False)

show_cols = [
    "asset_id_code",
    "asset_name",
    "asset_type",
    "owner",
    "description",
    "system_dependency",
    "created_at"
]

show_cols = [c for c in show_cols if c in df.columns]

st.dataframe(df[show_cols], use_container_width=True, hide_index=True)