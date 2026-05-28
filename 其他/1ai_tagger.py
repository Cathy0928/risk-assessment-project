import pandas as pd

def apply_ai_tagger(df):
    df = df.copy()

    # =========================
    # 欄位補齊
    # =========================
    for col in ["description", "asset_type", "asset_name"]:
        if col not in df.columns:
            df[col] = ""

    def tag(row):
        name = str(row.get("asset_name", "")).lower()
        desc = str(row.get("description", "")).lower()
        asset_type = str(row.get("asset_type", "")).lower()
        text = name + " " + desc

        # =========================
        # 規則分類
        # =========================
        if "customer" in text or "客戶" in text or "個資" in text:
            return 9, 9, 7

        elif "finance" in text or "財務" in text or "交易" in text:
            return 9, 8, 7

        elif "employee" in text or "員工" in text or "人事" in text:
            return 8, 8, 6

        elif "website" in text:
            return 6, 7, 9

        # asset_type fallback
        elif asset_type == "da":
            return 8, 8, 7
        elif asset_type == "sw":
            return 7, 7, 7
        elif asset_type == "hw":
            return 6, 6, 8

        # default
        return 6, 6, 6

    cia = df.apply(tag, axis=1)

    df["confidentiality"] = cia.apply(lambda x: int(x[0]))
    df["integrity"] = cia.apply(lambda x: int(x[1]))
    df["availability"] = cia.apply(lambda x: int(x[2]))

    return df