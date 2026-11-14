from __future__ import annotations

import os

from app.config import _load_backend_ai_environment


def test_backend_env_loads_ai_settings_and_keeps_process_environment(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=test-only-key\nOPENAI_MODEL=gpt-4.1-mini\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    _load_backend_ai_environment(env_file, set())

    assert os.environ["OPENAI_API_KEY"] == "test-only-key"
    assert os.environ["OPENAI_MODEL"] == "gpt-4.1-mini"


def test_backend_env_does_not_override_process_environment_or_blank_key(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=\nOPENAI_MODEL=gpt-4.1-mini\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")

    _load_backend_ai_environment(env_file, {"OPENAI_API_KEY", "OPENAI_MODEL"})

    assert os.environ["OPENAI_API_KEY"] == "environment-key"
    assert os.environ["OPENAI_MODEL"] == "environment-model"
