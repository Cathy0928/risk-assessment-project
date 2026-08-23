from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20260822_add_risk_assessment_result_fields.sql"
)


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_result_fields_migration_exists_and_is_transactional():
    sql = migration_sql().strip()

    assert MIGRATION.is_file()
    assert sql.startswith("BEGIN;")
    assert sql.endswith("COMMIT;")


def test_result_fields_migration_adds_expected_nullable_columns():
    sql = migration_sql().lower()

    assert "alter table public.risk_assessments" in sql
    for definition in (
        "impact_score double precision",
        "likelihood_score double precision",
        "risk_level text",
    ):
        assert f"add column if not exists {definition}" in sql

    assert "not null" not in sql
    assert "default" not in sql


def test_result_fields_migration_is_safe_to_rerun():
    sql = migration_sql().lower()

    assert sql.count("add column if not exists") == 3


def test_result_fields_migration_preserves_existing_columns_and_data():
    sql = migration_sql().lower()

    for protected_column in (
        "threat_description",
        "cvss_score",
        "risk_score",
        "uploaded_by",
    ):
        assert protected_column not in sql

    assert "drop column" not in sql
    assert "delete from" not in sql
    assert "update public.risk_assessments" not in sql
