from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "20260921_add_cve_sync_lease.sql"
)


def test_lease_migration_is_atomic_and_recovers_expired_leases():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "lock_name text primary key" in sql
    assert "lock_token uuid not null" in sql
    assert "expires_at timestamptz not null" in sql
    assert "on conflict (lock_name) do update" in sql
    assert "where lease.expires_at <= now()" in sql
    assert "lease.lock_token = requested_token" in sql
    assert "renew_cve_sync_lease" in sql
    assert "release_cve_sync_lease" in sql
    assert "pg_advisory_lock" not in sql
    assert sql.count("revoke all on function") == 3
    assert sql.count("to service_role") == 3
    assert sql.count("security invoker") == 3
    assert sql.count("set search_path = pg_catalog, public") == 3
    assert "security definer" not in sql
