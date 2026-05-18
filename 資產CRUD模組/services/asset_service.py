from config.supabase_client import supabase


# =========================
# ➕ 新增
# =========================
def add_asset(asset: dict):
    try:
        res = supabase.table("assets").insert(asset).execute()

        if res.data:
            return True, "新增成功", res.data

        return False, "新增失敗（未知原因）", None

    except Exception as e:
        error_msg = str(e)

        if "duplicate key" in error_msg or "already exists" in error_msg:
            return False, "資產代碼已存在", None

        return False, f"新增失敗：{error_msg}", None


# =========================
# 📖 查詢
# =========================
def get_assets():
    try:
        res = (
            supabase.table("assets")
            .select("*")
            .order("asset_id_code", desc=False)
            .execute()
        )
        return res.data if res.data else []

    except Exception as e:
        print("Select Error:", e)
        return []


# =========================
# ✏ 更新（⭐你缺的就是這個）
# =========================
def update_asset(asset_id_code: str, update_data: dict):
    try:
        res = (
            supabase.table("assets")
            .update(update_data)
            .eq("asset_id_code", asset_id_code)
            .execute()
        )

        if res.data:
            return True, "更新成功", res.data

        return False, "更新失敗（找不到資料）", None

    except Exception as e:
        return False, f"更新失敗：{str(e)}", None


# =========================
# 🗑 刪除
# =========================
def delete_asset(asset_id_code: str):
    try:
        res = (
            supabase.table("assets")
            .delete()
            .eq("asset_id_code", asset_id_code)
            .execute()
        )

        if res.data:
            return True, "刪除成功"

        return False, "刪除失敗（找不到資料）"

    except Exception as e:
        return False, f"刪除失敗：{str(e)}"