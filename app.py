from __future__ import annotations

import pandas as pd
import streamlit as st

from risk_engine import calculate_risk


st.set_page_config(page_title="AI Risk System", layout="wide")

st.title("AI 輔助資產風險評估系統")
st.caption("Phase 1 MVP：Excel 上傳 + 規則引擎風險計算")

st.markdown("### 1) 上傳資產清冊 Excel")
uploaded_file = st.file_uploader("請上傳 .xlsx 檔案", type=["xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_excel(uploaded_file)

        st.markdown("### 2) 原始資產資料")
        st.dataframe(df, use_container_width=True)

        result_df = calculate_risk(df)

        st.markdown("### 3) 風險評估結果")
        st.dataframe(result_df, use_container_width=True)

        st.markdown("### 4) 風險統計")
        col1, col2, col3 = st.columns(3)
        col1.metric("總資產數", len(result_df))
        col2.metric("平均風險分數", round(result_df["final_score"].mean(), 2))
        col3.metric(
            "Critical 資產數",
            int((result_df["risk_level"] == "Critical").sum())
        )

        st.markdown("### 5) 高風險資產")
        high_risk_df = result_df[result_df["final_score"] >= 61].sort_values(
            by="final_score", ascending=False
        )

        if high_risk_df.empty:
            st.success("目前沒有 High / Critical 資產")
        else:
            st.dataframe(high_risk_df, use_container_width=True)

    except Exception as e:
        st.error(f"處理失敗：{e}")
else:
    st.info("先上傳一份符合格式的 Excel 檔案")