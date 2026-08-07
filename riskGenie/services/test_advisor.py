from rag_service import generate_advice



asset = """

資產名稱:
Apache Web Server 2.4


用途:
公開網站服務


資料:
包含使用者個人資料


CIA:

C=5
I=5
A=4


CVSS:
8.7


風險值:
43.5

"""


result = generate_advice(
    asset
)


print(result)