BEGIN;

-- RiskOps Lite fields used by riskGenie/services/risk_routes.py
-- (save_risk_assessment_api, get_riskops_api, save_riskops_api, ai_advice).
--
-- All columns are added as nullable with no default, matching the existing
-- convention in 20260822_add_risk_assessment_result_fields.sql, so this
-- migration is safe to re-run and does not rewrite or backfill existing rows.
-- Application code already supplies its own defaults / fallbacks
-- (e.g. "status" || '待處理' on read, "待處理" on first insert), so these
-- columns are added without a DB-level default or nullability constraint.

ALTER TABLE public.risk_assessments
    ADD COLUMN IF NOT EXISTS status text,
    ADD COLUMN IF NOT EXISTS ai_suggestion text,
    ADD COLUMN IF NOT EXISTS treatment_note text,
    ADD COLUMN IF NOT EXISTS treatment_due_date date,
    ADD COLUMN IF NOT EXISTS evidence_url text;

COMMIT;

-- Read-only verification query (run manually after applying this migration).
--
-- SELECT column_name, data_type, is_nullable, column_default
-- FROM information_schema.columns
-- WHERE table_schema = 'public' AND table_name = 'risk_assessments'
--   AND column_name IN ('status', 'ai_suggestion', 'treatment_note', 'treatment_due_date', 'evidence_url')
-- ORDER BY column_name;
