-- ============================================================
-- READY FOR REVIEW — DO NOT APPLY FROM THIS WORKSPACE.
--
-- A read-only production schema check confirmed that cve_sync_runs
-- does not yet have query_start_date or query_end_date.
--
-- riskGenie/services/cve_sync_service.py requires the actual query
-- window end as its checkpoint. It deliberately does not use
-- completed_at because execution time and NVD query time are different
-- facts.
--
-- Both columns remain nullable so existing run-history rows stay valid.
-- ADD COLUMN IF NOT EXISTS keeps the migration idempotent.
-- ============================================================

BEGIN;

ALTER TABLE public.cve_sync_runs
    ADD COLUMN IF NOT EXISTS query_start_date timestamptz,
    ADD COLUMN IF NOT EXISTS query_end_date timestamptz;

COMMIT;
