import importlib
import os
import sys
from pathlib import Path

import dotenv
import pytest
from flask import Flask


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ENV = {
    "FLASK_SECRET_KEY": "dotenv-secret",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_ANON_KEY": "dotenv-anon-key",
}


def _fresh_app_import(monkeypatch, dotenv_loader):
    monkeypatch.setattr(dotenv, "load_dotenv", dotenv_loader)
    sys.modules.pop("riskGenie.app", None)
    return importlib.import_module("riskGenie.app")


def test_project_root_dotenv_is_loaded_before_app_startup(monkeypatch):
    calls = []
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)

    def fake_load_dotenv(path, override=False):
        calls.append((Path(path), override))
        for name, value in REQUIRED_ENV.items():
            monkeypatch.setenv(name, value)
        return True

    app_module = _fresh_app_import(monkeypatch, fake_load_dotenv)

    assert app_module.PROJECT_ROOT == ROOT
    assert calls == [(ROOT / ".env", False)]
    assert app_module.app.secret_key == REQUIRED_ENV["FLASK_SECRET_KEY"]


def test_project_dotenv_does_not_override_existing_environment(monkeypatch):
    existing_values = {
        name: f"existing-{index}" for index, name in enumerate(REQUIRED_ENV, start=1)
    }
    for name, value in existing_values.items():
        monkeypatch.setenv(name, value)

    def fake_load_dotenv(_path, override=False):
        for name, value in REQUIRED_ENV.items():
            if override or name not in os.environ:
                monkeypatch.setenv(name, value)
        return True

    _fresh_app_import(monkeypatch, fake_load_dotenv)

    for name, value in existing_values.items():
        assert os.environ[name] == value


def test_missing_required_environment_still_raises(monkeypatch):
    def fake_load_dotenv(_path, override=False):
        assert override is False
        for name, value in REQUIRED_ENV.items():
            monkeypatch.setenv(name, value)
        return True

    app_module = _fresh_app_import(monkeypatch, fake_load_dotenv)
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = False

    with pytest.raises(RuntimeError, match="Missing required environment variables"):
        app_module._validate_runtime_config(flask_app)
