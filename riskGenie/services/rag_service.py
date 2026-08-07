import os
import logging

from dotenv import load_dotenv
from supabase import create_client
from google import genai


load_dotenv()


logger = logging.getLogger(__name__)


# ==========================
# Supabase
# ==========================

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_ANON_KEY")
)



# ==========================
# Gemini
# ==========================

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)



# ==========================
# Embedding
# ==========================

def create_embedding(text):

    result = client.models.embed_content(

        model="gemini-embedding-001",

        contents=text,

        config={
            "output_dimensionality":768
        }

    )


    return result.embeddings[0].values





# ==========================
# RAG Search
# ==========================

def search_cve(query):


    embedding = create_embedding(query)



    response = supabase.rpc(

        "search_cve",

        {
            "query_embedding": embedding,

            "match_count":10
        }

    ).execute()



    results = response.data or []



    # 相似度過低不要使用

    filtered=[]


    for item in results:

        similarity = item.get(
            "similarity",
            0
        )


        if similarity >= 0.65:

            filtered.append(item)



    return filtered[:5]





# ==========================
# Generate Advice
# ==========================


def generate_advice(asset_info):


    cves = search_cve(
        asset_info
    )



    context=""


    for cve in cves:


        context += f"""

CVE ID:
{cve['cve_id']}


漏洞描述:
{cve['content']}


相似度:
{round(cve['similarity'],3)}


----------------

"""



    if context=="":
        
        context="沒有找到直接相關CVE"



    prompt=f"""

你是一名企業資安風險評鑑顧問。



請分析以下資產。



【資產資訊】

{asset_info}



【RAG漏洞資料】

{context}



重要規則：

1.
只能使用上述RAG提供的CVE資料。

2.
如果CVE與資產沒有直接關係，
請明確說明「無直接相關漏洞」。

3.
禁止自行創造CVE編號。

4.
不要引用不存在的漏洞。



請輸出：


一、風險等級

低 / 中 / 高


二、風險原因

說明資產暴露與漏洞影響。


三、CIA分析

機密性(C):

完整性(I):

可用性(A):


四、改善建議

提出具體措施。


五、ISO27002:2022控制措施


六、優先處理順序


"""



    try:


        response = client.models.generate_content(

            model="gemini-3.1-flash-lite",

            contents=prompt,

            config={

                "temperature":0.2,

                "max_output_tokens":1500

            }

        )


        return response.text



    except Exception as e:


        logger.error(
            "Gemini error %s",
            e
        )

        raise

if __name__ == "__main__":

    test_asset = """
    資產名稱:
    Apache Web Server 2.4

    描述:
    公開網站服務，儲存使用者資料

    CIA:
    C=5 I=5 A=4

    CVSS:
    9.8
    """

    result = generate_advice(test_asset)

    print(result)