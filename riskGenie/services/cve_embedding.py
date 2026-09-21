import os
import time

from dotenv import load_dotenv
from supabase import create_client
from google import genai

try:
    from .cve_change_detector import compute_content_hash
except ImportError:
    from cve_change_detector import compute_content_hash


# ============================================================
# 設定
# ============================================================

PAGE_SIZE = 1000

BATCH_SIZE = 50

MAX_RETRY = 3

SLEEP_TIME = 0.2

EMBEDDING_DIMENSION = 768

# Confirmed production table name via pg_class (OID 34266). A prior
# screenshot-based guess said singular "cve_embedding" — that table does
# not exist. The real, existing search_cve RPC already reads
# "FROM cve_embeddings" (plural), matching this.
EMBEDDING_TABLE = "cve_embeddings"


# ============================================================
# 主流程
#
# 包成 main()，而不是在 import 時就直接執行，
# 這樣才能在測試中安全地 import 這個檔案裡的其他函式
# （例如 compute_content_hash），而不會意外建立真正的
# Supabase / Gemini client 或打真正的網路請求。
#
# 只依 cve_id 判斷「是否已經 embedding 過」的舊邏輯，
# 沒辦法分辨「內容有更新，需要重新 embedding」跟
# 「內容沒變，可以跳過」。這裡改成比對 content_hash：
#
#   - cve_embeddings 沒有這筆 cve_id → 視為新增，要 embedding
#   - 有這筆，但 content_hash 不同    → 視為內容更新，要重新 embedding
#   - 有這筆，且 content_hash 相同    → 跳過
#
# 失敗處理維持原本的作法：只有 Gemini embedding 成功、
# 且 Supabase upsert 成功後，才會更新記憶體中的
# existing_hash_by_id；任何一步失敗，原本已存在的
# cve_embeddings 資料列完全不會被刪除或覆寫，
# 下次重新執行時會因為 hash 仍然不同而再次嘗試。
# ============================================================

def main(supabase=None, client=None, cve_ids=None, max_embeddings=None):

    load_dotenv()

    if supabase is None:
        supabase = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_ANON_KEY")
        )

    if client is None:
        client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY")
        )

    target_cve_ids = set(cve_ids) if cve_ids is not None else None
    gemini_call_count = 0
    limited_count = 0

    # ========================================================
    # 取得 CVE Documents 總數
    # ========================================================

    count_response = (
        supabase
        .table("cve_documents")
        .select("cve_id", count="exact")
        .limit(1)
        .execute()
    )

    total_cves = count_response.count or 0

    print("==============================")
    print("CVE Documents 總數:", total_cves)
    print("==============================")

    # ========================================================
    # 取得目前已完成 Embedding 的 CVE 及其 content_hash
    # ========================================================

    print("正在取得已完成 Embedding...")

    # Supabase 單次查詢可能只回傳前 1000 筆，
    # 所以分頁取得全部已完成的 CVE，避免遺漏。
    existing_hash_by_id = {}
    embedding_page_size = 1000
    embedding_start = 0

    while True:
        embedding_end = embedding_start + embedding_page_size - 1

        try:
            embedding_response = (
                supabase
                .table(EMBEDDING_TABLE)
                .select("cve_id, content_hash")
                .range(embedding_start, embedding_end)
                .execute()
            )
        except Exception as e:
            print("取得已完成 Embedding 失敗:")
            print(e)
            raise

        rows = embedding_response.data or []

        for row in rows:
            if row.get("cve_id"):
                existing_hash_by_id[row["cve_id"]] = row.get("content_hash")

        if len(rows) < embedding_page_size:
            break

        embedding_start += embedding_page_size

    print(
        "目前已有 Embedding:",
        len(existing_hash_by_id)
    )

    print("※ content_hash 與目前內容相同的 CVE 會自動跳過")
    print("※ content_hash 不同（內容已更新）的 CVE 會重新 embedding")
    print("※ 重新執行程式會從尚未完成 / 尚未更新的 CVE 繼續")

    # ========================================================
    # 統計
    # ========================================================

    total_success = 0
    total_skip = 0
    total_error = 0
    total_new = 0
    total_updated = 0

    # ========================================================
    # 分頁取得 CVE
    # ========================================================

    for start in range(0, total_cves, PAGE_SIZE):

        end = min(start + PAGE_SIZE - 1, total_cves - 1)

        print()
        print("==============================")
        print(f"取得 CVE {start + 1} ~ {end + 1}")
        print("==============================")

        try:
            response = (
                supabase
                .table("cve_documents")
                .select(
                    "cve_id, description, cvss_score, severity, cwe, "
                    "reference_urls"
                )
                .range(start, end)
                .execute()
            )

            cves = response.data or []

        except Exception as e:
            print("取得 CVE 失敗:")
            print(e)

            total_error += (end - start + 1)
            continue

        print("本頁取得:", len(cves))

        # ====================================================
        # 建立 Batch
        # ====================================================

        batch = []

        for cve in cves:

            cve_id = cve.get("cve_id")

            if not cve_id:
                continue

            if target_cve_ids is not None and cve_id not in target_cve_ids:
                continue

            # ================================================
            # 內容比對：是否需要（重新）embedding
            # ================================================

            content_hash = compute_content_hash(cve)
            existing_hash = existing_hash_by_id.get(cve_id)
            is_new = cve_id not in existing_hash_by_id

            if not is_new and existing_hash == content_hash:
                total_skip += 1
                print("Skip (unchanged):", cve_id)
                continue

            # ================================================
            # Batch 內去重
            # ================================================

            if any(item["cve_id"] == cve_id for item in batch):
                print("Batch 重複，跳過:", cve_id)
                total_skip += 1
                continue

            # ================================================
            # 建立 Embedding 文字
            # ================================================

            content = f"""
CVE ID:
{cve_id}

Description:
{cve.get('description') or ''}

CVSS:
{cve.get('cvss_score')}

Severity:
{cve.get('severity')}

CWE:
{cve.get('cwe')}
"""

            # ================================================
            # Gemini Embedding
            # ================================================

            success_this_cve = False
            limited_this_cve = False

            for retry in range(MAX_RETRY):

                if (
                    max_embeddings is not None
                    and gemini_call_count >= max_embeddings
                ):
                    limited_this_cve = True
                    break

                try:
                    print(
                        f"Gemini Embedding: "
                        f"{cve_id} ({retry + 1}/{MAX_RETRY})"
                        + (" [新增]" if is_new else " [內容更新]")
                    )

                    gemini_call_count += 1
                    result = client.models.embed_content(
                        model="gemini-embedding-001",
                        contents=content,
                        config={"output_dimensionality": 768}
                    )

                    embedding = result.embeddings[0].values

                    if len(embedding) != EMBEDDING_DIMENSION:
                        raise ValueError(
                            f"Embedding 維度錯誤: {len(embedding)}"
                        )

                    batch.append({
                        "cve_id": cve_id,
                        "content": content,
                        "embedding": embedding,
                        "content_hash": content_hash,
                    })
                    success_this_cve = True
                    break

                except Exception as e:
                    print(f"Embedding Error: {cve_id}")
                    print(e)

                    if retry < MAX_RETRY - 1:
                        wait_time = 2 ** retry
                        print(f"{wait_time} 秒後重試...")
                        time.sleep(wait_time)

            if not success_this_cve:
                # Embedding 失敗：不動 existing_hash_by_id，
                # 保留原本（可能是舊版本，也可能完全沒有）的資料，
                # 下次執行會因為 hash 仍然不同而重新嘗試。
                if limited_this_cve:
                    limited_count += 1
                    print("Embedding 上限已達，保留下次處理:", cve_id)
                else:
                    total_error += 1
                    print("Embedding 失敗:", cve_id)
            else:
                if is_new:
                    total_new += 1
                else:
                    total_updated += 1

            # ================================================
            # Batch 滿了就寫入 Supabase
            # ================================================

            if len(batch) >= BATCH_SIZE:
                saved = _flush_batch(
                    supabase,
                    batch,
                    existing_hash_by_id,
                )
                total_success += saved
                if saved < len(batch):
                    total_error += len(batch) - saved
                batch = []

            time.sleep(SLEEP_TIME)

        # ========================================================
        # 本頁剩餘 Batch
        # ========================================================

        if batch:
            saved = _flush_batch(
                supabase,
                batch,
                existing_hash_by_id,
            )
            total_success += saved
            if saved < len(batch):
                total_error += len(batch) - saved

    # ========================================================
    # 最終統計
    # ========================================================

    print()
    print()
    print("==============================")
    print("Embedding 全部處理完成")
    print("==============================")
    print("CVE Documents:", total_cves)
    print("本次成功:", total_success, f"(新增 {total_new} / 更新 {total_updated})")
    print("跳過（內容未變）:", total_skip)
    print("本次失敗:", total_error)
    print("==============================")

    # ========================================================
    # 再查一次實際 Embedding 數量
    # ========================================================

    final_response = (
        supabase
        .table(EMBEDDING_TABLE)
        .select("cve_id", count="exact")
        .limit(1)
        .execute()
    )

    final_count = final_response.count or 0

    print("目前 Embedding 總數:", final_count)
    print("還缺:", max(total_cves - final_count, 0))
    print("==============================")

    return {
        "success_count": total_success,
        "skipped_count": total_skip,
        "error_count": total_error,
        "new_count": total_new,
        "updated_count": total_updated,
        "gemini_call_count": gemini_call_count,
        "limited_count": limited_count,
        "complete": limited_count == 0,
    }


def _flush_batch(supabase, batch, existing_hash_by_id):
    """Upsert one batch of {cve_id, content, embedding, content_hash}
    rows. Only updates existing_hash_by_id (our in-memory record of
    "what's already embedded and up to date") after the Supabase write
    actually succeeds — never before, and never by deleting the old
    row first. If the upsert fails, the previous cve_embeddings row
    (if any) is left exactly as it was, and this batch's CVEs will be
    re-attempted on the next run because their stored hash still won't
    match.
    """

    print()
    print(f"送出 Gemini Batch / Supabase Batch ({len(batch)} 筆)")

    for retry in range(MAX_RETRY):

        try:
            supabase.table(EMBEDDING_TABLE).upsert(
                batch,
                on_conflict="cve_id"
            ).execute()

            for item in batch:
                if item.get("cve_id"):
                    existing_hash_by_id[item["cve_id"]] = item["content_hash"]

            print(f"Batch 寫入成功: {len(batch)} 筆")
            return len(batch)

        except Exception as e:
            print()
            print("Batch Error:")
            print(e)

            if retry < MAX_RETRY - 1:
                wait_time = 5 * (retry + 1)
                print(f"{wait_time} 秒後重試...")
                time.sleep(wait_time)

    print("Batch 寫入失敗，下次重新執行即可")
    return 0


if __name__ == "__main__":
    main()
