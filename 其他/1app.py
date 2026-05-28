from __future__ import annotations

import pandas as pd
import streamlit as st

from ai_tagger import apply_ai_tagger
from risk_engine import calculate_risk
from utils.db import insert_assets, insert_risk_results
from engine.decision_support import get_rag_advice


# =========================
# UI
# =========================
st.set_page_config(page_title="AI Risk System", layout="wide")

st.title("🛡️ AI 資產風險評估系統")
st.caption("Excel → AI CIA → Risk Engine → RAG → Supabase")

uploaded_file = st.file_uploader("📂 上傳 Excel", type=["xlsx"])


# =========================
# MAIN
# =========================
if uploaded_file:

    # -------------------------
    # 1️⃣ 讀 Excel
    # -------------------------
    df = pd.read_excel(uploaded_file)
    df.columns = df.columns.str.strip()

    if "asset_id" in df.columns:
        df["asset_id_code"] = df["asset_id"]

    df = df.fillna({
        "original_label": "C",
        "threat_level": "Medium",
        "confidentiality": 5,
        "integrity": 5,
        "availability": 5
    })

    st.subheader("📦 原始資料")
    st.dataframe(df, use_container_width=True)

    # -------------------------
    # 2️⃣ AI CIA Tagger
    # -------------------------
    df_ai = apply_ai_tagger(df)

    st.subheader("🤖 AI CIA 分析")
    st.dataframe(df_ai, use_container_width=True)

    # -------------------------
    # 3️⃣ Risk Engine
    # -------------------------
    result = calculate_risk(df_ai)

    st.subheader("📊 風險分析結果")
    st.dataframe(result, use_container_width=True)

    # -------------------------
    # 4️⃣ 存 Assets
    # -------------------------
    insert_assets(df_ai)

    # -------------------------
    # 5️⃣ 存 Risk DB
    # -------------------------
    db_risk = pd.DataFrame({
        "asset_id_code": result["asset_id_code"],
        "risk_score": result["final_score"],
        "threat_description": "AI generated threat",
        "vulnerability_description": "AI generated vulnerability",
        "ai_suggestion": result["risk_level"].apply(
            lambda x: "立即修補" if x == "Critical" else "持續監控"
        ),
        "status": result["risk_level"]
    })

    db_risk = db_risk.dropna(subset=["asset_id_code", "risk_score"])

    if db_risk.empty:
        st.error("❌ DB資料為空")
        st.stop()

    insert_risk_results(db_risk)

    st.success("✅ Supabase 寫入成功")


    # =========================
    # 📊 DASHBOARD
    # =========================
    st.markdown("---")

    st.subheader("📊 CIA 分析")
    st.bar_chart(result[["confidentiality", "integrity", "availability"]])

    st.subheader("🚨 Top 10 高風險")
    st.dataframe(
        result.sort_values("final_score", ascending=False)
        .head(10)
        .reset_index(drop=True),
        use_container_width=True
    )


    # =========================
    # 🧠 RAG（ISO 27001 AI 顧問）
    # =========================
    st.markdown("---")
    st.header("🧠 AI 資安決策建議（RAG）")

    for _, row in result.head(3).iterrows():

        rag = get_rag_advice(
            asset_name=str(row.get("asset_name", "unknown")),
            asset_type=str(row.get("asset_type", "unknown")),
            asset_description="由AI風險模型分析產生",
            risk_level=row["risk_level"]
        )

        if rag["status"] == "success":

            st.markdown(f"""
### 📌 {row['asset_id_code']} ({row['risk_level']})

🔹 ISO 條款：{rag['control_id']}
📘 條文：{rag['title']}

🤖 AI 建議：
{rag['advice']}
""")

        else:
            st.warning(rag.get("message", rag.get("advice", "AI失敗")))


    # =========================
    # 📄 REPORT
    # =========================
    st.subheader("📄 AI 風險報告")

    report = f"""
資產總數：{len(result)}
平均風險：{result['final_score'].mean():.2f}

Low：{len(result[result['risk_level']=='Low'])}
Medium：{len(result[result['risk_level']=='Medium'])}
High：{len(result[result['risk_level']=='High'])}
Critical：{len(result[result['risk_level']=='Critical'])}
"""

    st.text_area("報告內容", report, height=200)