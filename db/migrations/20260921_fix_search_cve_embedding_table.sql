-- ============================================================
-- SUPERSEDED — DO NOT APPLY. DO NOT MODIFY THE REAL search_cve RPC.
--
-- Kept only for audit trail; the SQL body below is left exactly as
-- originally written so the record of the mistake is preserved.
--
-- Why: this migration was written on a screenshot-based guess that the
-- production embedding table was singular "cve_embedding". A read-only
-- pg_class query has since confirmed the real table is plural
-- public.cve_embeddings (OID 34266) — "cve_embedding" does not exist —
-- and that the EXISTING, already-deployed search_cve RPC already reads
-- "FROM cve_embeddings", matching production. search_cve was never
-- broken; this migration's premise was wrong.
--
-- Applying this CREATE OR REPLACE FUNCTION would have overwritten a
-- working RPC with one that queries a table that does not exist,
-- breaking RAG retrieval entirely. It must not be applied.
--
-- Whether the real search_cve RPC is otherwise healthy (correct
-- columns, correct distance operator, correct grants) is unconfirmed
-- and is left to a separate read-only smoke test, not to reactivating
-- this file.
-- ============================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.search_cve(
    query_embedding vector,
    match_count integer
)
RETURNS TABLE (
    cve_id text,
    content text,
    similarity double precision
)
LANGUAGE sql
AS $function$
    SELECT
        embedding_record.cve_id::text AS cve_id,
        embedding_record.content::text AS content,
        1 - (embedding_record.embedding <=> query_embedding) AS similarity
    FROM public.cve_embedding AS embedding_record
    ORDER BY embedding_record.embedding <=> query_embedding
    LIMIT match_count;
$function$;

COMMIT;
