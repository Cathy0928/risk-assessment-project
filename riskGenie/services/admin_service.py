"""Data access helpers for admin account and permission management."""

from datetime import datetime, timezone

try:
    from .supabase_client import get_supabase_admin_client
except ImportError:  # Allows imports when running from inside riskGenie/.
    from supabase_client import get_supabase_admin_client


USER_FIELDS = "id, username, email, role_id, company_id, is_active"
USER_ACTIVE_FIELD = "is_active"


class DuplicateEmailError(ValueError):
    """Raised when a public user profile already uses an email address."""


class CompanyNotFoundError(ValueError):
    """Raised when a requested company does not exist."""


class UserNotFoundError(LookupError):
    """Raised when a requested user profile does not exist."""


class ProfileCreationError(RuntimeError):
    """Raised after Auth succeeds but creating the public profile fails."""


class UserStatusConfigError(RuntimeError):
    """Raised when the users table does not yet support account disabling."""


def _response_data(response):
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("data", response)
    return getattr(response, "data", response)


def _first_record(response):
    data = _response_data(response)
    if isinstance(data, list):
        return data[0] if data else None
    return data


def _auth_user_id(auth_response):
    user = getattr(auth_response, "user", None)
    if user is None and isinstance(auth_response, dict):
        user = auth_response.get("user")
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def _email_exists(client, email):
    response = (
        client.table("users")
        .select("id")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    return bool(_response_data(response))


def _company_exists(client, company_id):
    response = (
        client.table("companies")
        .select("id")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    return bool(_response_data(response))


def _insert_profile(client, profile):
    response = client.table("users").insert(profile).execute()
    created = _first_record(response)
    if not created:
        raise ProfileCreationError("Unable to create the user profile.")
    return created


def _delete_auth_user(client, user_id):
    client.auth.admin.delete_user(user_id)


def _missing_active_column(exc):
    message = str(exc).lower()
    return USER_ACTIVE_FIELD in message and any(
        token in message for token in ("column", "schema", "field", "pgrst")
    )


def _duplicate_email_error(exc):
    message = str(exc).lower()
    return "email" in message and any(
        token in message for token in ("already", "duplicate", "exists", "registered")
    )


def _get_user(client, user_id, company_id):
    try:
        response = (
            client.table("users")
            .select(USER_FIELDS)
            .eq("id", user_id)
            .eq("company_id", company_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        if _missing_active_column(exc):
            raise UserStatusConfigError(
                "public.users.is_active is required to disable accounts."
            ) from exc
        raise

    return _first_record(response)


def list_roles():
    response = (
        get_supabase_admin_client()
        .table("roles")
        .select("id, role_name")
        .execute()
    )
    return response.data or []


def list_users(company_id):
    response = (
        get_supabase_admin_client()
        .table("users")
        .select(USER_FIELDS)
        .eq("company_id", company_id)
        .execute()
    )
    return response.data or []


def create_user(username, email, password, role_id, company_id):
    client = get_supabase_admin_client()

    if _email_exists(client, email):
        raise DuplicateEmailError("Email is already in use.")
    if not _company_exists(client, company_id):
        raise CompanyNotFoundError("Company does not exist.")

    try:
        auth_response = client.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
            }
        )
    except Exception as exc:
        if _duplicate_email_error(exc):
            raise DuplicateEmailError("Email is already in use.") from exc
        raise
    auth_user_id = _auth_user_id(auth_response)
    if not auth_user_id:
        raise RuntimeError("Supabase Auth Admin API did not return a user id.")

    profile = {
        "id": auth_user_id,
        "username": username,
        "email": email,
        "role_id": role_id,
        "company_id": company_id,
    }

    try:
        return _insert_profile(client, profile)
    except Exception as exc:
        try:
            _delete_auth_user(client, auth_user_id)
        except Exception:
            pass
        if _duplicate_email_error(exc):
            raise DuplicateEmailError("Email is already in use.") from exc
        if isinstance(exc, ProfileCreationError):
            raise
        raise ProfileCreationError("Unable to create the user profile.") from exc


def update_user(user_id, changes, company_id):
    client = get_supabase_admin_client()
    existing = _get_user(client, user_id, company_id)
    if not existing:
        raise UserNotFoundError("User not found.")

    response = (
        client.table("users")
        .update(changes)
        .eq("id", user_id)
        .eq("company_id", company_id)
        .execute()
    )
    updated = _first_record(response)
    if not updated:
        raise UserNotFoundError("User not found.")
    return updated


def disable_user(user_id, company_id):
    client = get_supabase_admin_client()
    user = _get_user(client, user_id, company_id)
    if not user:
        raise UserNotFoundError("User not found.")
    if user.get(USER_ACTIVE_FIELD) is False:
        return user, True

    try:
        response = (
            client.table("users")
            .update({USER_ACTIVE_FIELD: False})
            .eq("id", user_id)
            .eq("company_id", company_id)
            .execute()
        )
    except Exception as exc:
        if _missing_active_column(exc):
            raise UserStatusConfigError(
                "public.users.is_active is required to disable accounts."
            ) from exc
        raise

    disabled = _first_record(response)
    if not disabled:
        raise UserNotFoundError("User not found.")
    return disabled, False


def write_audit_log(operator_id, action, ip_address, status):
    if not operator_id:
        return False

    payload = {
        "user_id": operator_id,
        "action": action,
        "log_time": datetime.now(timezone.utc).isoformat(),
        "ip_address": ip_address,
        "status": status,
    }
    get_supabase_admin_client().table("audit_logs").insert(payload).execute()
    return True
