"""CM-21 / CM-A2 D2 depth evidence for the rule-purity guard (supplements
`test_rule_purity.py`, which is left untouched).

Adds: more negative cases for the transitive import scanner, failure
injection into the evaluator, gate-red reproductions (broken inputs must turn
the guard red) and a perf assertion with a 10x virtual-delay red repro.

Perf budget: one full sweep of the 7 rules + policy functions + evaluator
(`_run_all_domain_rules_and_evaluator`) measured best-of-20 wall ~= 0.08 ms locally
(see `_SWEEP_BUDGET_MS`); the AST import-graph scan of the guarded tree is
~= 26 ms. Budgets below carry >=30x headroom over those measurements.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.foundation.mandates.contracts.v1 import ComplianceVerdict, RuleHit
from src.foundation.mandates.domain.evaluator import evaluate_bundle
from src.foundation.mandates.domain.rule_bundle import RuleBundle, RuleSpec
from tests.adversarial.compliance.test_rule_purity import (
    _NOW,
    BANNED_MODULES,
    GUARDED_PATHS,
    ROOT,
    NetworkAccessError,
    _fake_tree,
    _install_io_traps,
    _run_all_domain_rules_and_evaluator,
    matches_banned,
    scan_import_graph,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_RULES_DIR = ("src/foundation/mandates/domain/rules",)

# Measured locally: ~0.08 ms per sweep; ~26 ms per graph scan (budgets >=30x headroom).
_SWEEP_BUDGET_MS = 50.0
_SCAN_BUDGET_MS = 2000.0


# --- negative -----------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "from socket import create_connection\n",
        "import httpx._client\n",
        "from anthropic.types import Message\n",
        "import openai.resources\n",
    ],
)
def test_negative_submodule_and_from_imports_are_flagged(tmp_path: Path, source: str) -> None:
    root = _fake_tree(tmp_path, source)
    violations, _ = scan_import_graph(root, _RULES_DIR, BANNED_MODULES)
    assert violations, f"scanner missed {source!r}"


@pytest.mark.parametrize("name", ["socketserver", "httpx_mock", "openai_compat", "anthropic2"])
def test_negative_lookalike_module_names_are_not_false_positives(name: str) -> None:
    assert matches_banned(name, BANNED_MODULES) is None


def test_negative_non_src_import_chain_is_not_followed(tmp_path: Path) -> None:
    # Third-party modules are opaque leaves: only `src.*` edges are traversed.
    root = _fake_tree(tmp_path, "import decimal\nimport requests_like\n")
    violations, reached = scan_import_graph(root, _RULES_DIR, BANNED_MODULES)
    assert violations == []
    assert root / "src/services/helper.py" in reached


def test_negative_guard_over_empty_tree_reaches_nothing(tmp_path: Path) -> None:
    (tmp_path / "src/foundation/mandates/domain/rules").mkdir(parents=True)
    violations, reached = scan_import_graph(tmp_path, _RULES_DIR, BANNED_MODULES)
    assert violations == []
    assert reached == set()


# --- failure injection ----------------------------------------------------------


def test_failure_injection_rule_raising_non_io_error_denies_fail_closed() -> None:
    def _boom(_p: Mapping[str, Any], _s: Mapping[str, Any]) -> RuleHit | None:
        raise ValueError("injected rule failure")

    bundle = RuleBundle(version="fi-v1", rules=(RuleSpec(rule_id="BOOM", params={}, check=_boom),))
    decision = evaluate_bundle(bundle, {}, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY


def test_failure_injection_socket_trap_inside_rule_is_recorded_and_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _install_io_traps(monkeypatch, calls)

    def _dial(_p: Mapping[str, Any], _s: Mapping[str, Any]) -> RuleHit | None:
        socket.create_connection(("example.invalid", 443))
        return None

    bundle = RuleBundle(version="fi-v2", rules=(RuleSpec(rule_id="DIAL", params={}, check=_dial),))
    decision = evaluate_bundle(bundle, {}, now=_NOW)
    assert decision.verdict == ComplianceVerdict.DENY
    assert calls == ["socket.create_connection"]


def test_failure_injection_llm_module_attribute_access_is_trapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    calls: list[str] = []
    _install_io_traps(monkeypatch, calls)
    with pytest.raises(NetworkAccessError):
        sys.modules["anthropic"].Anthropic  # noqa: B018 - attribute access is the probe
    assert calls == ["anthropic.Anthropic"]


# --- gate red reproduction ---------------------------------------------------------


def test_gate_red_direct_banned_import_in_guarded_file_is_caught(tmp_path: Path) -> None:
    root = _fake_tree(tmp_path, "")
    rule = root / "src/foundation/mandates/domain/rules/restricted_list.py"
    rule.write_text("import httpx\n", encoding="utf-8")
    violations, _ = scan_import_graph(root, _RULES_DIR, BANNED_MODULES)
    assert [v[0] for v in violations] == ["src/foundation/mandates/domain/rules/restricted_list.py"]


def test_gate_red_runtime_sweep_turns_red_when_http_call_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    _install_io_traps(monkeypatch, calls)
    _run_all_domain_rules_and_evaluator()
    assert calls == []  # green baseline

    from src.foundation.mandates.domain.rules import leverage

    original = leverage.check

    def _dirty(params: Mapping[str, Any], snapshot: Mapping[str, Any]) -> RuleHit | None:
        httpx.AsyncClient()
        return original(params, snapshot)

    monkeypatch.setattr(leverage, "check", _dirty)
    with pytest.raises(NetworkAccessError):
        _run_all_domain_rules_and_evaluator()
    assert calls == ["httpx.AsyncClient.__init__"]


def test_gate_red_missing_guarded_path_is_detected() -> None:
    broken = (*GUARDED_PATHS, "src/foundation/mandates/domain/does_not_exist.py")
    missing = [rel for rel in broken if not (ROOT / rel).exists()]
    assert missing == ["src/foundation/mandates/domain/does_not_exist.py"]


# --- perf -------------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_rule_sweep_within_budget(perf_budget: PerfBudget) -> None:
    reset_virtual_totals()
    sample = perf_budget.best_of(_run_all_domain_rules_and_evaluator, n=5, warmup=1)
    assert sample.cpu_ms < _SWEEP_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_SWEEP_BUDGET_MS
    )


@pytest.mark.perf
def test_perf_rule_sweep_gate_red_with_10x_injection(perf_budget: PerfBudget) -> None:
    reset_virtual_totals()
    slow = virtual_delayed_sync(_run_all_domain_rules_and_evaluator, _SWEEP_BUDGET_MS * 10 / 1000)
    sample = perf_budget.best_of(slow, n=5, warmup=1)
    assert not (sample.cpu_ms < _SWEEP_BUDGET_MS)
    reset_virtual_totals()


@pytest.mark.perf
def test_perf_import_graph_scan_within_budget(perf_budget: PerfBudget) -> None:
    reset_virtual_totals()
    sample = perf_budget.best_of(
        lambda: scan_import_graph(ROOT, GUARDED_PATHS, BANNED_MODULES), n=3, warmup=1
    )
    assert sample.cpu_ms < _SCAN_BUDGET_MS, perf_budget.describe(sample, budget_ms=_SCAN_BUDGET_MS)
