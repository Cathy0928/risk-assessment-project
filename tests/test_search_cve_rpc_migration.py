from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20260921_fix_search_cve_embedding_table.sql"
)


def migration_sql():
    return MIGRATION.read_text(encoding="utf-8")


def migration_sql_lower():
    return migration_sql().lower()


def test_migration_is_marked_superseded_and_must_not_be_applied():
    sql = migration_sql()

    assert MIGRATION.is_file()
    # A read-only pg_class query confirmed the real embedding table is
    # plural public.cve_embeddings (OID 34266), and that the deployed
    # search_cve RPC already reads "FROM cve_embeddings" correctly —
    # this migration's singular-table premise was simply wrong. Applying
    # it would overwrite a working RPC with a broken one.
    assert "SUPERSEDED" in sql
    assert "DO NOT APPLY" in sql
    assert "DO NOT MODIFY" in sql


def test_migration_body_still_documents_the_original_singular_table_mistake():
    # The SQL body is deliberately left exactly as originally written
    # (per the "preserve original modification record" instruction) —
    # this test documents what it still says, not that it's correct.
    # Scoped to the actual SQL statement, not the prose warning above it
    # (which correctly mentions the real plural table by name).
    statement = migration_sql_lower().split("begin;", 1)[1]

    assert "create or replace function public.search_cve" in statement
    assert "from public.cve_embedding as embedding_record" in statement
    assert "from cve_embeddings" not in statement
    assert "public.cve_embeddings" not in statement


def test_migration_preserves_rpc_contract_shape():
    sql = migration_sql_lower()

    assert "query_embedding vector" in sql
    assert "match_count integer" in sql
    assert "cve_id text" in sql
    assert "content text" in sql
    assert "similarity double precision" in sql
    assert "embedding_record.embedding <=> query_embedding" in sql
    assert "order by embedding_record.embedding <=> query_embedding" in sql


def test_migration_has_no_destructive_or_table_creation_sql():
    sql = migration_sql_lower()

    assert "drop function" not in sql
    assert "drop table" not in sql
    assert "create table" not in sql
    assert "alter table" not in sql
    assert "delete from" not in sql
    assert "truncate" not in sql
    assert "begin;" in sql
    assert sql.strip().endswith("commit;")


def test_migration_does_not_change_function_attributes():
    sql = migration_sql_lower()

    # The original definition omitted these clauses, therefore its
    # effective attributes are PostgreSQL's defaults. Keep them omitted.
    assert " stable" not in sql
    assert " immutable" not in sql
    assert "security definer" not in sql
    assert "parallel safe" not in sql
