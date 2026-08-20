import os
import logging

from dotenv import load_dotenv
from supabase import create_client
from google import genai

# ==========================
# Environment
# ==========================
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
def search_cve(query, match_count=10, similarity_threshold=0.65):
    try:
        # 1. 建立 Query Embedding
        embedding = create_embedding(query)
        logger.info(
            "開始進行 CVE RAG 搜尋，query 長度：%s",
            len(query)
        )

        # 2. Supabase Vector Search
        response = supabase.rpc(
            "search_cve",
            {
                "query_embedding": embedding,
                "match_count": match_count
            }
        ).execute()
        results = response.data or []
        logger.info(
            "RAG 原始搜尋結果：%s 筆",
            len(results)
        )

        # 3. 相似度過濾
        filtered = []
        for item in results:
            similarity = item.get("similarity", 0)
            try:
                similarity = float(similarity)
            except (TypeError, ValueError):
                similarity = 0.0
            if similarity >= similarity_threshold:
                filtered.append({
                    "cve_id": item.get("cve_id", ""),
                    "content": item.get("content", ""),
                    "similarity": similarity
                })
        # 4. 依相似度由高到低排序
        filtered.sort(
            key=lambda x: x["similarity"],
            reverse=True
        )

        # 最多取前 5 筆給 AI
        filtered = filtered[:5]
        logger.info(
            "RAG 過濾後結果：%s 筆，threshold=%s",
            len(filtered),
            similarity_threshold
        )

        for item in filtered:
            logger.info(
                "CVE=%s similarity=%.3f",
                item["cve_id"],
                item["similarity"]
            )

        return filtered

    except Exception as e:
        logger.exception(
            "CVE RAG 搜尋失敗：%s",
            e
        )
        # 不讓 RAG 搜尋錯誤直接造成整個 AI Advisor 崩潰
        return []

# ==========================
# Generate Advice
# ==========================
def generate_advice(
    asset_name,
    asset_type="",
    description="",
    threat_description="",
    confidentiality=0,
    integrity=0,
    availability=0,
    legality=0,
    cvss_score=0,
    likelihood_score=0,
    impact_score=0,
    risk_score=0,
    risk_level=""
):
    logger.info(
        "開始產生 AI 建議：%s",
        asset_name
    )

    # 1. 整理資產資訊
    threat_description = (
        threat_description.strip()
        if isinstance(threat_description, str)
        else ""
    )

    description = (
        description.strip()
        if isinstance(description, str)
        else ""
    )

    asset_type = (
        asset_type.strip()
        if isinstance(asset_type, str)
        else ""
    )

    # 如果前端傳 "-"，視為沒有資料
    if threat_description in ["-", "無", "無資料", "None", "null"]:
        threat_description = ""

    if description in ["-", "無", "無資料", "None", "null"]:
        description = ""

    # 2. 建立 RAG Query
    rag_query = f"""
資產名稱：{asset_name}
資產類型：{asset_type}
資產描述：{description}
目前威脅與弱點：{threat_description}
"""

    logger.info(
        "RAG Query：%s",
        rag_query
    )

    # 3. 搜尋 CVE
    cves = search_cve(
        rag_query,
        match_count=10,
        similarity_threshold=0.70
    )

    # 4. 建立 RAG Context
    context = build_cve_context(cves)

    # 5. 建立完整 Prompt
    prompt = f"""
你是一名企業資安風險評鑑 AI 顧問。

你的工作是根據「實際提供的資產資料」、「風險評鑑結果」
以及「RAG 檢索到的 CVE 資料」，產生專業且可執行的資安改善建議。

==================================================
【一、資產資訊】
==================================================

資產名稱：
{asset_name}

資產類型：
{asset_type}

資產描述：
{description if description else "未提供"}

威脅與弱點：
{threat_description if threat_description else "未提供"}

==================================================
【二、風險評鑑結果】
==================================================

機密性 C：
{confidentiality}

完整性 I：
{integrity}

可用性 A：
{availability}

適法性 L：
{legality}

CVSS：
{cvss_score}

發生機率 Likelihood：
{likelihood_score}

Impact Score：
{impact_score}

Risk Score：
{risk_score}

Risk Level：
{risk_level}

==================================================
【三、RAG CVE 資料】
==================================================

{context}

==================================================
【四、非常重要的分析規則】
==================================================

請嚴格遵守以下規則：

1. CVSS 分數只代表本次風險評鑑所設定的弱點嚴重度，
   不代表一定存在 CVE。

2. 只有 RAG CVE 資料中實際出現的 CVE，
   才可以在回答中提及 CVE 編號。

3. 禁止自行創造、猜測或補充任何 CVE 編號。

4. 如果 RAG 沒有找到明確相關 CVE，
   必須明確寫：
   「目前 RAG 知識庫沒有找到與此資產直接相關的 CVE。」

5. 不可以因為 CVSS = 7.5、9.8 等高分，
   就直接宣稱目前資產存在某個漏洞。

6. RAG 搜尋結果的「相似度」只能作為候選資料，
   不代表該 CVE 與資產直接相關。

7. 判斷 CVE 是否直接相關時，
   必須優先確認：
   - 產品名稱
   - 軟體名稱
   - 軟體版本
   - 技術元件
   - 部署環境

8. 如果 RAG 找到 CVE，
   但無法確認產品、版本或技術元件與本資產一致，
   不得宣稱該 CVE 是本資產目前存在的漏洞。

9. 如果只能確認語意相似，
   請說明：
   「RAG 找到相似漏洞資訊，但目前無法確認與本資產直接相關。」

10. 如果資產沒有提供產品名稱或版本，
    不得自行猜測資產使用的資料庫、Web Server、
    作業系統或其他軟體。

11. 如果威脅與弱點沒有提供，
    不可以自行假設：
    - 未修補
    - 權限過大
    - 加密不足
    - MFA 未啟用
    - 防火牆未設定

12. 如果沒有直接相關 CVE，
    必須明確說明目前 RAG 知識庫沒有找到直接相關 CVE。

    此時可以提出：
    - 確認資產所使用的軟體名稱與版本
    - 確認作業系統與部署環境
    - 執行版本型弱點掃描
    - 比對 NVD/CVE 資訊
    - 確認高風險漏洞修補狀態

    但禁止宣稱資產目前一定存在某個漏洞。

13. 如果涉及法律或合規風險，
    未提供具體法規名稱時，
    不得自行指定 GDPR 或其他特定法規。
    應使用「適用之個人資料保護及相關法規」等描述。

14. 不得宣稱某項資安措施一定可以降低法律責任。
    應描述其對降低資訊外洩風險、
    事件影響或營運衝擊的作用。

15. Risk Score 與 Risk Level 已由 RiskGenie
    Risk Engine 計算完成，
    不得自行重新計算 Risk Score，
    不得自行修改 Risk Level。

16. AI 的工作是「解釋風險結果」與「提供改善建議」，
    而不是取代 Risk Engine。

17. CIA 與 L 分數必須依照提供的評鑑結果分析。

18. 改善建議必須與資產描述、CIA、L、CVSS、
    Likelihood、Risk Score 及已知威脅相關。

19. 不要只提供一般性的資安口號。

20. ISO 27002 控制措施只能列出與目前風險相關的措施，
    不需要為了湊數量而列出無關控制措施。

21. 如果沒有直接 CVE，
    可以提出：
    - 確認資產實際使用的軟體與版本
    - 執行版本型弱點掃描
    - 確認修補狀態
    - 檢查存取控制
    - 檢查加密措施
    等建議。

22. 所有內容使用繁體中文。

==================================================
【五、分析要求】
==================================================

請完成：

1. 說明目前主要風險。

2. 解釋為什麼目前 RiskGenie 判定為
   「{risk_level}」。

3. 分析 CIA 與適法性的重要性。

4. 說明 RAG 是否找到直接相關 CVE。

5. 如果有相關 CVE：
   - 列出 CVE ID
   - 說明與資產的關聯
   - 說明漏洞可能造成的影響

6. 如果沒有相關 CVE：
   明確說明沒有找到直接相關 CVE，
   不得自行補充 CVE。

7. 提出具體改善措施。

8. 每一項改善措施說明：
   - 建議做什麼
   - 為什麼
   - 建議優先程度

9. 提供相關 ISO 27002:2022 控制措施。

10. 最後提出最優先處理事項。

==================================================
【六、輸出格式】
==================================================

請嚴格按照以下格式回答：

【風險分析】

說明目前主要資安風險，
並結合資產描述、CIA、L、CVSS、
Likelihood、Risk Score 與 Risk Level。

【可能影響】

列出可能造成的：
1. 資料影響
2. 營運影響
3. 法律 / 合規影響
4. 財務或聲譽影響

【相關漏洞 / CVE】

如果 RAG 有找到直接相關 CVE：

CVE-XXXX-XXXXX
- 漏洞說明：
- 與本資產的關聯：
- 可能影響：

如果沒有：

目前 RAG 知識庫沒有找到與此資產直接相關的 CVE。
建議確認資產實際使用的軟體、版本與部署環境，
再進一步進行版本型弱點掃描。

【ISO 27002:2022 控制措施】

請列出最相關的 2～4 項控制措施，
並說明每項措施與目前風險的關聯。

【改善建議】

請依照目前 Risk Score、Risk Level、CIA/L、
CVSS、Likelihood 以及 RAG 檢索結果，
提出 3 項最具體且可執行的改善措施。

請依重要性排序，格式如下：

1. 【最高優先】
   措施：
   原因：

2. 【高優先】
   措施：
   原因：

3. 【中優先】
   措施：
   原因：

每項措施都必須：
- 與目前資產及風險直接相關
- 說明具體應執行的工作
- 說明為什麼需要處理
- 不得重複前面的建議

【風險降低重點】

請用 2～3 句話總結目前最重要的風險降低方向，
不要重新列出前三項改善措施。
"""

    # ========================================================
    # 6. Gemini Generate
    # ========================================================

    try:

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config={
                "temperature": 0.2,
                "max_output_tokens": 2500
            }
        )

        if not response or not response.text:

            raise ValueError(
                "Gemini 沒有回傳 AI 建議內容"
            )

        advice = response.text.strip()

        logger.info(
            "AI 建議產生成功：%s",
            asset_name
        )

        return advice

    except Exception as e:

        logger.error(
            "Gemini generate advice error: %s",
            e,
            exc_info=True
        )
        raise

def build_cve_context(cves):
    if not cves:
        return "目前 RAG 知識庫沒有找到與此資產直接相關的 CVE。"

    context_parts = []

    for cve in cves:
        cve_id = cve.get("cve_id", "未知 CVE")
        content = cve.get("content", "")
        similarity = cve.get("similarity", 0)

        context_parts.append(
            f"""
CVE ID：{cve_id}
相似度：{similarity:.3f}
漏洞資訊：
{content}
"""
        )

    return "\n--------------------\n".join(context_parts)