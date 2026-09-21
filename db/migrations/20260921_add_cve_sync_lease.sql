-- Renewable lease used by the standalone CVE sync CLI.
-- Prepare only; do not execute from this workspace.

BEGIN;

CREATE TABLE IF NOT EXISTS public.cve_sync_leases (
    lock_name text PRIMARY KEY,
    lock_token uuid NOT NULL,
    expires_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.acquire_cve_sync_lease(
    requested_lock_name text,
    requested_token uuid,
    requested_lease_seconds integer
)
RETURNS boolean
LANGUAGE sql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $function$
    INSERT INTO public.cve_sync_leases AS lease
        (lock_name, lock_token, expires_at, updated_at)
    VALUES (
        requested_lock_name,
        requested_token,
        now() + make_interval(secs => requested_lease_seconds),
        now()
    )
    ON CONFLICT (lock_name) DO UPDATE
    SET lock_token = EXCLUDED.lock_token,
        expires_at = EXCLUDED.expires_at,
        updated_at = now()
    WHERE lease.expires_at <= now()
    RETURNING true;
$function$;

CREATE OR REPLACE FUNCTION public.renew_cve_sync_lease(
    requested_lock_name text,
    requested_token uuid,
    requested_lease_seconds integer
)
RETURNS boolean
LANGUAGE sql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $function$
    UPDATE public.cve_sync_leases AS lease
    SET expires_at = now() + make_interval(secs => requested_lease_seconds),
        updated_at = now()
    WHERE lease.lock_name = requested_lock_name
      AND lease.lock_token = requested_token
      AND lease.expires_at > now()
    RETURNING true;
$function$;

CREATE OR REPLACE FUNCTION public.release_cve_sync_lease(
    requested_lock_name text,
    requested_token uuid
)
RETURNS boolean
LANGUAGE sql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $function$
    DELETE FROM public.cve_sync_leases AS lease
    WHERE lease.lock_name = requested_lock_name
      AND lease.lock_token = requested_token
    RETURNING true;
$function$;

REVOKE ALL ON FUNCTION public.acquire_cve_sync_lease(text, uuid, integer)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.acquire_cve_sync_lease(text, uuid, integer)
TO service_role;

REVOKE ALL ON FUNCTION public.renew_cve_sync_lease(text, uuid, integer)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.renew_cve_sync_lease(text, uuid, integer)
TO service_role;

REVOKE ALL ON FUNCTION public.release_cve_sync_lease(text, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.release_cve_sync_lease(text, uuid)
TO service_role;

COMMIT;
