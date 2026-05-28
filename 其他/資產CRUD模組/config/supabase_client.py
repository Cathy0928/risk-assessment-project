from supabase import create_client

SUPABASE_URL = "https://mdbhkbxkvologsvyhrwq.supabase.co"
SUPABASE_KEY = "sb_secret_AvlFMbMjsIN_BoYUUWTRFQ_ktZNyh_5"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)