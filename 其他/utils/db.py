import os

import mysql.connector


DB_HOST = os.getenv("DB_HOST", " ")
DB_NAME = os.getenv("DB_NAME", " ")
DB_USER = os.getenv("DB_USER", " ")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


def get_connection():
    connection = mysql.connector.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    return connection


conn = None
try:
    conn = get_connection()
    print("Database connection succeeded.")
except mysql.connector.Error as err:
    print(f"Database connection failed: {err}")
finally:
    if conn and conn.is_connected():
        conn.close()
        print("Database connection closed.")
