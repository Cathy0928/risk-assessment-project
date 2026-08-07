import os
import time
from dotenv import load_dotenv
from supabase import create_client
from google import genai


load_dotenv()


# =====================
# Supabase
# =====================

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)


# =====================
# Gemini
# =====================

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)


# =====================
# 取得 CVE
# =====================

response = supabase.table(
    "cve_documents"
).select(
    "*"
).limit(
    1000       # 測試用，成功後刪掉
).execute()


print(
    "CVE數量:",
    len(response.data)
)



success = 0
error = 0



# =====================
# 建立 Embedding
# =====================

for index, cve in enumerate(response.data):

    try:

        content = f"""
CVE ID:
{cve['cve_id']}

Description:
{cve['description']}

CVSS:
{cve['cvss_score']}

Severity:
{cve['severity']}

CWE:
{cve['cwe']}
"""


        result = client.models.embed_content(

            model="gemini-embedding-001",

            contents=content,

            config={
                "output_dimensionality": 768
            }

        )


        embedding = result.embeddings[0].values



        supabase.table(
            "cve_embeddings"
        ).upsert({

            "cve_id": cve["cve_id"],

            "content": content,

            "embedding": embedding

        }).execute()



        success += 1


        print(
            index + 1,
            "Embedding:",
            cve["cve_id"]
        )


        # 避免 API 過快
        time.sleep(0.1)



    except Exception as e:


        error += 1


        print(
            "Error:",
            cve["cve_id"],
            e
        )



print("====================")
print("完成")
print("成功:", success)
print("失敗:", error)
print("====================")