"""task-11242: nested pytest must not recreate its parent's xdist database."""

from __future__ import annotations

import importlib
import os
import subprocess

import pytest


@pytest.mark.parametrize("worker", ["gw0", "gw1", "gw3"])
@pytest.mark.parametrize("case", ["nav", "kis"])
def test_nested_pytest_preserves_parent_database(monkeypatch, tmp_path, worker, case):
    parent_url = "postgresql+asyncpg://localhost/parent_worker"
    monkeypatch.setenv("DATABASE_URL", parent_url)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://localhost/template")
    monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    calls = []

    def run(command, **kwargs):
        env = kwargs["env"]
        assert "PYTEST_XDIST_WORKER" not in env, "child would DROP the parent's database"
        assert env["TEST_DATABASE_URL"] == parent_url
        calls.append(command)
        mutated = len(calls) == 2
        return subprocess.CompletedProcess(
            command, int(mutated), "1 failed" if mutated else "1 passed", ""
        )

    monkeypatch.setattr(subprocess, "run", run)
    if case == "nav":
        module = importlib.import_module(
            "tests.integration.foundation.positions.test_compute_daily_nav_failure_injection"
        )
        module.test_pytest_gate_turns_red_when_verify_chain_call_is_removed(tmp_path)
    else:
        module = importlib.import_module(
            "tests.foundation.unit.market_data.providers.test_kis_provider_deepen"
        )
        module.test_pytest_gate_turns_red_when_br8_websocket_declaration_is_reverted(tmp_path)
    assert len(calls) == 2
    assert os.environ["PYTEST_XDIST_WORKER"] == worker
