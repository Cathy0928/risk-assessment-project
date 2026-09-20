import os
import time

from dotenv import load_dotenv
from supabase import create_client
from google import genai


# ============================================================
# 載入環境變數
# ============================================================

load_dotenv()


# ============================================================
# Supabase
# ============================================================

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)


# ============================================================
# Gemini
# ============================================================

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


# ============================================================
# 設定
# ============================================================

PAGE_SIZE = 1000

BATCH_SIZE = 50

MAX_RETRY = 3

SLEEP_TIME = 0.2

EMBEDDING_DIMENSION = 768


# ============================================================
# 取得 CVE Documents 總數
# ============================================================

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


# ============================================================
# 取得目前已完成 Embedding 的 CVE
# ============================================================

print("正在取得已完成 Embedding...")


# Supabase 單次查詢可能只回傳前 1000 筆，
# 所以分頁取得全部已完成的 CVE，避免重複做 Embedding。
existing_ids = set()
embedding_page_size = 1000
embedding_start = 0

while True:
    embedding_end = embedding_start + embedding_page_size - 1

    try:
        embedding_response = (
            supabase
            .table("cve_embeddings")
            .select("cve_id")
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
            existing_ids.add(row["cve_id"])

    if len(rows) < embedding_page_size:
        break

    embedding_start += embedding_page_size


print(
    "目前已有 Embedding:",
    len(existing_ids)
)


print(
    "目前還缺:",
    max(total_cves - len(existing_ids), 0)
)

print("※ 已存在於 cve_embeddings 的 CVE 會自動跳過")
print("※ 重新執行程式會從尚未完成的 CVE 繼續")



# ============================================================
# 統計
# ============================================================

total_success = 0
total_skip = 0
total_error = 0


# ============================================================
# 分頁取得 CVE
# ============================================================

for start in range(
    0,
    total_cves,
    PAGE_SIZE
):

    end = min(
        start + PAGE_SIZE - 1,
        total_cves - 1
    )


    print()
    print("==============================")
    print(
        f"取得 CVE {start + 1} ~ {end + 1}"
    )
    print("==============================")


    try:

        response = (
            supabase
            .table("cve_documents")
            .select(
                "cve_id, description, cvss_score, severity, cwe"
            )
            .range(
                start,
                end
            )
            .execute()
        )

        cves = response.data or []


    except Exception as e:

        print("取得 CVE 失敗:")
        print(e)

        total_error += (
            end - start + 1
        )

        continue


    print(
        "本頁取得:",
        len(cves)
    )


    # ========================================================
    # 建立 Batch
    # ========================================================

    batch = []


    for cve in cves:

        cve_id = cve.get("cve_id")


        if not cve_id:

            continue


        # ====================================================
        # 已經有 Embedding
        # ====================================================

        if cve_id in existing_ids:

            total_skip += 1

            print(
                "Skip:",
                cve_id
            )

            continue


        # ====================================================
        # 建立 Embedding 文字
        # ====================================================

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


        # ====================================================
        # Batch 內去重
        # ====================================================

        if any(
            item["cve_id"] == cve_id
            for item in batch
        ):

            print(
                "Batch 重複，跳過:",
                cve_id
            )

            total_skip += 1

            continue


        # ====================================================
        # Gemini Embedding
        # ====================================================

        success_this_cve = False


        for retry in range(
            MAX_RETRY
        ):

            try:

                print(
                    f"Gemini Embedding: "
                    f"{cve_id} "
                    f"({retry + 1}/{MAX_RETRY})"
                )


                result = (
                    client
                    .models
                    .embed_content(

                        model="gemini-embedding-001",

                        contents=content,

                        config={
                            "output_dimensionality": 768
                        }
                    )
                )


                embedding = (
                    result
                    .embeddings[0]
                    .values
                )


                # =================================================
                # 確認維度
                # =================================================

                if len(embedding) != EMBEDDING_DIMENSION:

                    raise ValueError(
                        f"Embedding 維度錯誤: "
                        f"{len(embedding)}"
                    )


                # =================================================
                # 加入 Batch
                # =================================================

                batch.append({

                    "cve_id": cve_id,

                    "content": content,

                    "embedding": embedding

                })
                success_this_cve = True

                break


            except Exception as e:

                print(
                    f"Embedding Error: {cve_id}"
                )

                print(e)


                if retry < MAX_RETRY - 1:

                    wait_time = (
                        2 ** retry
                    )

                    print(
                        f"{wait_time} 秒後重試..."
                    )

                    time.sleep(
                        wait_time
                    )


        if not success_this_cve:

            total_error += 1

            print(
                "Embedding 失敗:",
                cve_id
            )


        # ====================================================
        # Batch 滿了就寫入 Supabase
        # ====================================================

        if len(batch) >= BATCH_SIZE:

            print()
            print(
                f"送出 Gemini Batch / Supabase Batch "
                f"({len(batch)} 筆)"
            )


            saved = False


            for retry in range(
                MAX_RETRY
            ):

                try:

                    # =================================================
                    # 使用 upsert + cve_id unique
                    # =================================================

                    supabase.table(
                        "cve_embeddings"
                    ).upsert(

                        batch,

                        on_conflict="cve_id"

                    ).execute()


                    total_success += len(batch)

                    # 只有 Supabase 寫入成功後，才標記為已完成。
                    for item in batch:
                        if item.get("cve_id"):
                            existing_ids.add(item["cve_id"])

                    print(
                        f"Batch 寫入成功: "
                        f"{len(batch)} 筆"
                    )


                    saved = True

                    break


                except Exception as e:

                    print()
                    print(
                        "Batch Error:"
                    )

                    print(e)


                    if retry < MAX_RETRY - 1:

                        wait_time = (
                            5 * (retry + 1)
                        )

                        print(
                            f"{wait_time} 秒後重試..."
                        )

                        time.sleep(
                            wait_time
                        )


            if not saved:

                total_error += len(batch)

                print(
                    "Batch 寫入失敗，"
                    "下次重新執行即可"
                )


            batch = []


        time.sleep(
            SLEEP_TIME
        )


    # ========================================================
    # 本頁剩餘 Batch
    # ========================================================

    if batch:

        print()
        print(
            f"送出最後 Batch "
            f"({len(batch)} 筆)"
        )


        saved = False


        for retry in range(
            MAX_RETRY
        ):

            try:

                supabase.table(
                    "cve_embeddings"
                ).upsert(

                    batch,

                    on_conflict="cve_id"

                ).execute()


                total_success += len(batch)

                # 最後一批成功後，也標記為已完成。
                for item in batch:
                    if item.get("cve_id"):
                        existing_ids.add(item["cve_id"])

                print(
                    f"Batch 寫入成功: "
                    f"{len(batch)} 筆"
                )


                saved = True

                break


            except Exception as e:

                print(
                    "Batch Error:"
                )

                print(e)


                if retry < MAX_RETRY - 1:

                    wait_time = (
                        5 * (retry + 1)
                    )

                    print(
                        f"{wait_time} 秒後重試..."
                    )

                    time.sleep(
                        wait_time
                    )


        if not saved:

            total_error += len(batch)

            print(
                "最後 Batch 寫入失敗"
            )


# ============================================================
# 最終統計
# ============================================================

print()
print()
print("==============================")
print("Embedding 全部處理完成")
print("==============================")


print(
    "CVE Documents:",
    total_cves
)


print(
    "本次成功:",
    total_success
)


print(
    "跳過已完成:",
    total_skip
)


print(
    "本次失敗:",
    total_error
)


print("==============================")


# ============================================================
# 再查一次實際 Embedding 數量
# ============================================================

final_response = (
    supabase
    .table("cve_embeddings")
    .select("cve_id", count="exact")
    .limit(1)
    .execute()
)


final_count = (
    final_response.count or 0
)


print(
    "目前 Embedding 總數:",
    final_count
)


print(
    "還缺:",
    max(
        total_cves - final_count,
        0
    )
)


print("==============================")