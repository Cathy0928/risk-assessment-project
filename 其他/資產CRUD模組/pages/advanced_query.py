import streamlit as st
import pandas as pd
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from services.asset_service import get_assets, delete_asset

st.set_page_config(page_title="資產進階查詢", layout="wide")
st.title("🔍 資產進階查詢 / 管理中心")

# =========================
# sidebar
# =========================
st.sidebar.subheader("⚙ 篩選條件")

if "cia_max" not in st.session_state:
    st.session_state.cia_max = 10

cia_max = st.session_state.cia_max

assets = get_assets()

if not assets:
    st.warning("目前沒有資產資料")
    st.stop()

# =========================
# 🧼 安全處理 None（重點修正）
# =========================
def safe_list(field):
    return sorted(
        set(str(a.get(field)) for a in assets if a.get(field) not in [None, ""])
    )

asset_types = safe_list("asset_type")
owners = safe_list("owner")
data_types = safe_list("data_type")

# =========================
# 篩選 UI
# =========================
filter_type = st.sidebar.selectbox("資料型態", ["全部"] + asset_types)
filter_owner = st.sidebar.selectbox("擁有者", ["全部"] + owners)
filter_data = st.sidebar.selectbox("資料類型", ["全部"] + data_types)

sort_mode = st.sidebar.radio(
    "排序",
    ["最新→最舊", "最舊→最新", "資產代碼"]
)

# =========================
# 過濾資料
# =========================
filtered = assets

if filter_type != "全部":
    filtered = [a for a in filtered if a.get("asset_type") == filter_type]

if filter_owner != "全部":
    filtered = [a for a in filtered if a.get("owner") == filter_owner]

if filter_data != "全部":
    filtered = [a for a in filtered if a.get("data_type") == filter_data]

# =========================
# 排序（安全版）
# =========================
if sort_mode == "最新→最舊":
    filtered = sorted(filtered, key=lambda x: x.get("created_at") or "", reverse=True)

elif sort_mode == "最舊→最新":
    filtered = sorted(filtered, key=lambda x: x.get("created_at") or "")

else:
    filtered = sorted(filtered, key=lambda x: x.get("asset_id_code") or "")

# =========================
# 顯示表格
# =========================
df = pd.DataFrame([
    {
        "資產代碼": a.get("asset_id_code", ""),
        "名稱": a.get("asset_name", ""),
        "型態": a.get("asset_type", ""),
        "類型": a.get("data_type", ""),
        "擁有者": a.get("owner", ""),
        "CIA": f"{a.get('confidentiality',0)}/{a.get('integrity',0)}/{a.get('availability',0)}",
        "建立時間": a.get("created_at", "")
    }
    for a in filtered
])

st.dataframe(df, use_container_width=True, hide_index=True)

st.divider()

# =========================
# 操作區
# =========================
st.subheader("🛠 資產操作")

options = {
    f"{a.get('asset_id_code','')} - {a.get('asset_name','')}": a.get("asset_id_code")
    for a in filtered
    if a.get("asset_id_code")
}

selected = st.selectbox("選擇資產", list(options.keys()) if options else ["無資料"])

if selected == "無資料":
    st.stop()

selected_code = options[selected]

col1, col2 = st.columns(2)

with col1:
    if st.button("✏ 修改"):
        st.session_state.edit_asset_id = selected_code
        st.switch_page("pages/edit_asset.py")

with col2:
    if st.button("🗑 刪除"):
        delete_asset(selected_code)
        st.success("已刪除")
        st.rerun()