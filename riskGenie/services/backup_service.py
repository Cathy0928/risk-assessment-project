"""Create sanitized JSON backups of RiskGenie system tables."""

import io
import json
import logging
import zipfile
from datetime import datetime, timezone

try:
    from .supabase_client import get_supabase_admin_client
except ImportError:  # Allows imports when running from inside riskGenie/.
    from supabase_client import get_supabase_admin_client


BACKUP_VERSION = "1.0"
logger = logging.getLogger(__name__)
BACKUP_TABLES = (
    "companies",
    "departments",
    "users",
    "roles",
    "assets",
    "risk_assessments",
    "audit_logs",
    "vulnerabilities",
)
TABLE_SCOPE_POLICIES = {
    "companies": {
        "strategy": "company",
        "column": "id",
    },
    "departments": {
        "strategy": "company",
        "column": "company_id",
    },
    "users": {
        "strategy": "company",
        "column": "company_id",
    },
    # Roles are shared authorization reference data and are not tenant-owned.
    "roles": {
        "strategy": "global_reference",
    },
    "assets": {
        "strategy": "company",
        "column": "company_id",
    },
    "risk_assessments": {
        "strategy": "company_related",
        "column": "company_id",
        "relation_column": "asset_id",
        "source_table": "assets",
        "source_column": "id",
    },
    "audit_logs": {
        "strategy": "related",
        "column": "user_id",
        "source_table": "users",
        "source_column": "id",
    },
    "vulnerabilities": {
        "strategy": "related",
        "column": "id",
        "source_table": "risk_assessments",
        "source_column": "vulnerability_id",
    },
}
SENSITIVE_FIELD_MARKERS = ("password", "secret", "token", "api_key")


class BackupUnavailableError(RuntimeError):
    """Raised when no configured table can be exported."""

    def __init__(self, failed_tables, skipped_tables=None):
        super().__init__("No tables could be exported.")
        self.failed_tables = failed_tables
        self.skipped_tables = skipped_tables or []


class MissingCompanyScopeError(ValueError):
    """Raised when a backup is requested without a tenant company id."""


class TableScopeSkipped(RuntimeError):
    """Raised when a table cannot be safely restricted to the current tenant."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _response_data(response):
    if response is None:
        return None
    if isinstance(response, dict):
        return response.get("data", response)
    return getattr(response, "data", response)


def _is_sensitive_field(field_name):
    normalized = str(field_name).casefold()
    compacted = "".join(character for character in normalized if character.isalnum())
    return any(marker in normalized for marker in SENSITIVE_FIELD_MARKERS) or (
        "apikey" in compacted or "key" in compacted
    )


def sanitize_json(value):
    """Recursively remove sensitive dictionary fields from JSON-compatible data."""
    if isinstance(value, dict):
        return {
            key: sanitize_json(nested_value)
            for key, nested_value in value.items()
            if not _is_sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value


def _safe_error_type(exc):
    error_type = type(exc).__name__
    if not error_type.isidentifier() or len(error_type) > 80:
        return "QueryError"
    return error_type


def _utc_now(now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0)


def _related_values(exported_tables, policy):
    source_table = policy["source_table"]
    if source_table not in exported_tables:
        raise TableScopeSkipped("scope_dependency_unavailable")

    values = []
    for record in exported_tables[source_table]:
        value = record.get(policy["source_column"])
        if value is not None and value not in values:
            values.append(value)

    if not values:
        raise TableScopeSkipped("no_tenant_relation_ids")
    return values


def _query_table(client, table_name, company_id, exported_tables):
    policy = TABLE_SCOPE_POLICIES[table_name]
    strategy = policy["strategy"]
    related_values = None
    if strategy in {"related", "company_related"}:
        related_values = _related_values(exported_tables, policy)

    query = client.table(table_name).select("*")
    if strategy == "company":
        query = query.eq(policy["column"], company_id)
    elif strategy == "company_related":
        query = query.eq(policy["column"], company_id)
    elif strategy == "related":
        query = query.in_(policy["column"], related_values)
    elif strategy != "global_reference":
        raise TableScopeSkipped("unsupported_scope_strategy")

    response = query.execute()
    records = _response_data(response)
    if records is None:
        records = []
    if not isinstance(records, list):
        raise TypeError("Table response data must be a list.")

    if strategy == "company_related":
        allowed_relation_ids = set(related_values)
        consistent_records = [
            record
            for record in records
            if isinstance(record, dict)
            and record.get(policy["column"]) == company_id
            and record.get(policy["relation_column"]) in allowed_relation_ids
        ]
        excluded_count = len(records) - len(consistent_records)
        if excluded_count:
            logger.warning(
                "Excluded %d risk assessment record(s) with inconsistent "
                "company or asset scope.",
                excluded_count,
            )
        records = consistent_records
    return sanitize_json(records)


def _json_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")


def create_backup_archive(generated_by, company_id, now=None):
    """Query configured tables and return a sanitized in-memory ZIP export."""
    if (
        not isinstance(company_id, int)
        or isinstance(company_id, bool)
        or company_id <= 0
    ):
        raise MissingCompanyScopeError("A company scope is required.")

    failed_tables = []
    skipped_tables = []
    exported_tables = {}

    try:
        client = get_supabase_admin_client()
    except Exception as exc:
        error_type = _safe_error_type(exc)
        failed_tables = [
            {"table": table_name, "error_type": error_type}
            for table_name in BACKUP_TABLES
        ]
        raise BackupUnavailableError(failed_tables) from exc

    for table_name in BACKUP_TABLES:
        try:
            exported_tables[table_name] = _query_table(
                client,
                table_name,
                company_id,
                exported_tables,
            )
        except TableScopeSkipped as exc:
            skipped_tables.append(
                {
                    "table": table_name,
                    "reason": exc.reason,
                }
            )
        except Exception as exc:
            error_type = _safe_error_type(exc)
            logger.warning(
                "Backup query failed for table %s (%s).",
                table_name,
                error_type,
            )
            failed_tables.append(
                {
                    "table": table_name,
                    "error_type": error_type,
                }
            )

    if not exported_tables:
        raise BackupUnavailableError(failed_tables, skipped_tables)

    generated_at = _utc_now(now)
    included_tables = list(exported_tables)
    manifest = {
        "backup_version": BACKUP_VERSION,
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "generated_by": generated_by,
        "company_id": company_id,
        "included_tables": included_tables,
        "record_counts": {
            table_name: len(exported_tables[table_name])
            for table_name in included_tables
        },
        "failed_tables": failed_tables,
        "skipped_tables": skipped_tables,
    }

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as backup:
        backup.writestr("backup_manifest.json", _json_bytes(manifest))
        for table_name in included_tables:
            backup.writestr(
                f"{table_name}.json",
                _json_bytes(exported_tables[table_name]),
            )
    archive.seek(0)

    filename_timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return {
        "stream": archive,
        "filename": f"riskgenie_backup_{filename_timestamp}.zip",
        "manifest": manifest,
    }
