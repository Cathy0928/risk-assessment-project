from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20260920_add_cve_embeddings_content_hash.sql"
)


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_is_marked_superseded_and_must_not_be_applied():
    sql = migration_sql()

    assert MIGRATION.is_file()
    # Production already has content_hash on the real embeddings table
    # (per a read-only Supabase check), so this migration is redundant
    # regardless of naming — it stays superseded.
    assert "SUPERSEDED" in sql
    assert "DO NOT APPLY" in sql


def test_migration_body_targets_confirmed_plural_table():
    # pg_class confirmed the real table is plural cve_embeddings
    # (OID 34266); singular "cve_embedding" does not exist. The body is
    # kept accurate to this even though the file stays unapplied, so it
    # would be a correct reference if ever needed.
    sql = migration_sql().lower()

    assert "alter table public.cve_embeddings" in sql
    assert "alter table public.cve_embedding " not in sql
    assert "add column if not exists content_hash text" in sql
