"""Deployment contract for the Vercel Hobby Git setup."""
import json
from pathlib import Path

import yaml
from fastapi import FastAPI

from app.llm_assistant import ClinicalAssistant
from app.main import app
from app import store as store_module


ROOT = Path(__file__).resolve().parent.parent


def test_vercel_config_targets_asgi_app_and_bundles_runtime_files():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    function = config["functions"]["app/main.py"]
    included = function["includeFiles"]

    assert isinstance(app, FastAPI)
    for pattern in ("app/static/**", "app/templates/**", "config/**", "data/**"):
        assert pattern in included
    for directory in ("app/static", "app/templates", "config", "data/recovery_coach_data"):
        path = ROOT / directory
        assert path.is_dir()
        assert any(item.is_file() for item in path.rglob("*"))


def test_vercel_uses_tmp_database_and_local_path_stays_unchanged(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    assert store_module.default_db_path() == store_module.LOCAL_DB_PATH

    monkeypatch.setenv("VERCEL", "1")
    assert store_module.default_db_path() == Path("/tmp/between_visits.db")


def test_store_resolves_default_path_when_it_is_created(monkeypatch, tmp_path):
    expected = tmp_path / "selected.db"
    monkeypatch.setattr(store_module, "default_db_path", lambda: expected)
    store = store_module.Store()
    try:
        assert store.path == expected
        assert expected.is_file()
    finally:
        store.conn.close()


def test_deployment_defaults_to_free_rules_assistant(monkeypatch):
    monkeypatch.delenv("BETWEEN_VISITS_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    status = ClinicalAssistant().status()
    assert status["provider"] == "deterministic-fallback"
    assert status["model_configured"] is False


def test_ci_has_no_render_hook_or_deploy_job():
    workflow_path = ROOT / ".github/workflows/tests.yml"
    workflow_text = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)

    assert workflow["on"]["push"]["branches"] == ["main"]
    assert set(workflow["jobs"]) == {"test"}
    assert "RENDER_DEPLOY_HOOK_URL" not in workflow_text
    assert not (ROOT / ".github/workflows/test-and-deploy.yml").exists()
    assert not (ROOT / "render.yaml").exists()
    assert not (ROOT / "scripts/deploy_render.py").exists()
