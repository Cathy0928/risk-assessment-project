"""
Threat Feed
-----------
模擬外部威脅情資,可手動觸發事件以動態調整 threat_level。
這是主題二的核心 demo 點:讓評審看到「新威脅 → 分數上升」。

設計理念:
- 不串真實 NVD/CVE API(專題範圍內成本太高、不穩定),
  改用「情境包」(scenario pack)模擬,但介面預留成可擴充。
- 每個威脅事件指定 affects_asset_type 或 affects_keyword,
  命中的資產 threat_level 會被升級。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import pandas as pd


# 威脅等級升級表(原等級 → 升級後)
_ESCALATION = {
    "low": "medium",
    "medium": "high",
    "high": "critical",
    "critical": "critical",
    "低": "中",
    "中": "高",
    "高": "極高",
    "極高": "極高",
}


@dataclass
class ThreatEvent:
    cve_id: str               # e.g. "CVE-2024-XXXXX" 或 "INC-2025-001"
    title: str                # 簡短標題
    description: str          # 詳細說明
    severity: str             # "low" / "medium" / "high" / "critical"
    affects_keywords: List[str] = field(default_factory=list)  # 命中資產 name/desc 關鍵字
    affects_asset_types: List[str] = field(default_factory=list)
    iso_hint: str = ""        # 對應 ISO 27002 控制提示


# =========================
# 預設情境包(Demo 用)
# =========================

DEFAULT_THREAT_LIBRARY: List[ThreatEvent] = [
    ThreatEvent(
        cve_id="CVE-2024-DEMO-SQLI",
        title="SQL Injection 0-day 在主流資料庫被披露",
        description="近期揭露之 SQL Injection 漏洞影響多款資料庫,攻擊者可取得敏感資料。",
        severity="critical",
        affects_keywords=["資料庫", "database", "db", "客戶", "customer"],
        affects_asset_types=["DA", "資料庫"],
        iso_hint="A.8.28 安全編碼 / A.8.11 資料遮蔽",
    ),
    ThreatEvent(
        cve_id="INC-2025-RANSOM",
        title="勒索軟體攻擊鎖定企業備份系統",
        description="勒索軟體變種已能滲透並加密備份,破壞企業復原能力。",
        severity="high",
        affects_keywords=["備份", "backup", "server", "主機", "雲端", "cloud"],
        affects_asset_types=["HW", "硬體"],
        iso_hint="A.8.13 資訊備份 / A.8.7 防護惡意軟體",
    ),
    ThreatEvent(
        cve_id="CVE-2024-DEMO-PHISH",
        title="針對員工郵件系統的釣魚攻擊增加",
        description="近期針對企業郵件帳號的釣魚攻擊明顯上升,已造成多起資料外洩。",
        severity="medium",
        affects_keywords=["郵件", "email", "mail"],
        affects_asset_types=["DA", "SW"],
        iso_hint="A.6.3 資安意識訓練 / A.5.17 認證資訊",
    ),
    ThreatEvent(
        cve_id="INC-2025-CLOUD",
        title="雲端服務 API 金鑰外洩事件頻發",
        description="多起雲端設定錯誤導致 API 金鑰外洩,攻擊者可越權存取雲端資源。",
        severity="high",
        affects_keywords=["雲端", "cloud", "api"],
        affects_asset_types=["SW"],
        iso_hint="A.5.23 雲端服務資安 / A.5.15 存取控制",
    ),
]


# =========================
# Public API
# =========================

def list_threats() -> List[ThreatEvent]:
    """列出可觸發的威脅事件(給 UI 選擇)。"""
    return DEFAULT_THREAT_LIBRARY


def _hits(row: pd.Series, event: ThreatEvent) -> bool:
    text = f"{row.get('asset_name','')} {row.get('description','')}".lower()
    if any(kw.lower() in text for kw in event.affects_keywords):
        return True
    asset_type = str(row.get("asset_type", "")).strip()
    if any(t.lower() == asset_type.lower() for t in event.affects_asset_types):
        return True
    return False


def apply_threat_event(df: pd.DataFrame, event: ThreatEvent) -> pd.DataFrame:
    """套用威脅事件,將命中的資產 threat_level 升級,並記錄 triggered_by。"""
    df = df.copy()
    if "threat_level_original" not in df.columns:
        df["threat_level_original"] = df["threat_level"]
    if "triggered_by" not in df.columns:
        df["triggered_by"] = ""

    for idx, row in df.iterrows():
        if _hits(row, event):
            current = str(row.get("threat_level", "")).strip().lower()
            new = _ESCALATION.get(current, current)
            # 若 lookup 是中文也要能命中
            if new == current:
                new = _ESCALATION.get(current, current)
            df.at[idx, "threat_level"] = new
            existing = df.at[idx, "triggered_by"]
            df.at[idx, "triggered_by"] = (
                f"{existing}; {event.cve_id}".lstrip("; ") if existing else event.cve_id
            )
    return df


def reset_threat_levels(df: pd.DataFrame) -> pd.DataFrame:
    """還原成 Excel 上傳時的原始 threat_level。"""
    df = df.copy()
    if "threat_level_original" in df.columns:
        df["threat_level"] = df["threat_level_original"]
    if "triggered_by" in df.columns:
        df["triggered_by"] = ""
    return df
