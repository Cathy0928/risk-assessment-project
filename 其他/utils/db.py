# 匯入 MySQL 資料庫模組
import mysql.connector

# MySQL 連線資訊
DB_HOST = " "  # MySQL 資料庫的 IP 地址
DB_NAME = " "  # 資料庫名稱
DB_USER = " "  # 資料庫使用者名稱
DB_PASSWORD = " "  # 資料庫密碼

# 建立資料庫連線
def get_connection():
    connection = mysql.connector.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    return connection

# 測試連接
try:
    conn = get_connection()
    print("連接成功")
except mysql.connector.Error as err:
    print(f"連接失敗: {err}")
finally:
    if conn.is_connected():
        conn.close()
        print("連接已關閉")