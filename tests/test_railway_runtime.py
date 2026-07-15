from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime_config import env_bool, env_int

ROOT = Path(__file__).resolve().parents[1]


def test_railway_uses_postgres_predeploy_and_worker():
    config = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    assert config["build"]["builder"] == "RAILPACK"
    assert config["deploy"]["preDeployCommand"] == "python -u db_init.py"
    assert config["deploy"]["startCommand"] == "python -u worker.py"
    assert config["deploy"]["numReplicas"] == 1
    assert config["deploy"]["overlapSeconds"] == 0


def test_worker_runs_original_main_in_a_child_process():
    worker = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert '[sys.executable, "-u", "main.py"]' in worker
    assert "previsione_trading_agent" not in worker
    assert "execute_signal" not in worker


def test_worker_has_postgres_duplicate_cycle_lock():
    worker = (ROOT / "worker.py").read_text(encoding="utf-8")
    assert "pg_try_advisory_lock" in worker
    assert "pg_advisory_unlock" in worker


def test_runtime_configuration_parsing(monkeypatch):
    monkeypatch.setenv("BOOLEAN_FLAG", "true")
    assert env_bool("BOOLEAN_FLAG", False) is True

    monkeypatch.setenv("BOOLEAN_FLAG", "invalid")
    with pytest.raises(ValueError):
        env_bool("BOOLEAN_FLAG", False)

    monkeypatch.setenv("INTERVAL", "59")
    with pytest.raises(ValueError):
        env_int("INTERVAL", 600, minimum=60)


def test_environment_template_contains_no_values_for_secrets():
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "PRIVATE_KEY",
        "WALLET_ADDRESS",
        "OPENAI_API_KEY",
        "CMC_PRO_API_KEY",
        "DATABASE_URL",
    ):
        assert f"{key}=\n" in env_text
