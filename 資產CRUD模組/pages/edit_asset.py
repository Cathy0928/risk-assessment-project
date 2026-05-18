import streamlit as st
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from services.asset_service import get_assets, update_asset

st.set_page_config(page_title="修改資產", layout="wide")
st.title("✏ 修改資產")

# =========================
# CIA 範圍
# =========================
if "cia_max" not in st.session_state:
    st.session_state.cia_max = 10

cia_max = st.session_state.cia_max
st.sidebar.info(f"CIA 最大值：{cia_max}")

# =========================
# 讀取資料
# =========================
assets = get_assets()

if not assets:
    st.warning("目前沒有資產資料")
    st.stop()

# =========================
# 接收 from advanced_query
# =========================
default_id = st.session_state.get("edit_asset_id")

# =========================
# 找預設 index（安全版）
# =========================
default_index = 0
for i, a in enumerate(assets):
    if a.get("asset_id_code") == default_id:
        default_index = i
        break

# =========================
# label list
# =========================
asset_labels = [
    f"{a.get('asset_id_code','')} - {a.get('asset_name','')}"
    for a in assets
]

# =========================
# 選擇資產
# =========================
selected_index = st.selectbox(
    "選擇要修改的資產",
    range(len(asset_labels)),
    index=default_index,
    format_func=lambda i: asset_labels[i]
)

current_asset = assets[selected_index]

# =========================
# session 清理（避免干擾）
# =========================
st.session_state.edit_asset_id = current_asset.get("asset_id_code")

st.divider()
st.subheader("📝 編輯資產資料")

# =========================
# asset type safe
# =========================
asset_type_list = ["DA", "DC", "CM", "SW", "EV", "PE", "SM"]

current_type = current_asset.get("asset_type") or "DA"
if current_type not in asset_type_list:
    current_type = "DA"

asset_type_index = asset_type_list.index(current_type)

# =========================
# 表單
# =========================
with st.form("edit_form"):

    asset_name = st.text_input("資產名稱", value=current_asset.get("asset_name", ""))

    asset_type = st.selectbox(
        "資料型態",
        asset_type_list,
        index=asset_type_index
    )

    description = st.text_area("描述", value=current_asset.get("description", ""))

    data_type = st.text_input("資料類型", value=current_asset.get("data_type", ""))

    owner = st.text_input("擁有者", value=current_asset.get("owner", ""))

    system_dependency = st.text_input(
        "系統依賴",
        value=current_asset.get("system_dependency", "")
    )

    confidentiality = st.number_input(
        "C (機密性)",
        min_value=1,
        max_value=cia_max,
        value=current_asset.get("confidentiality", 1)
    )

    integrity = st.number_input(
        "I (完整性)",
        min_value=1,
        max_value=cia_max,
        value=current_asset.get("integrity", 1)
    )

    availability = st.number_input(
        "A (可用性)",
        min_value=1,
        max_value=cia_max,
        value=current_asset.get("availability", 1)
    )

    original_label = st.text_input(
        "原始人工風險等級",
        value=current_asset.get("original_label", "")
    )

    submitted = st.form_submit_button("💾 更新資產")

    # =========================
    # 更新邏輯
    # =========================
    if submitted:

        update_data = {
            "asset_name": asset_name,
            "asset_type": asset_type,
            "description": description,
            "data_type": data_type,
            "owner": owner,
            "system_dependency": system_dependency,
            "confidentiality": confidentiality,
            "integrity": integrity,
            "availability": availability,
            "original_label": original_label
        }

        success, msg, data = update_asset(
            current_asset["asset_id_code"],
            update_data
        )

        if success:
            st.success(f"✅ {msg}")

            # 🔥 清掉 session 避免下一次亂跳
            if "edit_asset_id" in st.session_state:
                del st.session_state.edit_asset_id

            st.info("🔄 已更新，請回查詢頁確認")

            st.stop()

        else:
            st.error(f"❌ {msg}")
            if data:
                st.json(data)