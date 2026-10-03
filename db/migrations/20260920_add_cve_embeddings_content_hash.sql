-- ============================================================
-- SUPERSEDED — DO NOT APPLY.
--
-- Kept only for audit trail. This file is left uncommitted in the
-- working tree; delete it once reviewed.
--
-- Why: this migration was written to add content_hash to the
-- embeddings table because the repo had no record of that column
-- existing anywhere. A read-only Supabase check has since confirmed
-- the embeddings table already has embedding / content_hash /
-- updated_at — the underlying problem this migration solved is already
-- solved in production, so applying it now would at most be a no-op.
--
-- The production table name is confirmed via pg_class as plural
-- public.cve_embeddings (OID 34266); singular "cve_embedding" does not
-- exist. This corrects an earlier screenshot-based guess that had it
-- backwards. The existing search_cve RPC already reads
-- "FROM cve_embeddings", matching this — it was never broken.
--
-- Not reactivated per "do not re-enable a superseded migration without
-- new schema evidence": the new evidence here (the plural table name)
-- only confirms WHERE content_hash already lives, it does not change
-- the fact that the column is already there and this migration is
-- still unnecessary.
-- ============================================================

BEGIN;

ALTER TABLE public.cve_embeddings
    ADD COLUMN IF NOT EXISTS content_hash text;

COMMIT;
