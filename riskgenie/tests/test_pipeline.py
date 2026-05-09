"""
基本回歸測試
跑法:cd 到專案根目錄,執行 `pytest tests/` 或 `python tests/test_pipeline.py`
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from modules.ai_tagger import apply_ai_tagger
from modules.risk_engine import calculate_risk, classify_risk
from modules.threat_feed import list_threats, apply_threat_event, reset_threat_levels


SAMPLE = pd.DataFrame([
    {
        "asset_id": "A001", "asset_name": "客戶資料庫", "asset_type": "DA",
        "description": "儲存客戶個資與交易紀錄", "data_type": "個資",
        "owner": "資訊部", "original_label": "高",
        "system_dependency": "CRM",
        "confidentiality": 9, "integrity": 9, "availability": 6,
        "threat_level": "high",
    },
    {
        "asset_id": "A002", "asset_name": "公司官網", "asset_type": "SW",
        "description": "對外宣傳網站", "data_type": "公開",
        "owner": "行銷部", "original_label": "低",
        "system_dependency": "IDC",
        "confidentiality": 3, "integrity": 6, "availability": 9,
        "threat_level": "low",
    },
    {
        "asset_id": "A003", "asset_name": "備份伺服器", "asset_type": "HW",
        "description": "全公司備份系統", "data_type": "備份",
        "owner": "資訊部", "original_label": "中",
        "system_dependency": "Backup",
        "confidentiality": 7, "integrity": 9, "availability": 7,
        "threat_level": "medium",
    },
])


def test_classify_risk():
    assert classify_risk(10) == "Low"
    assert classify_risk(45) == "Medium"
    assert classify_risk(75) == "High"
    assert classify_risk(95) == "Critical"


def test_risk_engine_basic():
    df = calculate_risk(SAMPLE)
    assert "impact_score" in df.columns
    assert "final_score" in df.columns
    assert "risk_level" in df.columns
    # 客戶資料庫 (9,9,6 + 高 + high) 應比官網 (3,6,9 + 低 + low) 分數高
    customer = df[df["asset_id"] == "A001"]["final_score"].iloc[0]
    website = df[df["asset_id"] == "A002"]["final_score"].iloc[0]
    assert customer > website, f"客戶資料庫應比官網高分,實際:{customer} vs {website}"
    # 分數應該被 clamp 在 0-100
    assert (df["final_score"] >= 0).all()
    assert (df["final_score"] <= 100).all()
    print(f"✓ Risk engine: customer={customer}, website={website}")


def test_label_normalization():
    """確認中文「高/中/低」label 跟 threat_level 大小寫都能正確 map"""
    df = calculate_risk(SAMPLE)
    # 全部分數應該 > 50 (因為都有有效的 multiplier 命中)
    customer_impact = df[df["asset_id"] == "A001"]["impact_score"].iloc[0]
    customer_final = df[df["asset_id"] == "A001"]["final_score"].iloc[0]
    # final 應該大於 impact (因為 high label * high threat 會放大)
    assert customer_final > customer_impact, \
        f"label/threat multiplier 沒生效:final={customer_final}, impact={customer_impact}"
    print(f"✓ Label normalization: impact={customer_impact} → final={customer_final}")


def test_ai_tagger_no_overwrite():
    """關鍵測試:AI Tagger 不能覆蓋人工值"""
    df = apply_ai_tagger(SAMPLE, prefer_llm=False)
    # 原本的 confidentiality 應保持不變
    assert df.loc[0, "confidentiality"] == 9
    assert df.loc[1, "confidentiality"] == 3
    # AI 建議值應該存在
    assert "ai_c" in df.columns
    assert "ai_reason" in df.columns
    assert "cia_delta" in df.columns
    print(f"✓ AI tagger 不覆蓋人工值,delta 平均 = {df['cia_delta'].mean():.2f}")


def test_threat_event_increases_risk():
    """關鍵測試:觸發威脅後分數應上升"""
    tagged = apply_ai_tagger(SAMPLE, prefer_llm=False)
    before = calculate_risk(tagged)
    customer_before = before[before["asset_id"] == "A001"]["final_score"].iloc[0]

    # 觸發 SQL Injection 事件,應命中客戶資料庫
    threats = list_threats()
    sqli_event = [t for t in threats if "SQLI" in t.cve_id][0]
    tagged_after = apply_threat_event(tagged, sqli_event)
    after = calculate_risk(tagged_after)
    customer_after = after[after["asset_id"] == "A001"]["final_score"].iloc[0]

    assert customer_after >= customer_before, \
        f"觸發威脅後分數應 ≥ 原分數:before={customer_before}, after={customer_after}"
    print(f"✓ Threat event: 客戶資料庫 {customer_before} → {customer_after}")


def test_reset_threat():
    """還原機制"""
    tagged = apply_ai_tagger(SAMPLE, prefer_llm=False)
    threats = list_threats()
    tagged_after = apply_threat_event(tagged, threats[0])
    tagged_reset = reset_threat_levels(tagged_after)
    # threat_level 應與原始相同
    assert (tagged_reset["threat_level"] == SAMPLE["threat_level"].values).all()
    print("✓ Threat level reset 正常")


if __name__ == "__main__":
    test_classify_risk()
    print("✓ classify_risk OK")
    test_risk_engine_basic()
    test_label_normalization()
    test_ai_tagger_no_overwrite()
    test_threat_event_increases_risk()
    test_reset_threat()
    print("\n🎉 全部測試通過")
