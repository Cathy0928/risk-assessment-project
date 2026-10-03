"""
Tests for riskGenie/services/cve_embedding.py's main() sync loop.

Uses a fully in-memory FakeSupabase and FakeGenaiClient — no real
Supabase project, no real Gemini API, no real network or sleeping.
`time.sleep` inside the module is monkeypatched to a no-op so retry/
backoff paths run instantly.
"""

from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from riskGenie.services import cve_embedding  # noqa: E402
from riskGenie.services.cve_change_detector import compute_content_hash  # noqa: E402


EMBEDDING_DIMENSION = cve_embedding.EMBEDDING_DIMENSION


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeCveQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.op = "select"
        self.count_mode = None
        self.range_start = None
        self.range_end = None
        self.upsert_payload = None

    def select(self, _fields, count=None):
        self.op = "select"
        self.count_mode = count
        return self

    def upsert(self, payload, on_conflict=None):
        self.op = "upsert"
        self.upsert_payload = payload
        return self

    def range(self, start, end):
        self.range_start = start
        self.range_end = end
        return self

    def limit(self, _value):
        return self

    def execute(self):
        if self.op == "upsert":
            if self.client.fail_upsert:
                raise Exception("simulated upsert failure")

            for row in self.upsert_payload:
                self.client.tables[self.table_name][row["cve_id"]] = dict(row)

            return FakeResult(data=list(self.upsert_payload))

        rows = list(self.client.tables[self.table_name].values())

        if self.range_start is not None:
            rows = rows[self.range_start:self.range_end + 1]

        count = (
            len(self.client.tables[self.table_name])
            if self.count_mode == "exact"
            else None
        )

        return FakeResult(data=rows, count=count)


class FakeSupabase:
    def __init__(self, cve_documents=None, cve_embeddings=None):
        self.tables = {
            "cve_documents": {
                row["cve_id"]: row for row in (cve_documents or [])
            },
            "cve_embeddings": {
                row["cve_id"]: row for row in (cve_embeddings or [])
            },
        }
        self.fail_upsert = False

    def table(self, name):
        return FakeCveQuery(self, name)


class FakeModels:
    def __init__(self, client):
        self._client = client

    def embed_content(self, model, contents, config):
        self._client.embed_calls.append(contents)

        if self._client.fail_predicate and self._client.fail_predicate(contents):
            raise Exception("simulated embedding failure")

        dimension = config.get("output_dimensionality", EMBEDDING_DIMENSION)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.1] * dimension)]
        )


class FakeGenaiClient:
    def __init__(self, fail_predicate=None):
        self.models = FakeModels(self)
        self.embed_calls = []
        self.fail_predicate = fail_predicate


def cve_document_row(cve_id, description="desc", cvss_score=9.8, severity="CRITICAL", cwe="CWE-79", urls=None):
    return {
        "cve_id": cve_id,
        "description": description,
        "cvss_score": cvss_score,
        "severity": severity,
        "cwe": cwe,
        "reference_urls": urls or [],
    }


@pytest.fixture()
def patched_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setattr(cve_embedding, "load_dotenv", lambda *a, **kw: False)
    monkeypatch.setattr(cve_embedding.time, "sleep", lambda *_a, **_kw: None)


def install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai):
    monkeypatch.setattr(cve_embedding, "create_client", lambda *_a, **_kw: fake_supabase)
    monkeypatch.setattr(cve_embedding.genai, "Client", lambda *_a, **_kw: fake_genai)


def test_new_cve_gets_embedded_and_hash_stored(monkeypatch, patched_env):
    doc = cve_document_row("CVE-2024-0001")
    fake_supabase = FakeSupabase(cve_documents=[doc], cve_embeddings=[])
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()

    stored = fake_supabase.tables["cve_embeddings"]["CVE-2024-0001"]
    assert stored["content_hash"] == compute_content_hash(doc)
    assert len(stored["embedding"]) == EMBEDDING_DIMENSION
    assert fake_genai.embed_calls  # Gemini was actually called for the new CVE


def test_unchanged_cve_is_skipped_and_not_reembedded(monkeypatch, patched_env):
    doc = cve_document_row("CVE-2024-0001")
    existing_hash = compute_content_hash(doc)
    fake_supabase = FakeSupabase(
        cve_documents=[doc],
        cve_embeddings=[
            {
                "cve_id": "CVE-2024-0001",
                "content": "old content text",
                "embedding": [0.9] * EMBEDDING_DIMENSION,
                "content_hash": existing_hash,
            }
        ],
    )
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()

    assert fake_genai.embed_calls == []  # never called Gemini — nothing changed
    stored = fake_supabase.tables["cve_embeddings"]["CVE-2024-0001"]
    assert stored["embedding"] == [0.9] * EMBEDDING_DIMENSION  # untouched


def test_changed_description_triggers_reembedding(monkeypatch, patched_env):
    old_doc = cve_document_row("CVE-2024-0001", description="old description")
    new_doc = cve_document_row("CVE-2024-0001", description="brand new description")
    stale_hash = compute_content_hash(old_doc)

    fake_supabase = FakeSupabase(
        cve_documents=[new_doc],
        cve_embeddings=[
            {
                "cve_id": "CVE-2024-0001",
                "content": "old content text",
                "embedding": [0.9] * EMBEDDING_DIMENSION,
                "content_hash": stale_hash,
            }
        ],
    )
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()

    assert fake_genai.embed_calls  # re-embedded because content changed
    stored = fake_supabase.tables["cve_embeddings"]["CVE-2024-0001"]
    assert stored["content_hash"] == compute_content_hash(new_doc)
    assert stored["content_hash"] != stale_hash


def test_changed_cvss_score_triggers_reembedding(monkeypatch, patched_env):
    old_doc = cve_document_row("CVE-2024-0001", cvss_score=9.8)
    new_doc = cve_document_row("CVE-2024-0001", cvss_score=4.2)
    stale_hash = compute_content_hash(old_doc)

    fake_supabase = FakeSupabase(
        cve_documents=[new_doc],
        cve_embeddings=[
            {
                "cve_id": "CVE-2024-0001",
                "content": "old",
                "embedding": [0.9] * EMBEDDING_DIMENSION,
                "content_hash": stale_hash,
            }
        ],
    )
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()

    assert fake_genai.embed_calls
    assert (
        fake_supabase.tables["cve_embeddings"]["CVE-2024-0001"]["content_hash"]
        != stale_hash
    )


def test_repeated_run_on_unchanged_data_is_a_no_op_second_time(monkeypatch, patched_env):
    doc = cve_document_row("CVE-2024-0001")
    fake_supabase = FakeSupabase(cve_documents=[doc], cve_embeddings=[])
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()
    first_run_calls = len(fake_genai.embed_calls)
    assert first_run_calls == 1

    # Run again with the exact same source data — nothing should change.
    cve_embedding.main()

    assert len(fake_genai.embed_calls) == first_run_calls  # no new calls


def test_embedding_failure_preserves_existing_row_and_does_not_delete_it(
    monkeypatch, patched_env
):
    old_doc = cve_document_row("CVE-2024-0001", description="old description")
    new_doc = cve_document_row("CVE-2024-0001", description="a description that will fail to embed")
    stale_hash = compute_content_hash(old_doc)
    old_embedding = [0.42] * EMBEDDING_DIMENSION

    fake_supabase = FakeSupabase(
        cve_documents=[new_doc],
        cve_embeddings=[
            {
                "cve_id": "CVE-2024-0001",
                "content": "old content",
                "embedding": old_embedding,
                "content_hash": stale_hash,
            }
        ],
    )
    fake_genai = FakeGenaiClient(fail_predicate=lambda _contents: True)
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()

    # Embedding kept failing (MAX_RETRY exhausted) — the previously stored
    # row must be untouched: not deleted, not overwritten with a partial
    # value, and its content_hash is still the OLD (stale) one — meaning
    # the system can still tell this CVE's embedding has not caught up
    # with its current content.
    stored = fake_supabase.tables["cve_embeddings"]["CVE-2024-0001"]
    assert stored["embedding"] == old_embedding
    assert stored["content_hash"] == stale_hash
    assert stored["content_hash"] != compute_content_hash(new_doc)

    # The stale hash is a durable retry marker. Once Gemini recovers,
    # the next full scan must retry and atomically replace the row.
    fake_genai.fail_predicate = None
    cve_embedding.main()

    retried = fake_supabase.tables["cve_embeddings"]["CVE-2024-0001"]
    assert retried["content_hash"] == compute_content_hash(new_doc)
    assert retried["embedding"] != old_embedding


def test_batch_upsert_failure_does_not_lose_existing_data(monkeypatch, patched_env):
    doc = cve_document_row("CVE-2024-0001")
    fake_supabase = FakeSupabase(cve_documents=[doc], cve_embeddings=[])
    fake_supabase.fail_upsert = True
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    cve_embedding.main()  # must not raise even though every upsert fails

    assert "CVE-2024-0001" not in fake_supabase.tables["cve_embeddings"]


def test_max_embeddings_bounds_gemini_calls_and_leaves_remainder_pending(
    monkeypatch, patched_env
):
    documents = [
        cve_document_row(f"CVE-2024-{index:04d}")
        for index in range(20)
    ]
    fake_supabase = FakeSupabase(cve_documents=documents, cve_embeddings=[])
    fake_genai = FakeGenaiClient()
    install_fakes(monkeypatch, patched_env, fake_supabase, fake_genai)

    result = cve_embedding.main(max_embeddings=5)

    assert len(fake_genai.embed_calls) == 5
    assert result["success_count"] == 5
    assert result["limited_count"] == 15
    assert result["complete"] is False
    assert len(fake_supabase.tables["cve_embeddings"]) == 5
