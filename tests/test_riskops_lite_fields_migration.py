from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20260920_add_riskops_lite_fields.sql"
)


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_riskops_migration_exists_and_is_transactional():
    sql = migration_sql().strip()

    assert MIGRATION.is_file()
    assert sql.startswith("BEGIN;")
    assert "COMMIT;" in sql


def test_riskops_migration_adds_expected_nullable_columns():
    sql = migration_sql().lower()

    assert "alter table public.risk_assessments" in sql
    for definition in (
        "status text",
        "ai_suggestion text",
        "treatment_note text",
        "treatment_due_date date",
        "evidence_url text",
    ):
        assert f"add column if not exists {definition}" in sql

    assert "not null" not in sql


def test_riskops_migration_is_safe_to_rerun():
    sql = migration_sql().lower()

    assert sql.count("add column if not exists") == 5


def test_riskops_migration_preserves_existing_columns_and_data():
    sql = migration_sql().lower()

    for protected_column in (
        "threat_description",
        "cvss_score",
        "risk_score",
        "risk_level",
        "impact_score",
        "likelihood_score",
        "uploaded_by",
        "company_id",
        "asset_id",
    ):
        assert protected_column not in sql

    assert "drop column" not in sql
    assert "delete from" not in sql
    assert "update public.risk_assessments" not in sql
