import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_missing_weight_fallback_file_loads_as_empty(tmp_path, monkeypatch):
    from riskGenie.services import risk_service

    missing_file = tmp_path / "data" / "weight_settings_fallback.json"
    monkeypatch.setattr(risk_service, "FALLBACK_FILE", str(missing_file))

    assert risk_service.load_fallback_settings() == {}


def test_weight_fallback_save_creates_directory_and_file(tmp_path, monkeypatch):
    from riskGenie.services import risk_service

    def fail_supabase():
        raise RuntimeError("database unavailable")

    fallback_file = tmp_path / "missing-data-dir" / "weight_settings_fallback.json"
    monkeypatch.setattr(risk_service, "get_supabase_client", fail_supabase)
    monkeypatch.setattr(risk_service, "FALLBACK_FILE", str(fallback_file))

    result = risk_service.RiskService.save_weight_settings(
        company_id=7,
        formula_type="weighted_average",
        weight_c=0.4,
        weight_i=0.3,
        weight_a=0.3,
    )

    assert result["success"] is True
    assert result["local_backup_saved"] is True
    assert fallback_file.exists()
    saved = json.loads(fallback_file.read_text(encoding="utf-8"))
    assert saved["7"]["company_id"] == 7


def test_runtime_weight_fallback_file_is_ignored_and_example_is_empty():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    example = ROOT / "riskGenie" / "data" / "weight_settings_fallback.example.json"

    assert "riskGenie/data/weight_settings_fallback.json" in gitignore
    assert json.loads(example.read_text(encoding="utf-8")) == {}
