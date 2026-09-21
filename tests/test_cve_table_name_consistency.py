"""
Static consistency check: every actual (non-test) reference to the CVE
embeddings table must agree with the schema confirmed via a read-only
pg_class query — table public.cve_embeddings (plural), OID 34266.
Singular "cve_embedding" does not exist as a table.

This exists specifically to catch a regression of the mistake found in
this session: a screenshot was misread as the table being singular, and
that wrong name spread into cve_sync_service.py, a migration meant to
"fix" the already-correct search_cve RPC, and several tests. This test
scans actual source files (not test fixtures, which are free to name
their own local dict keys) for the literal table-name string.
"""

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "riskGenie" / "services"

# Files whose job is specifically to know about both names (e.g. to
# reject the wrong one, or to preserve a superseded migration's
# original — now-corrected-around — body) are exempt from the blanket
# "no singular mention at all" scan below and are checked individually
# instead.
SOURCE_FILES = [
    path
    for path in SERVICES_DIR.glob("*.py")
]


def test_source_modules_only_use_the_plural_confirmed_table_name():
    """No .py file under riskGenie/services/ may hardcode the
    nonexistent singular table name as a Supabase .table(...) argument."""

    offenders = []

    for path in SOURCE_FILES:
        text = path.read_text(encoding="utf-8")
        if '.table("cve_embedding")' in text or ".table('cve_embedding')" in text:
            offenders.append(path.name)

    assert offenders == [], (
        f"Found hardcoded singular .table('cve_embedding') in: {offenders}. "
        "The confirmed real table is plural cve_embeddings (OID 34266)."
    )


def test_cve_embedding_module_constant_is_the_confirmed_plural_name():
    from riskGenie.services import cve_embedding

    assert cve_embedding.EMBEDDING_TABLE == "cve_embeddings"


def test_cve_sync_service_reuses_the_same_table_constant():
    """cve_sync_service.py must not hardcode its own copy of the table
    name (that duplication is exactly how the original bug could recur
    independently in two places) — it must reference
    cve_embedding.EMBEDDING_TABLE."""

    path = SERVICES_DIR / "cve_sync_service.py"
    text = path.read_text(encoding="utf-8")

    assert "cve_embedding.EMBEDDING_TABLE" in text
    assert '.table("cve_embedding")' not in text
    assert ".table('cve_embedding')" not in text


@pytest.mark.parametrize(
    "filename",
    ["cve_embedding.py", "cve_sync_service.py", "nvd_client.py", "cve_change_detector.py"],
)
def test_no_service_module_references_a_nonexistent_singular_cve_embedding_table(
    filename,
):
    path = SERVICES_DIR / filename
    text = path.read_text(encoding="utf-8")

    for line_number, line in enumerate(text.splitlines(), start=1):
        if "cve_embedding" not in line:
            continue
        # Legitimate: the Python module name/import itself, or its
        # attributes (cve_embedding.py, cve_embedding.main, .EMBEDDING_*,
        # .time, .genai, .create_client, .load_dotenv), or prose that
        # explicitly says the singular name does not exist.
        if (
            "cve_embedding.py" in line
            or "cve_embedding.main" in line
            or "cve_embedding.EMBEDDING" in line
            or "cve_embedding.time" in line
            or "cve_embedding.genai" in line
            or "cve_embedding.create_client" in line
            or "cve_embedding.load_dotenv" in line
            or "import cve_embedding" in line
            or "does not exist" in line
            or "does not" in line
        ):
            continue
        # Anything else mentioning "cve_embedding" without the plural
        # "s" is suspect enough to fail loudly and be reviewed by hand.
        assert "cve_embeddings" in line or "cve_embedding " not in line + " ", (
            f"{filename}:{line_number}: possible singular table "
            f"reference needing manual review: {line!r}"
        )
