from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "db"
    / "migrations"
    / "20260803_risk_assessment_company_isolation.sql"
)


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_exists_and_is_transactional():
    sql = migration_sql().strip()

    assert MIGRATION.is_file()
    assert sql.startswith("BEGIN;")
    assert "COMMIT;" in sql
    assert sql.index("COMMIT;") > sql.index("BEGIN;")


def test_migration_dynamically_resolves_exactly_one_test_company():
    sql = migration_sql()

    assert sql.index("WHERE company_id IS NULL") < sql.index(
        "WHERE company_name = '測試公司'"
    )
    assert "WHERE company_name = '測試公司'" in sql
    assert "target_company_count <> 1" in sql
    assert "target_company_id" in sql
    assert "RAISE EXCEPTION" in sql
    assert "SET company_id = target_company_id" in sql


def test_migration_checks_and_backfills_only_expected_assets():
    sql = migration_sql()

    assert "ELSIF null_asset_count = 5" in sql
    assert "ARRAY[2124, 2130, 2132, 2133, 2134]::bigint[]" in sql
    assert "ARRAY['A003', 'A006', 'A004', 'A005', 'A009']::text[]" in sql
    for mapping in (
        "(2124, 'A003')",
        "(2130, 'A006')",
        "(2132, 'A004')",
        "(2133, 'A005')",
        "(2134, 'A009')",
    ):
        assert mapping in sql
    assert "assets.company_id still contains NULL" in sql


def test_migration_skips_specific_backfill_when_no_assets_are_null():
    sql = migration_sql()

    zero_branch = sql.index("IF null_asset_count = 0 THEN")
    expected_assets_branch = sql.index("ELSIF null_asset_count = 5")
    company_lookup = sql.index("WHERE company_name = '測試公司'")
    backfill = sql.index("SET company_id = target_company_id")

    assert zero_branch < expected_assets_branch < company_lookup < backfill
    assert "IF null_asset_count = 0 THEN\n        NULL;" in sql


def test_migration_rejects_unexpected_null_assets():
    sql = migration_sql()

    assert "ELSE\n        RAISE EXCEPTION" in sql
    assert "Unexpected assets with NULL company_id" in sql
    assert "null_asset_count" in sql
    assert "null_asset_ids" in sql
    assert "null_asset_codes" in sql


def test_migration_hardens_assets_company_scope():
    sql = migration_sql()

    assert "REFERENCES public.companies(id)" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "ALTER COLUMN company_id SET NOT NULL" in sql
    assert "GROUP BY company_id, asset_id_code" in sql
    assert "DROP CONSTRAINT assets_asset_id_code_key" in sql
    assert "UNIQUE (company_id, asset_id_code)" in sql
    assert "assets_company_asset_id_code_key" in sql


def test_migration_adds_asset_id_company_unique_constraint():
    sql = migration_sql()

    assert "assets_id_company_id_key" in sql
    assert "UNIQUE (id, company_id)" in sql
    assert "ARRAY[asset_id_attnum, company_attnum]::smallint[]" in sql
    assert "Constraint assets_id_company_id_key does not match" in sql


def test_migration_adds_and_safely_backfills_risk_company_id():
    sql = migration_sql()

    assert "ALTER TABLE public.risk_assessments" in sql
    assert "ADD COLUMN company_id bigint" in sql
    assert "LEFT JOIN public.assets AS asset" in sql
    assert "assessment.company_id <> asset.company_id" in sql
    assert "SET company_id = asset.company_id" in sql
    assert "risk_assessments.company_id still contains NULL" in sql
    assert "risk_assessments_company_id_fkey" in sql


def test_migration_adds_asset_company_composite_foreign_key():
    sql = migration_sql()

    assert "risk_assessments_asset_company_fkey" in sql
    assert "FOREIGN KEY (asset_id, company_id)" in sql
    assert "REFERENCES public.assets(id, company_id)" in sql
    assert "Conflicting risk_assessments asset/company foreign key" in sql


def test_single_company_fk_checks_do_not_reject_composite_fk_on_rerun():
    sql = migration_sql()

    assert not any(
        line.strip().startswith("AND company_attnum = ANY")
        for line in sql.splitlines()
    )
    assert (
        "constraint_record.conkey = ARRAY[company_attnum]::smallint[]"
        in sql
    )


def test_migration_preserves_single_asset_id_foreign_key():
    sql = migration_sql()

    assert "Preserve the existing single-column risk_assessments.asset_id" in sql
    assert "DROP CONSTRAINT risk_assessments_asset_id_fkey" not in sql
    assert "FOREIGN KEY (asset_id) REFERENCES assets(id)" in sql


def test_migration_adds_company_index_without_removing_other_indexes():
    sql = migration_sql()

    assert "index_record.indkey[0] = company_attnum" in sql
    assert "CREATE INDEX idx_risk_assessments_company_id" in sql
    assert "ON public.risk_assessments(company_id)" in sql
    assert "DROP INDEX" not in sql.upper()


def test_migration_only_deduplicates_equivalent_users_foreign_keys():
    sql = migration_sql()

    assert "users_company_fk" in sql
    assert "users_company_id_fkey" in sql
    assert "legacy.confupdtype = standard.confupdtype" in sql
    assert "legacy.confdeltype = standard.confdeltype" in sql
    assert "legacy.condeferrable = standard.condeferrable" in sql
    assert "legacy.condeferred = standard.condeferred" in sql
    assert "IF constraints_equivalent THEN" in sql
    assert "DROP CONSTRAINT users_company_fk" in sql
    assert "users company foreign keys differ; neither was removed" in sql


def test_migration_includes_read_only_verification_sql_comments():
    sql = migration_sql()

    assert "-- Read-only verification queries" in sql
    for required_check in (
        "-- 1. assets.company_id has no NULL values.",
        "-- 6. UNIQUE(company_id, asset_id_code) exists.",
        "-- 7-10. risk_assessments.company_id exists",
        "-- 12. risk_assessments has a usable index",
        "-- 13. users.company_id still allows NULL.",
        "-- 15. Confirm weight_settings.company_id",
        "-- 16. Confirm audit_logs.asset_id",
        "-- 17. assets has UNIQUE(id, company_id)",
        "-- 18. risk_assessments enforces the asset/company pair",
        "-- 19. No risk assessment company differs",
        "-- 20. The existing single-column risk_assessments.asset_id",
    ):
        assert required_check in sql

    assert "mismatched_assessment_companies" in sql
    assert "WHERE assessment.company_id <> asset.company_id" in sql


def test_migration_does_not_add_rls_or_modify_protected_constraints():
    sql = migration_sql().lower()

    assert "create policy" not in sql
    assert "enable row level security" not in sql
    assert "alter table public.weight_settings" not in sql
    assert "alter table public.audit_logs" not in sql
