BEGIN;

DO $migration$
DECLARE
    target_company_id bigint;
    target_company_count integer;
    null_asset_count integer;
    null_asset_ids bigint[];
    null_asset_codes text[];
    updated_asset_count integer;
BEGIN
    SELECT
        COUNT(*),
        COALESCE(array_agg(id ORDER BY id), ARRAY[]::bigint[]),
        COALESCE(array_agg(asset_id_code ORDER BY id), ARRAY[]::text[])
    INTO null_asset_count, null_asset_ids, null_asset_codes
    FROM public.assets
    WHERE company_id IS NULL;

    IF null_asset_count = 0 THEN
        NULL;
    ELSIF null_asset_count = 5
          AND null_asset_ids = ARRAY[2124, 2130, 2132, 2133, 2134]::bigint[]
          AND null_asset_codes = ARRAY['A003', 'A006', 'A004', 'A005', 'A009']::text[] THEN
        SELECT COUNT(*), MIN(id)
        INTO target_company_count, target_company_id
        FROM public.companies
        WHERE company_name = '測試公司';

        IF target_company_count <> 1 THEN
            RAISE EXCEPTION
                'Expected exactly one company named 測試公司, found %.',
                target_company_count;
        END IF;

        UPDATE public.assets
        SET company_id = target_company_id
        WHERE company_id IS NULL
          AND (id, asset_id_code) IN (
              (2124, 'A003'),
              (2130, 'A006'),
              (2132, 'A004'),
              (2133, 'A005'),
              (2134, 'A009')
          );

        GET DIAGNOSTICS updated_asset_count = ROW_COUNT;
        IF updated_asset_count <> 5 THEN
            RAISE EXCEPTION
                'Expected to backfill 5 assets, updated %.',
                updated_asset_count;
        END IF;
    ELSE
        RAISE EXCEPTION
            'Unexpected assets with NULL company_id: count=%, ids=%, codes=%.',
            null_asset_count,
            null_asset_ids,
            null_asset_codes;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.assets
        WHERE company_id IS NULL
    ) THEN
        RAISE EXCEPTION 'assets.company_id still contains NULL values after backfill.';
    END IF;
END
$migration$;

DO $migration$
DECLARE
    company_attnum smallint;
    company_id_attnum smallint;
    equivalent_fk_count integer;
    conflicting_fk_definitions text;
BEGIN
    SELECT attnum
    INTO company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    SELECT attnum
    INTO company_id_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.companies'::regclass
      AND attname = 'id'
      AND NOT attisdropped;

    SELECT string_agg(
        format('%I: %s', constraint_record.conname,
               pg_catalog.pg_get_constraintdef(constraint_record.oid, true)),
        '; '
    )
    INTO conflicting_fk_definitions
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.assets'::regclass
      AND constraint_record.contype = 'f'
      AND constraint_record.conkey = ARRAY[company_attnum]::smallint[]
      AND NOT (
          constraint_record.conkey = ARRAY[company_attnum]::smallint[]
          AND constraint_record.confrelid = 'public.companies'::regclass
          AND constraint_record.confkey = ARRAY[company_id_attnum]::smallint[]
          AND constraint_record.confdeltype = 'r'
      );

    IF conflicting_fk_definitions IS NOT NULL THEN
        RAISE EXCEPTION
            'Conflicting assets.company_id foreign key(s): %.',
            conflicting_fk_definitions;
    END IF;

    SELECT COUNT(*)
    INTO equivalent_fk_count
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.assets'::regclass
      AND constraint_record.contype = 'f'
      AND constraint_record.conkey = ARRAY[company_attnum]::smallint[]
      AND constraint_record.confrelid = 'public.companies'::regclass
      AND constraint_record.confkey = ARRAY[company_id_attnum]::smallint[]
      AND constraint_record.confdeltype = 'r';

    IF equivalent_fk_count = 0 THEN
        ALTER TABLE public.assets
            ADD CONSTRAINT assets_company_id_fkey
            FOREIGN KEY (company_id)
            REFERENCES public.companies(id)
            ON DELETE RESTRICT;
    END IF;
END
$migration$;

ALTER TABLE public.assets
    ALTER COLUMN company_id SET NOT NULL;

DO $migration$
DECLARE
    company_attnum smallint;
    asset_code_attnum smallint;
    duplicate_details text;
    old_constraint_oid oid;
    old_constraint_type "char";
    old_constraint_keys smallint[];
    composite_unique_count integer;
BEGIN
    SELECT attnum
    INTO company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    SELECT attnum
    INTO asset_code_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'asset_id_code'
      AND NOT attisdropped;

    SELECT string_agg(
        format('company_id=%s asset_id_code=%L count=%s',
               duplicate.company_id,
               duplicate.asset_id_code,
               duplicate.row_count),
        '; '
    )
    INTO duplicate_details
    FROM (
        SELECT company_id, asset_id_code, COUNT(*) AS row_count
        FROM public.assets
        GROUP BY company_id, asset_id_code
        HAVING COUNT(*) > 1
    ) AS duplicate;

    IF duplicate_details IS NOT NULL THEN
        RAISE EXCEPTION
            'Duplicate asset_id_code values exist within a company: %.',
            duplicate_details;
    END IF;

    SELECT oid, contype, conkey
    INTO old_constraint_oid, old_constraint_type, old_constraint_keys
    FROM pg_catalog.pg_constraint
    WHERE conrelid = 'public.assets'::regclass
      AND conname = 'assets_asset_id_code_key';

    IF old_constraint_oid IS NOT NULL THEN
        IF old_constraint_type <> 'u'
           OR old_constraint_keys <> ARRAY[asset_code_attnum]::smallint[] THEN
            RAISE EXCEPTION
                'Constraint assets_asset_id_code_key does not match UNIQUE(asset_id_code).';
        END IF;

        ALTER TABLE public.assets
            DROP CONSTRAINT assets_asset_id_code_key;
    END IF;

    SELECT COUNT(*)
    INTO composite_unique_count
    FROM pg_catalog.pg_constraint
    WHERE conrelid = 'public.assets'::regclass
      AND contype = 'u'
      AND conkey = ARRAY[company_attnum, asset_code_attnum]::smallint[];

    IF composite_unique_count = 0 THEN
        ALTER TABLE public.assets
            ADD CONSTRAINT assets_company_asset_id_code_key
            UNIQUE (company_id, asset_id_code);
    END IF;
END
$migration$;

DO $migration$
DECLARE
    asset_id_attnum smallint;
    company_attnum smallint;
    named_constraint_oid oid;
    named_constraint_type "char";
    named_constraint_keys smallint[];
    equivalent_unique_count integer;
BEGIN
    SELECT attnum
    INTO asset_id_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'id'
      AND NOT attisdropped;

    SELECT attnum
    INTO company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    SELECT oid, contype, conkey
    INTO named_constraint_oid, named_constraint_type, named_constraint_keys
    FROM pg_catalog.pg_constraint
    WHERE conrelid = 'public.assets'::regclass
      AND conname = 'assets_id_company_id_key';

    IF named_constraint_oid IS NOT NULL
       AND (
           named_constraint_type <> 'u'
           OR named_constraint_keys
              <> ARRAY[asset_id_attnum, company_attnum]::smallint[]
       ) THEN
        RAISE EXCEPTION
            'Constraint assets_id_company_id_key does not match UNIQUE(id, company_id).';
    END IF;

    SELECT COUNT(*)
    INTO equivalent_unique_count
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.assets'::regclass
      AND constraint_record.contype = 'u'
      AND constraint_record.conkey
          = ARRAY[asset_id_attnum, company_attnum]::smallint[];

    IF equivalent_unique_count = 0 THEN
        ALTER TABLE public.assets
            ADD CONSTRAINT assets_id_company_id_key
            UNIQUE (id, company_id);
    END IF;
END
$migration$;

DO $migration$
DECLARE
    company_column_type text;
    orphan_count integer;
    mismatch_count integer;
BEGIN
    SELECT pg_catalog.format_type(attribute.atttypid, attribute.atttypmod)
    INTO company_column_type
    FROM pg_catalog.pg_attribute AS attribute
    WHERE attribute.attrelid = 'public.risk_assessments'::regclass
      AND attribute.attname = 'company_id'
      AND NOT attribute.attisdropped;

    IF company_column_type IS NULL THEN
        ALTER TABLE public.risk_assessments
            ADD COLUMN company_id bigint;
    ELSIF company_column_type <> 'bigint' THEN
        RAISE EXCEPTION
            'risk_assessments.company_id must be bigint, found %.',
            company_column_type;
    END IF;

    SELECT COUNT(*)
    INTO orphan_count
    FROM public.risk_assessments AS assessment
    LEFT JOIN public.assets AS asset
      ON asset.id = assessment.asset_id
    WHERE asset.id IS NULL
       OR asset.company_id IS NULL;

    IF orphan_count <> 0 THEN
        RAISE EXCEPTION
            'Found % orphan risk assessment(s) or assessment(s) linked to an unscoped asset.',
            orphan_count;
    END IF;

    SELECT COUNT(*)
    INTO mismatch_count
    FROM public.risk_assessments AS assessment
    JOIN public.assets AS asset
      ON asset.id = assessment.asset_id
    WHERE assessment.company_id IS NOT NULL
      AND assessment.company_id <> asset.company_id;

    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Found % risk assessment(s) whose company_id conflicts with the linked asset.',
            mismatch_count;
    END IF;

    UPDATE public.risk_assessments AS assessment
    SET company_id = asset.company_id
    FROM public.assets AS asset
    WHERE assessment.asset_id = asset.id
      AND assessment.company_id IS NULL;

    IF EXISTS (
        SELECT 1
        FROM public.risk_assessments
        WHERE company_id IS NULL
    ) THEN
        RAISE EXCEPTION 'risk_assessments.company_id still contains NULL values after backfill.';
    END IF;
END
$migration$;

DO $migration$
DECLARE
    company_attnum smallint;
    company_id_attnum smallint;
    equivalent_fk_count integer;
    conflicting_fk_definitions text;
BEGIN
    SELECT attnum
    INTO company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.risk_assessments'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    SELECT attnum
    INTO company_id_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.companies'::regclass
      AND attname = 'id'
      AND NOT attisdropped;

    SELECT string_agg(
        format('%I: %s', constraint_record.conname,
               pg_catalog.pg_get_constraintdef(constraint_record.oid, true)),
        '; '
    )
    INTO conflicting_fk_definitions
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.risk_assessments'::regclass
      AND constraint_record.contype = 'f'
      AND constraint_record.conkey = ARRAY[company_attnum]::smallint[]
      AND NOT (
          constraint_record.conkey = ARRAY[company_attnum]::smallint[]
          AND constraint_record.confrelid = 'public.companies'::regclass
          AND constraint_record.confkey = ARRAY[company_id_attnum]::smallint[]
          AND constraint_record.confdeltype = 'r'
      );

    IF conflicting_fk_definitions IS NOT NULL THEN
        RAISE EXCEPTION
            'Conflicting risk_assessments.company_id foreign key(s): %.',
            conflicting_fk_definitions;
    END IF;

    SELECT COUNT(*)
    INTO equivalent_fk_count
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.risk_assessments'::regclass
      AND constraint_record.contype = 'f'
      AND constraint_record.conkey = ARRAY[company_attnum]::smallint[]
      AND constraint_record.confrelid = 'public.companies'::regclass
      AND constraint_record.confkey = ARRAY[company_id_attnum]::smallint[]
      AND constraint_record.confdeltype = 'r';

    IF equivalent_fk_count = 0 THEN
        ALTER TABLE public.risk_assessments
            ADD CONSTRAINT risk_assessments_company_id_fkey
            FOREIGN KEY (company_id)
            REFERENCES public.companies(id)
            ON DELETE RESTRICT;
    END IF;
END
$migration$;

ALTER TABLE public.risk_assessments
    ALTER COLUMN company_id SET NOT NULL;

-- Preserve the existing single-column risk_assessments.asset_id foreign key.
DO $migration$
DECLARE
    assessment_asset_attnum smallint;
    assessment_company_attnum smallint;
    asset_id_attnum smallint;
    asset_company_attnum smallint;
    named_constraint_oid oid;
    named_constraint_type "char";
    named_constraint_keys smallint[];
    named_constraint_reference oid;
    named_constraint_reference_keys smallint[];
    named_constraint_delete_action "char";
    named_constraint_definition text;
    equivalent_fk_count integer;
    conflicting_fk_definitions text;
    mismatch_count integer;
BEGIN
    SELECT attnum
    INTO assessment_asset_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.risk_assessments'::regclass
      AND attname = 'asset_id'
      AND NOT attisdropped;

    SELECT attnum
    INTO assessment_company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.risk_assessments'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    SELECT attnum
    INTO asset_id_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'id'
      AND NOT attisdropped;

    SELECT attnum
    INTO asset_company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.assets'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    SELECT
        oid,
        contype,
        conkey,
        confrelid,
        confkey,
        confdeltype,
        pg_catalog.pg_get_constraintdef(oid, true)
    INTO
        named_constraint_oid,
        named_constraint_type,
        named_constraint_keys,
        named_constraint_reference,
        named_constraint_reference_keys,
        named_constraint_delete_action,
        named_constraint_definition
    FROM pg_catalog.pg_constraint
    WHERE conrelid = 'public.risk_assessments'::regclass
      AND conname = 'risk_assessments_asset_company_fkey';

    IF named_constraint_oid IS NOT NULL
       AND NOT (
           named_constraint_type = 'f'
           AND named_constraint_keys
               = ARRAY[
                   assessment_asset_attnum,
                   assessment_company_attnum
               ]::smallint[]
           AND named_constraint_reference = 'public.assets'::regclass
           AND named_constraint_reference_keys
               = ARRAY[asset_id_attnum, asset_company_attnum]::smallint[]
           AND named_constraint_delete_action = 'r'
       ) THEN
        RAISE EXCEPTION
            'Constraint risk_assessments_asset_company_fkey conflicts with the required definition: %.',
            named_constraint_definition;
    END IF;

    SELECT string_agg(
        format('%I: %s', constraint_record.conname,
               pg_catalog.pg_get_constraintdef(constraint_record.oid, true)),
        '; '
    )
    INTO conflicting_fk_definitions
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.risk_assessments'::regclass
      AND constraint_record.contype = 'f'
      AND array_length(constraint_record.conkey, 1) = 2
      AND assessment_asset_attnum = ANY (constraint_record.conkey)
      AND assessment_company_attnum = ANY (constraint_record.conkey)
      AND NOT (
          constraint_record.conkey
              = ARRAY[
                  assessment_asset_attnum,
                  assessment_company_attnum
              ]::smallint[]
          AND constraint_record.confrelid = 'public.assets'::regclass
          AND constraint_record.confkey
              = ARRAY[asset_id_attnum, asset_company_attnum]::smallint[]
          AND constraint_record.confdeltype = 'r'
      );

    IF conflicting_fk_definitions IS NOT NULL THEN
        RAISE EXCEPTION
            'Conflicting risk_assessments asset/company foreign key(s): %.',
            conflicting_fk_definitions;
    END IF;

    SELECT COUNT(*)
    INTO mismatch_count
    FROM public.risk_assessments AS assessment
    JOIN public.assets AS asset
      ON asset.id = assessment.asset_id
    WHERE assessment.company_id <> asset.company_id;

    IF mismatch_count <> 0 THEN
        RAISE EXCEPTION
            'Found % risk assessment(s) whose company_id conflicts with the linked asset.',
            mismatch_count;
    END IF;

    SELECT COUNT(*)
    INTO equivalent_fk_count
    FROM pg_catalog.pg_constraint AS constraint_record
    WHERE constraint_record.conrelid = 'public.risk_assessments'::regclass
      AND constraint_record.contype = 'f'
      AND constraint_record.conkey
          = ARRAY[
              assessment_asset_attnum,
              assessment_company_attnum
          ]::smallint[]
      AND constraint_record.confrelid = 'public.assets'::regclass
      AND constraint_record.confkey
          = ARRAY[asset_id_attnum, asset_company_attnum]::smallint[]
      AND constraint_record.confdeltype = 'r';

    IF equivalent_fk_count = 0 THEN
        ALTER TABLE public.risk_assessments
            ADD CONSTRAINT risk_assessments_asset_company_fkey
            FOREIGN KEY (asset_id, company_id)
            REFERENCES public.assets(id, company_id)
            ON DELETE RESTRICT;
    END IF;
END
$migration$;

DO $migration$
DECLARE
    company_attnum smallint;
BEGIN
    SELECT attnum
    INTO company_attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = 'public.risk_assessments'::regclass
      AND attname = 'company_id'
      AND NOT attisdropped;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_index AS index_record
        WHERE index_record.indrelid = 'public.risk_assessments'::regclass
          AND index_record.indisvalid
          AND index_record.indisready
          AND index_record.indpred IS NULL
          AND index_record.indexprs IS NULL
          AND index_record.indnkeyatts >= 1
          AND index_record.indkey[0] = company_attnum
    ) THEN
        CREATE INDEX idx_risk_assessments_company_id
            ON public.risk_assessments(company_id);
    END IF;
END
$migration$;

DO $migration$
DECLARE
    legacy_constraint_oid oid;
    standard_constraint_oid oid;
    constraints_equivalent boolean;
    legacy_definition text;
    standard_definition text;
BEGIN
    SELECT oid
    INTO legacy_constraint_oid
    FROM pg_catalog.pg_constraint
    WHERE conrelid = 'public.users'::regclass
      AND conname = 'users_company_fk';

    SELECT oid
    INTO standard_constraint_oid
    FROM pg_catalog.pg_constraint
    WHERE conrelid = 'public.users'::regclass
      AND conname = 'users_company_id_fkey';

    IF legacy_constraint_oid IS NOT NULL
       AND standard_constraint_oid IS NOT NULL THEN
        SELECT
            legacy.contype = 'f'
            AND standard.contype = 'f'
            AND legacy.conrelid = standard.conrelid
            AND legacy.conkey = standard.conkey
            AND legacy.confrelid = standard.confrelid
            AND legacy.confkey = standard.confkey
            AND legacy.confmatchtype = standard.confmatchtype
            AND legacy.confupdtype = standard.confupdtype
            AND legacy.confdeltype = standard.confdeltype
            AND legacy.condeferrable = standard.condeferrable
            AND legacy.condeferred = standard.condeferred
            AND legacy.convalidated = standard.convalidated,
            pg_catalog.pg_get_constraintdef(legacy.oid, true),
            pg_catalog.pg_get_constraintdef(standard.oid, true)
        INTO constraints_equivalent, legacy_definition, standard_definition
        FROM pg_catalog.pg_constraint AS legacy
        CROSS JOIN pg_catalog.pg_constraint AS standard
        WHERE legacy.oid = legacy_constraint_oid
          AND standard.oid = standard_constraint_oid;

        IF constraints_equivalent THEN
            ALTER TABLE public.users
                DROP CONSTRAINT users_company_fk;
        ELSE
            RAISE WARNING
                'users company foreign keys differ; neither was removed. users_company_fk=%, users_company_id_fkey=%.',
                legacy_definition,
                standard_definition;
        END IF;
    END IF;
END
$migration$;

COMMIT;

-- Read-only verification queries (run manually after applying this migration).
--
-- 1. assets.company_id has no NULL values.
-- SELECT COUNT(*) AS null_assets FROM public.assets WHERE company_id IS NULL;
--
-- 2. assets.company_id is NOT NULL.
-- SELECT is_nullable FROM information_schema.columns
-- WHERE table_schema = 'public' AND table_name = 'assets' AND column_name = 'company_id';
--
-- 3-4. assets.company_id references companies(id), with RESTRICT/NO ACTION delete behavior.
-- SELECT c.conname, pg_get_constraintdef(c.oid, true) AS definition, c.confdeltype
-- FROM pg_catalog.pg_constraint AS c
-- WHERE c.conrelid = 'public.assets'::regclass AND c.contype = 'f'
--   AND pg_get_constraintdef(c.oid, true) LIKE 'FOREIGN KEY (company_id)%REFERENCES companies(id)%';
--
-- 5. The old global UNIQUE(asset_id_code) constraint is gone.
-- SELECT conname, pg_get_constraintdef(oid, true) FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.assets'::regclass AND conname = 'assets_asset_id_code_key';
--
-- 6. UNIQUE(company_id, asset_id_code) exists.
-- SELECT conname, pg_get_constraintdef(oid, true) FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.assets'::regclass AND contype = 'u'
--   AND pg_get_constraintdef(oid, true) = 'UNIQUE (company_id, asset_id_code)';
--
-- 7-10. risk_assessments.company_id exists, is bigint, has no NULL values, and is NOT NULL.
-- SELECT data_type, is_nullable FROM information_schema.columns
-- WHERE table_schema = 'public' AND table_name = 'risk_assessments' AND column_name = 'company_id';
-- SELECT COUNT(*) AS null_risk_assessments
-- FROM public.risk_assessments WHERE company_id IS NULL;
--
-- 11. risk_assessments.company_id references companies(id).
-- SELECT c.conname, pg_get_constraintdef(c.oid, true) AS definition, c.confdeltype
-- FROM pg_catalog.pg_constraint AS c
-- WHERE c.conrelid = 'public.risk_assessments'::regclass AND c.contype = 'f'
--   AND pg_get_constraintdef(c.oid, true) LIKE 'FOREIGN KEY (company_id)%REFERENCES companies(id)%';
--
-- 12. risk_assessments has a usable index beginning with company_id.
-- SELECT indexname, indexdef FROM pg_catalog.pg_indexes
-- WHERE schemaname = 'public' AND tablename = 'risk_assessments'
--   AND indexdef ~ '\(company_id([, )])';
--
-- 13. users.company_id still allows NULL.
-- SELECT is_nullable FROM information_schema.columns
-- WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'company_id';
--
-- 14. Inspect remaining users.company_id foreign keys for exact duplicates.
-- SELECT conname, pg_get_constraintdef(oid, true), confupdtype, confdeltype,
--        condeferrable, condeferred
-- FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.users'::regclass AND contype = 'f'
--   AND pg_get_constraintdef(oid, true) LIKE 'FOREIGN KEY (company_id)%';
--
-- 15. Confirm weight_settings.company_id foreign key and UNIQUE remain unchanged.
-- SELECT conname, contype, pg_get_constraintdef(oid, true)
-- FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.weight_settings'::regclass
--   AND pg_get_constraintdef(oid, true) LIKE '%company_id%';
--
-- 16. Confirm audit_logs.asset_id still uses ON DELETE SET NULL.
-- SELECT conname, pg_get_constraintdef(oid, true)
-- FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.audit_logs'::regclass AND contype = 'f'
--   AND pg_get_constraintdef(oid, true) LIKE 'FOREIGN KEY (asset_id)%ON DELETE SET NULL';
--
-- 17. assets has UNIQUE(id, company_id) for the composite foreign key target.
-- SELECT conname, pg_get_constraintdef(oid, true)
-- FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.assets'::regclass AND contype = 'u'
--   AND pg_get_constraintdef(oid, true) = 'UNIQUE (id, company_id)';
--
-- 18. risk_assessments enforces the asset/company pair with ON DELETE RESTRICT.
-- SELECT conname, pg_get_constraintdef(oid, true)
-- FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.risk_assessments'::regclass AND contype = 'f'
--   AND pg_get_constraintdef(oid, true)
--       = 'FOREIGN KEY (asset_id, company_id) REFERENCES assets(id, company_id) ON DELETE RESTRICT';
--
-- 19. No risk assessment company differs from its linked asset company.
-- SELECT COUNT(*) AS mismatched_assessment_companies
-- FROM public.risk_assessments AS assessment
-- JOIN public.assets AS asset ON asset.id = assessment.asset_id
-- WHERE assessment.company_id <> asset.company_id;
--
-- 20. The existing single-column risk_assessments.asset_id foreign key remains.
-- SELECT conname, pg_get_constraintdef(oid, true)
-- FROM pg_catalog.pg_constraint
-- WHERE conrelid = 'public.risk_assessments'::regclass AND contype = 'f'
--   AND pg_get_constraintdef(oid, true)
--       LIKE 'FOREIGN KEY (asset_id) REFERENCES assets(id)%';
