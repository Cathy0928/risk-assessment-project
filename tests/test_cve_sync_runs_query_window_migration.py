from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20260921_add_cve_sync_runs_query_window.sql"
)


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_is_marked_ready_but_not_applied_here():
    sql = migration_sql()

    assert MIGRATION.is_file()
    assert "READY FOR REVIEW" in sql
    assert "DO NOT APPLY FROM THIS WORKSPACE" in sql


def test_migration_is_transactional_and_safe_to_rerun():
    sql = migration_sql().strip()

    assert sql.startswith("-- =")  # leads with the review warning block
    assert "BEGIN;" in sql
    assert sql.endswith("COMMIT;")

    # Only count occurrences inside the actual ALTER TABLE statement, not
    # the prose warning above it (which also uses this phrase).
    statement = sql.split("BEGIN;", 1)[1]
    assert statement.lower().count("add column if not exists") == 2


def test_migration_only_adds_nullable_query_window_columns():
    sql = migration_sql().lower()

    assert "alter table public.cve_sync_runs" in sql
    assert "add column if not exists query_start_date timestamptz" in sql
    assert "add column if not exists query_end_date timestamptz" in sql
    assert "not null" not in sql
    assert "drop column" not in sql
    assert "delete from" not in sql
    assert "update public.cve_sync_runs" not in sql
