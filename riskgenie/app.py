"""
RiskGenie - 結合情資之 AI 資安風險精靈
======================================
Demo 流程:
  1) 上傳資產清冊
  2) AI 建議 CIA(主題一)→ 與人工值比對
  3) Risk Engine 計算風險(主題二基底)
  4) 觸發威脅事件 → 動態調整(主題二亮點)
  5) AI Advisor 提供 ISO 27002 建議(主題三)
"""

import sys
from pathlib import Path

# 讓 modules 可以被 import
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import streamlit as st

from modules.ai_tagger import apply_ai_tagger
from modules.risk_engine import calculate_risk
from modules.threat_feed import list_threats, apply_threat_event, reset_threat_levels
from modules.advisor import get_advice


# =========================
# Page Config
# =========================
st.set_page_config(
    page_title="RiskGenie - AI 資安風險精靈",
    page_icon="🧞",
    layout="wide",
)

st.title("🧞 RiskGenie")
st.caption("結合情資之 AI 資安風險精靈 — 支援 ISMS 運作的智能顧問")


# =========================
# Session State
# =========================
if "df_raw" not in st.session_state:
    st.session_state.df_raw = None
if "df_tagged" not in st.session_state:
    st.session_state.df_tagged = None
if "df_scored" not in st.session_state:
    st.session_state.df_scored = None


# =========================
# Sidebar
# =========================
with st.sidebar:
    st.header("⚙️ 設定")
    use_llm = st.checkbox("使用 LLM 進行 AI Tagging", value=True,
                          help="未設定 GEMINI_API_KEY 時會自動 fallback 到規則式")
    show_delta = st.checkbox("顯示人工 vs AI CIA 差異", value=True)
    threshold = st.slider("觸發 AI 顧問的風險分數門檻", 30, 95, 60)


# =========================
# Step 1: 上傳
# =========================
st.markdown("## ① 上傳資產清冊")
uploaded = st.file_uploader("上傳 .xlsx 檔案", type=["xlsx"])

if uploaded is not None:
    df = pd.read_excel(uploaded)
    df.columns = df.columns.str.strip()
    st.session_state.df_raw = df
    st.success(f"已載入 {len(df)} 筆資產")
    with st.expander("📦 原始資料", expanded=False):
        st.dataframe(df, use_container_width=True)


# =========================
# Step 2: AI Tagger
# =========================
if st.session_state.df_raw is not None:
    st.markdown("## ② AI 建議資產類型 & CIA")
    col_a, col_b = st.columns([1, 4])
    with col_a:
        if st.button("🤖 執行 AI Tagger", type="primary"):
            with st.spinner("AI 分析中..."):
                tagged = apply_ai_tagger(st.session_state.df_raw, prefer_llm=use_llm)
                st.session_state.df_tagged = tagged
                # 建議套用後也重新計分
                st.session_state.df_scored = None

    if st.session_state.df_tagged is not None:
        tagged = st.session_state.df_tagged
        source_summary = tagged["ai_source"].value_counts().to_dict()
        st.info(f"AI 來源統計:{source_summary}(llm = LLM 產出,rule = 規則 fallback)")

        display_cols = [
            "asset_id", "asset_name",
            "confidentiality", "integrity", "availability",
            "ai_c", "ai_i", "ai_a", "ai_asset_type", "ai_reason",
        ]
        if show_delta and "cia_delta" in tagged.columns:
            display_cols.append("cia_delta")

        existing = [c for c in display_cols if c in tagged.columns]
        st.dataframe(tagged[existing], use_container_width=True)

        if show_delta and "cia_delta" in tagged.columns:
            high_delta = tagged[tagged["cia_delta"] >= 5].sort_values("cia_delta", ascending=False)
            if len(high_delta) > 0:
                st.warning(f"⚠️ 有 {len(high_delta)} 筆資產的人工值與 AI 建議差距較大,建議複核")
                st.dataframe(
                    high_delta[["asset_name", "confidentiality", "integrity", "availability",
                                "ai_c", "ai_i", "ai_a", "cia_delta", "ai_reason"]],
                    use_container_width=True,
                )


# =========================
# Step 3: Risk Engine
# =========================
if st.session_state.df_tagged is not None:
    st.markdown("## ③ 風險分數計算")
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("📊 計算風險分數"):
            scored = calculate_risk(st.session_state.df_tagged)
            st.session_state.df_scored = scored

    if st.session_state.df_scored is not None:
        scored = st.session_state.df_scored
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Critical", (scored["risk_level"] == "Critical").sum())
        m2.metric("High", (scored["risk_level"] == "High").sum())
        m3.metric("Medium", (scored["risk_level"] == "Medium").sum())
        m4.metric("Low", (scored["risk_level"] == "Low").sum())

        st.dataframe(
            scored[["asset_id", "asset_name", "original_label", "threat_level",
                    "impact_score", "final_score", "risk_level"]]
            .sort_values("final_score", ascending=False),
            use_container_width=True,
        )


# =========================
# Step 4: Threat Feed
# =========================
if st.session_state.df_scored is not None:
    st.markdown("## ④ 威脅情資觸發(Demo 亮點)")
    threats = list_threats()
    options = {f"{t.cve_id} — {t.title} [{t.severity}]": t for t in threats}

    col_t1, col_t2 = st.columns([3, 1])
    with col_t1:
        chosen = st.selectbox("選擇要模擬的威脅事件", list(options.keys()))
    with col_t2:
        st.write("")
        st.write("")
        if st.button("🚨 觸發事件"):
            event = options[chosen]
            df_after = apply_threat_event(st.session_state.df_tagged, event)
            scored_after = calculate_risk(df_after)
            st.session_state.df_scored = scored_after
            st.session_state.df_tagged = df_after
            st.success(f"已觸發 {event.cve_id}:{event.title}")

    if st.button("🔄 還原威脅等級"):
        df_reset = reset_threat_levels(st.session_state.df_tagged)
        st.session_state.df_tagged = df_reset
        st.session_state.df_scored = calculate_risk(df_reset)
        st.info("已還原至原始威脅等級")

    # 顯示有被觸發影響的資產
    if "triggered_by" in st.session_state.df_scored.columns:
        affected = st.session_state.df_scored[
            st.session_state.df_scored["triggered_by"].astype(str).str.len() > 0
        ]
        if len(affected) > 0:
            st.markdown("**受影響資產:**")
            st.dataframe(
                affected[["asset_id", "asset_name", "threat_level_original",
                          "threat_level", "final_score", "risk_level", "triggered_by"]],
                use_container_width=True,
            )


# =========================
# Step 5: AI Advisor
# =========================
if st.session_state.df_scored is not None:
    st.markdown("## ⑤ AI 顧問建議(主題三)")
    scored = st.session_state.df_scored
    high_risk = scored[scored["final_score"] >= threshold].sort_values(
        "final_score", ascending=False
    )

    if len(high_risk) == 0:
        st.success(f"✅ 目前沒有風險分數 ≥ {threshold} 的資產")
    else:
        st.write(f"以下 {len(high_risk)} 項資產達到顧問門檻:")

        for _, row in high_risk.iterrows():
            with st.expander(
                f"{row['asset_name']} — {row['risk_level']} ({row['final_score']:.1f})"
            ):
                with st.spinner("檢索 ISO 27002 並生成建議..."):
                    result = get_advice(
                        asset_name=str(row["asset_name"]),
                        asset_type=str(row.get("ai_asset_type", row.get("asset_type", ""))),
                        risk_level=str(row["risk_level"]),
                        score=float(row["final_score"]),
                        triggered_by=str(row.get("triggered_by", "")),
                    )
                st.caption(
                    f"檢索方式:{result['retrieval']} | "
                    f"建議生成:{result['method']}"
                )
                st.markdown("**📚 參考 ISO 27002 控制措施:**")
                for c in result["controls"]:
                    st.markdown(
                        f"- **A.{c.get('id','')} {c.get('title','')}** "
                        f"({c.get('category','')}) — {c.get('content','')}"
                    )
                st.markdown("---")
                st.markdown("**🤖 顧問建議:**")
                st.markdown(result["advice"])


# =========================
# Footer
# =========================
st.markdown("---")
st.caption("RiskGenie · 結合 LLM、規則引擎與 RAG 的 ISMS 風險顧問 · 學期專題")
