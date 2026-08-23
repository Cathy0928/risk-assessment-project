import importlib
import sys
from types import SimpleNamespace


MODULE_NAME = "riskGenie.services.rag_service"


def _import_fresh_rag_service(monkeypatch):
    monkeypatch.delitem(sys.modules, MODULE_NAME, raising=False)
    return importlib.import_module(MODULE_NAME)


def test_import_without_supabase_env_does_not_initialize_clients(monkeypatch):
    import dotenv
    import supabase
    from google import genai
    from riskGenie.services import supabase_client

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda: False)
    calls = []

    def reject_initialization(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("External clients must not initialize on import.")

    monkeypatch.setattr(supabase, "create_client", reject_initialization)
    monkeypatch.setattr(
        supabase_client,
        "get_supabase_client",
        reject_initialization,
    )
    monkeypatch.setattr(genai, "Client", reject_initialization)

    module = _import_fresh_rag_service(monkeypatch)

    assert module.is_gemini_configured() is False
    assert calls == []


def test_search_cve_initializes_supabase_only_when_called(monkeypatch):
    from riskGenie.services import rag_service

    class FakeSupabase:
        def __init__(self):
            self.rpc_calls = []

        def rpc(self, function_name, payload):
            self.rpc_calls.append((function_name, payload))
            return SimpleNamespace(
                execute=lambda: SimpleNamespace(
                    data=[
                        {
                            "cve_id": "CVE-HIGH",
                            "content": "high match",
                            "similarity": 0.91,
                        },
                        {
                            "cve_id": "CVE-LOW",
                            "content": "low match",
                            "similarity": 0.40,
                        },
                    ]
                )
            )

    fake = FakeSupabase()
    client_calls = []
    monkeypatch.setattr(
        rag_service,
        "create_embedding",
        lambda _query: [0.1, 0.2],
    )

    def get_fake_client():
        client_calls.append(True)
        return fake

    monkeypatch.setattr(rag_service, "get_supabase_client", get_fake_client)

    results = rag_service.search_cve(
        "web server",
        match_count=3,
        similarity_threshold=0.65,
    )

    assert client_calls == [True]
    assert fake.rpc_calls == [
        (
            "search_cve",
            {"query_embedding": [0.1, 0.2], "match_count": 3},
        )
    ]
    assert results == [
        {
            "cve_id": "CVE-HIGH",
            "content": "high match",
            "similarity": 0.91,
        }
    ]


def test_search_cve_without_supabase_config_fails_closed(monkeypatch):
    from riskGenie.services import rag_service, supabase_client

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.setattr(
        rag_service,
        "create_embedding",
        lambda _query: [0.1, 0.2],
    )
    monkeypatch.setattr(
        rag_service,
        "get_supabase_client",
        supabase_client.get_supabase_client,
    )

    assert rag_service.search_cve("web server") == []


def test_generate_advice_success_path_is_unchanged(monkeypatch):
    from riskGenie.services import rag_service

    class FakeModels:
        def __init__(self):
            self.calls = []

        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(text="  Apply the recommended patch.  ")

    fake_models = FakeModels()
    monkeypatch.setattr(
        rag_service,
        "search_cve",
        lambda *_args, **_kwargs: [
            {
                "cve_id": "CVE-2026-0001",
                "content": "Relevant vulnerability",
                "similarity": 0.9,
            }
        ],
    )
    monkeypatch.setattr(
        rag_service,
        "_get_gemini_client",
        lambda: SimpleNamespace(models=fake_models),
    )

    advice = rag_service.generate_advice(
        asset_name="Web Server",
        cvss_score=9.8,
        risk_level="High",
    )

    assert advice == "Apply the recommended patch."
    assert len(fake_models.calls) == 1
    assert fake_models.calls[0]["model"] == "gemini-3.1-flash-lite"
