import os
import json
from supabase import create_client
from dotenv import load_dotenv


# =====================
# Supabase 設定
# =====================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


# =====================
# NVD資料位置
# =====================

CVE_PATH = "./cve-data"


success_count = 0
error_count = 0


# =====================
# 搜尋 NVD JSON
# =====================

for root, dirs, files in os.walk(CVE_PATH):

    for file in files:

        if not file.endswith(".json"):
            continue


        filepath = os.path.join(
            root,
            file
        )


        try:

            with open(
                filepath,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)



            vulnerabilities = data.get(
                "vulnerabilities",
                []
            )


            print(
                "讀取:",
                file,
                "數量:",
                len(vulnerabilities)
            )


            # =====================
            # 每個 CVE
            # =====================

            for item in vulnerabilities:

                try:

                    cve = item.get(
                        "cve",
                        {}
                    )


                    cve_id = cve.get(
                        "id"
                    )


                    if not cve_id:
                        continue



                    # =====================
                    # Description
                    # =====================

                    description = ""


                    for desc in cve.get(
                        "descriptions",
                        []
                    ):

                        if desc.get(
                            "lang"
                        ) == "en":

                            description = desc.get(
                                "value",
                                ""
                            )

                            break



                    # =====================
                    # CVSS
                    # =====================

                    cvss_score = None
                    severity = None


                    metrics = cve.get(
                        "metrics",
                        {}
                    )


                    if metrics.get(
                        "cvssMetricV31"
                    ):

                        cvss = metrics[
                            "cvssMetricV31"
                        ][0].get(
                            "cvssData",
                            {}
                        )


                        cvss_score = cvss.get(
                            "baseScore"
                        )

                        severity = cvss.get(
                            "baseSeverity"
                        )



                    elif metrics.get(
                        "cvssMetricV30"
                    ):

                        cvss = metrics[
                            "cvssMetricV30"
                        ][0].get(
                            "cvssData",
                            {}
                        )


                        cvss_score = cvss.get(
                            "baseScore"
                        )

                        severity = cvss.get(
                            "baseSeverity"
                        )



                    elif metrics.get(
                        "cvssMetricV2"
                    ):

                        cvss = metrics[
                            "cvssMetricV2"
                        ][0].get(
                            "cvssData",
                            {}
                        )


                        cvss_score = cvss.get(
                            "baseScore"
                        )



                    # =====================
                    # CWE
                    # =====================

                    cwe = None


                    for weakness in cve.get(
                        "weaknesses",
                        []
                    ):

                        for desc in weakness.get(
                            "description",
                            []
                        ):

                            if desc.get(
                                "lang"
                            ) == "en":

                                cwe = desc.get(
                                    "value"
                                )

                                break



                    # =====================
                    # Reference
                    # =====================

                    references = []


                    for ref in cve.get(
                        "references",
                        []
                    ):

                        url = ref.get(
                            "url"
                        )

                        if url:
                            references.append(
                                url
                            )



                    # =====================
                    # 寫入 Supabase
                    # =====================

                    row = {

                        "cve_id": cve_id,

                        "description": description,

                        "cvss_score": cvss_score,

                        "severity": severity,

                        "cwe": cwe,

                        "reference_urls": references

                    }


                    supabase.table(
                        "cve_documents"
                    ).upsert(
                        row
                    ).execute()



                    success_count += 1


                    print(
                        "Inserted:",
                        cve_id,
                        "| CVSS:",
                        cvss_score,
                        "|",
                        severity
                    )


                except Exception as e:

                    error_count += 1

                    print(
                        "CVE錯誤:",
                        cve_id,
                        "|",
                        e
                    )



        except Exception as e:

            error_count += 1

            print(
                "檔案錯誤:",
                file,
                "|",
                e
            )



print("====================")
print("匯入完成")
print("成功:", success_count)
print("失敗:", error_count)
print("====================")