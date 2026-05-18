import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import datetime
from config.supabase_client import supabase

# =========================
# 🎯 CIA 分數範圍（可調）
# =========================
MIN_SCORE = 1
MAX_SCORE = 100


# =========================
# 📥 Excel 範本下載（8欄）
# =========================
def download_template():

    df = pd.DataFrame(columns=[
        "asset_id_code",
        "asset_name",
        "asset_type",
        "description",
        "owner",
        "confidentiality",
        "integrity",
        "availability"
    ])

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="asset_template")

    st.download_button(
        label="📥 下載Excel資產範本",
        data=buffer.getvalue(),
        file_name="asset_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# =========================
# 📤 Excel 上傳 + 匯入
# =========================
def upload_excel():

    file = st.file_uploader("📤 上傳 Excel 檔案", type=["xlsx"])

    if file is None:
        st.info("請先上傳 Excel 檔案")
        return

    try:
        df = pd.read_excel(file)
    except Exception as e:
        st.error(f"❌ Excel 讀取失敗：{e}")
        return

    st.subheader("📄 資料預覽")
    st.dataframe(df)

    # =========================
    # 必填欄位檢查
    # =========================
    required_cols = [
        "asset_id_code",
        "asset_name",
        "asset_type",
        "description",
        "owner",
        "confidentiality",
        "integrity",
        "availability"
    ]

    missing = [col for col in required_cols if col not in df.columns]

    if missing:
        st.error(f"❌ 欄位缺少：{missing}")
        st.stop()

    # =========================
    # 🚀 匯入按鈕
    # =========================
    if st.button("🚀 匯入資料庫"):

        df = df.fillna("")

        # =========================
        # 🔍 CIA 驗證（重點）
        # =========================
        int_cols = ["confidentiality", "integrity", "availability"]

        for col in int_cols:

            df[col] = pd.to_numeric(df[col], errors="coerce")

            # ❌ 非數字
            if df[col].isnull().any():
                st.error(f"❌ {col} 有非數字或空值")
                st.stop()

            # ❌ 超出範圍
            if (df[col] < MIN_SCORE).any() or (df[col] > MAX_SCORE).any():
                st.error(
                    f"❌ {col} 超出範圍（{MIN_SCORE} ~ {MAX_SCORE}）"
                )
                st.stop()

            # ✔ 轉整數
            df[col] = df[col].astype(int)

        # =========================
        # 🧠 系統自動補欄位
        # =========================
        df["threat_level"] = "low"
        df["original_label"] = "excel_import"
        df["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        data = df.to_dict(orient="records")

        # =========================
        # 📤 上傳 Supabase
        # =========================
        try:
            supabase.table("assets") \
                .upsert(data, on_conflict="asset_id_code") \
                .execute()

            st.success("✅ 匯入成功（已完成CIA驗證 + 系統補齊）")

        except Exception as e:
            st.error(f"❌ 匯入失敗：{e}")


# =========================
# 📦 主畫面
# =========================
def main():

    st.title("📦 Excel 匯入 / 範本下載（企業版）")

    tab1, tab2 = st.tabs(["📥 範本下載", "📤 Excel 匯入"])

    with tab1:
        st.subheader("下載標準資產範本（8欄）")
        download_template()

    with tab2:
        st.subheader("上傳 Excel 並匯入資產")
        upload_excel()


# =========================
# 🚀 Streamlit entry point
# =========================
main()