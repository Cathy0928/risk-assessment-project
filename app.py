import streamlit as st
import pandas as pd
from ai_tagger import apply_ai_tagger
from risk_engine import calculate_risk
from engine.decision_support import get_rag_advice # 匯入 D 的功能

# 頁面配置
st.set_page_config(page_title="AI Risk System", layout="wide")

st.title("🛡️ AI 輔助資產風險評估系統")
st.caption("Excel 上傳 + AI CIA 評估 + 自動化 ISO 27001 建議方案")

uploaded_file = st.file_uploader("請上傳資產清冊 .xlsx 檔案", type=["xlsx"])

if uploaded_file is not None:
    try:
        # 讀取並執行 B、C 的邏輯
        df = pd.read_excel(uploaded_file)
        df = apply_ai_tagger(df)       # B: AI 標記與 CIA
        result_df = calculate_risk(df) # C: 風險計算

        # 顯示原始統計表格 (原功能)
        st.markdown("### 1) 資產風險評估數據表")
        st.dataframe(result_df, use_container_width=True)

        # ---------------------------------------------------
        # 新增：成員 D 的專業修復建議區塊
        # ---------------------------------------------------
        st.markdown("---")
        st.header("🤖 AI 資安顧問：建議修復方案")
        st.info("系統已自動匹配 ISO 27001:2022 標準，並產出白話建議與效益分析。")

        # 篩選高風險資產 (例如分數高於 60)
        high_risk_items = result_df[result_df["final_score"] >= 61]

        if not high_risk_items.empty:
            for index, row in high_risk_items.iterrows():
                # 建立摺疊視窗，讓介面整潔
                with st.expander(f"⚠️ 建議方案：{row['asset_name']} (風險等級: {row['risk_level']})"):
                    # 顯示動畫代表正在處理
                    with st.spinner("AI 顧問正在研讀法規條文並產出建議..."):
                        # 呼叫 D 部分邏輯：傳入 B 與 C 產出的數據
                        advice = get_rag_advice(
                            row['asset_name'],
                            row.get('Asset Category', '技術資產'), 
                            row.get('asset_description', ''),
                            row['risk_level']
                        )
                        st.markdown(advice)
        else:
            st.success("✅ 目前所有資產均在安全範圍內，暫無高風險警告。")

    except Exception as e:
        st.error(f"系統處理失敗：{e}")
else:
    st.info("請先上傳一份符合格式的 Excel 檔案以開始分析。")