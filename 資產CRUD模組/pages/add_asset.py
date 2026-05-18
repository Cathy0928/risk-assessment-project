import streamlit as st
import pandas as pd
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from services.asset_service import add_asset

st.set_page_config(page_title="新增資產", layout="wide")
st.title("➕ 新增資產")

# ======================================
# sidebar 永久顯示（公司設定 + 功能）
# ======================================
st.sidebar.subheader("⚙ 公司設定")

if "cia_max" not in st.session_state:
    st.session_state.cia_max = 10

st.session_state.cia_max = st.sidebar.number_input(
    "CIA 最大值（例如 10 / 100）",
    min_value=1,
    value=st.session_state.cia_max
)

cia_max = st.session_state.cia_max

st.sidebar.divider()
st.sidebar.subheader("📌 功能")

if st.sidebar.button("➕ 新增資產", use_container_width=True):
    st.switch_page("pages/add_asset.py")

if st.sidebar.button("🛠 查詢資產(修改/刪除)", use_container_width=True):
    st.switch_page("pages/asset_list.py")


# ======================================
# 初始化狀態
# ======================================
DATA_TYPE_OPTIONS = ["客戶資料", "員工資料", "財務資料", "系統設定", "其他"]

if "last_asset" not in st.session_state:
    st.session_state.last_asset = None

if "form_version" not in st.session_state:
    st.session_state.form_version = 0

if "duplicate_asset" not in st.session_state:
    st.session_state.duplicate_asset = None


form_key = f"asset_form_{st.session_state.form_version}"


# ======================================
# 新增資產表單
# ======================================
st.subheader("📌 填寫資產資料")

with st.form(form_key, clear_on_submit=False):

    asset_id_code = st.text_input("資產代碼 *", key="asset_id_code")
    asset_name = st.text_input("資產名稱 *", key="asset_name")

    asset_type = st.selectbox(
        "資料型態 *",
        ["DA", "DC", "CM", "SW", "EV", "PE", "SM"],
        key="asset_type"
    )

    description = st.text_area("描述 *", key="description")

    data_type = st.selectbox(
        "資料類型 *",
        DATA_TYPE_OPTIONS,
        key="data_type"
    )

    owner = st.text_input("擁有者 *", key="owner")
    system_dependency = st.text_input("系統依賴 *", key="system_dependency")

    confidentiality = st.number_input(
        "C (機密性) *",
        min_value=1,
        max_value=cia_max,
        value=1,
        step=1,
        key="confidentiality"
    )

    integrity = st.number_input(
        "I (完整性) *",
        min_value=1,
        max_value=cia_max,
        value=1,
        step=1,
        key="integrity"
    )

    availability = st.number_input(
        "A (可用性) *",
        min_value=1,
        max_value=cia_max,
        value=1,
        step=1,
        key="availability"
    )

    original_label = st.text_input("原始人工風險等級 *", key="original_label")

    submitted = st.form_submit_button("📤 送出")

    if submitted:

        # =========================
        # 必填驗證
        # =========================
        if any(v.strip() == "" for v in [
            asset_id_code,
            asset_name,
            description,
            owner,
            system_dependency,
            original_label
        ]):
            st.error("❌ 所有欄位皆為必填（不可空白）")
            st.stop()

        asset = {
            "asset_id_code": asset_id_code,
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

        # ======================================
        # ⭐ 正確接 service 回傳（重點）
        # ======================================
        success, msg, data = add_asset(asset)

        if success:
            st.success(f"✅ {msg}")

            st.session_state.last_asset = asset
            st.session_state.duplicate_asset = None

            st.session_state.form_version += 1
            st.rerun()

        else:
            st.error(f"❌ {msg}")

            # ⭐ 如果是重複資料，存起來顯示
            if data:
                st.session_state.duplicate_asset = data

            st.stop()


# ======================================
# 顯示剛新增
# ======================================
if st.session_state.last_asset:
    st.subheader("🆕 剛剛新增")

    a = st.session_state.last_asset

    df_last = pd.DataFrame([{
        "資產代碼": a["asset_id_code"],
        "名稱": a["asset_name"],
        "資料型態": a["asset_type"],
        "描述": a["description"],
        "資料類型": a["data_type"],
        "擁有者": a["owner"],
        "系統依賴": a["system_dependency"],
        "C/I/A": f'{a["confidentiality"]}/{a["integrity"]}/{a["availability"]}',
        "原始人工風險等級": a["original_label"]
    }])

    st.dataframe(df_last, use_container_width=True, hide_index=True)


# ======================================
# ⭐ 顯示「已存在的資料」（重點新增）
# ======================================
if st.session_state.duplicate_asset:
    st.subheader("⚠️ 已存在的資產資料")

    dup = st.session_state.duplicate_asset

    st.dataframe(pd.DataFrame([dup]), use_container_width=True, hide_index=True)