"""Centralized Supabase client creation for RiskGenie."""

import os


class SupabaseConfigError(RuntimeError):
    """Raised when Supabase runtime configuration is missing or unsafe."""


def _require_env(name):
    value = os.getenv(name)
    if not value:
        raise SupabaseConfigError(f"Missing required environment variable: {name}")
    return value


def _validate_anon_key(key):
    normalized = key.strip().lower()
    if normalized.startswith("sb_secret_"):
        raise SupabaseConfigError("SUPABASE_ANON_KEY must not be an sb_secret_ key.")
    if "service_role" in normalized:
        raise SupabaseConfigError("SUPABASE_ANON_KEY must not be a service role key.")


def _validate_secret_key(secret_key):
    normalized = secret_key.strip().lower()
    if normalized.startswith("sb_publishable_"):
        raise SupabaseConfigError(
            "SUPABASE_SECRET_KEY must be a backend secret or service role key."
        )

    anon_key = os.getenv("SUPABASE_ANON_KEY")
    if anon_key and secret_key == anon_key:
        raise SupabaseConfigError(
            "SUPABASE_SECRET_KEY must not be the same as SUPABASE_ANON_KEY."
        )


def _create_client(supabase_url, supabase_key):
    try:
        from supabase import create_client
    except ImportError as exc:
        raise SupabaseConfigError(
            "The supabase package is required. Install dependencies from requirements.txt."
        ) from exc

    return create_client(supabase_url, supabase_key)


def get_supabase_client():
    supabase_url = _require_env("SUPABASE_URL")
    supabase_key = _require_env("SUPABASE_ANON_KEY")
    _validate_anon_key(supabase_key)
    return _create_client(supabase_url, supabase_key)


def get_supabase_admin_client():
    supabase_url = _require_env("SUPABASE_URL")
    secret_key = _require_env("SUPABASE_SECRET_KEY")
    _validate_secret_key(secret_key)
    return _create_client(supabase_url, secret_key)
