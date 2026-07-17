"""Data access helpers for admin account and permission management."""

try:
    from .supabase_client import get_supabase_client
except ImportError:  # Allows imports when running from inside riskGenie/.
    from supabase_client import get_supabase_client


def list_roles():
    response = (
        get_supabase_client()
        .table("roles")
        .select("id, role_name")
        .execute()
    )
    return response.data or []


def list_users():
    response = (
        get_supabase_client()
        .table("users")
        .select("id, username, email, role_id, company_id")
        .execute()
    )
    return response.data or []
