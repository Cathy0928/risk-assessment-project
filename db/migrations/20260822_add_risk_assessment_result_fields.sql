BEGIN;

ALTER TABLE public.risk_assessments
    ADD COLUMN IF NOT EXISTS impact_score double precision,
    ADD COLUMN IF NOT EXISTS likelihood_score double precision,
    ADD COLUMN IF NOT EXISTS risk_level text;

COMMIT;
