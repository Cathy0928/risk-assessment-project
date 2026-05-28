def apply_ai_tagger(df):
    df = df.copy()

    if "description" not in df.columns:
        df["description"] = ""

    def tag(row):
        name = str(row.get("asset_name", "")).lower()
        desc = str(row.get("description", "")).lower()

        text = name + " " + desc

        if "customer" in text:
            return 9, 8, 7
        elif "website" in text:
            return 6, 7, 9
        else:
            return 5, 5, 5

    cia = df.apply(tag, axis=1)

    df["confidentiality"] = cia.apply(lambda x: x[0])
    df["integrity"] = cia.apply(lambda x: x[1])
    df["availability"] = cia.apply(lambda x: x[2])

    return df