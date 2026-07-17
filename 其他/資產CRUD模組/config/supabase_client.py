import os

from supabase import create_client


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY environment variable.")

if SUPABASE_KEY.lower().startswith("sb_secret_") or "service_role" in SUPABASE_KEY.lower():
    raise RuntimeError("Legacy CRUD module must use the Supabase anon key only.")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
