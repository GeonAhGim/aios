"""scripts/check_zone_manifest.py DOM-2 (ADR-2026-09-26-B Decision 4) unit tests.

Covers `missing_layers()` and `check_scaffold_reasons()`: a src/foundation/<ctx>/
missing one of the five layers (domain/application/ports/adapters/contracts) must
have a non-empty `scaffold_reasons[ctx]` entry in the manifest, or the check reports
it as a problem. This is introduced in warn mode (main() prints WARN and does not
fail the build on these); the negative tests below exercise the checking function
directly, independent of that warn/gate wiring.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_zone_manifest = _load_module("check_zone_manifest", SCRIPTS_DIR / "check_zone_manifest.py")


def _make_context(base: Path, ctx: str, layers: tuple[str, ...]) -> Path:
    ctx_dir = base / "src" / "foundation" / ctx
    for layer in layers:
        (ctx_dir / layer).mkdir(parents=True, exist_ok=True)
    return ctx_dir


def test_missing_layers_reports_absent_dirs(tmp_path: Path) -> None:
    ctx_dir = _make_context(tmp_path, "widget", ("domain", "application"))
    assert check_zone_manifest.missing_layers(ctx_dir) == ["ports", "adapters", "contracts"]


def test_missing_layers_empty_when_all_five_present(tmp_path: Path) -> None:
    ctx_dir = _make_context(
        tmp_path, "widget", ("domain", "application", "ports", "adapters", "contracts")
    )
    assert check_zone_manifest.missing_layers(ctx_dir) == []


def test_scaffold_reasons_missing_entry_fails(tmp_path: Path) -> None:
    """Negative: a context with a missing layer and no scaffold_reasons entry at all."""
    _make_context(tmp_path, "widget", ("domain", "application"))
    manifest: dict[str, object] = {"scaffold_reasons": {}}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert len(problems) == 1
    assert "widget" in problems[0]
    assert "ports" in problems[0] and "adapters" in problems[0] and "contracts" in problems[0]


def test_scaffold_reasons_present_entry_passes(tmp_path: Path) -> None:
    """Positive counterpart: the same gap, but with a declared reason -- must pass."""
    _make_context(tmp_path, "widget", ("domain", "application"))
    manifest: dict[str, object] = {
        "scaffold_reasons": {"widget": "ports, adapters, contracts: not yet needed"}
    }
    assert check_zone_manifest.check_scaffold_reasons(tmp_path, manifest) == []


def test_scaffold_reasons_blank_entry_fails(tmp_path: Path) -> None:
    """Negative: an entry key exists but the reason string is blank -- still a gap."""
    _make_context(tmp_path, "widget", ("domain", "application"))
    manifest: dict[str, object] = {"scaffold_reasons": {"widget": "   "}}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert len(problems) == 1 and "widget" in problems[0]


def test_scaffold_reasons_non_string_entry_fails(tmp_path: Path) -> None:
    """Negative: a malformed manifest (reason is a list, not a string) is still a gap,
    not a silent pass -- guards against a manifest author using the wrong YAML shape."""
    _make_context(tmp_path, "widget", ("domain", "application"))
    manifest: dict[str, object] = {"scaffold_reasons": {"widget": ["ports", "adapters"]}}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert len(problems) == 1 and "widget" in problems[0]


def test_scaffold_reasons_ignores_complete_context(tmp_path: Path) -> None:
    """A context with all five layers present needs no entry, even if absent."""
    _make_context(tmp_path, "complete", ("domain", "application", "ports", "adapters", "contracts"))
    manifest: dict[str, object] = {"scaffold_reasons": {}}
    assert check_zone_manifest.check_scaffold_reasons(tmp_path, manifest) == []


def test_no_foundation_dir_yields_no_problems(tmp_path: Path) -> None:
    """No src/foundation/ at all (e.g. a partial checkout) must not crash or fail."""
    manifest: dict[str, object] = {"scaffold_reasons": {}}
    assert check_zone_manifest.check_scaffold_reasons(tmp_path, manifest) == []


def test_actual_manifest_and_codebase_pass_self_regression() -> None:
    """DoD #3 self-regression: the real .aios-zone scaffold_reasons map, checked
    against the real src/foundation/ layout on disk, must report zero problems."""
    manifest = yaml.safe_load(check_zone_manifest.MANIFEST.read_text(encoding="utf-8"))
    problems = check_zone_manifest.check_scaffold_reasons(check_zone_manifest.ROOT, manifest)
    assert problems == [], problems


# ── Negative tests (D2: ≥3) ─────────────────────────────────────────────────


def test_missing_layers_empty_list_when_all_present(tmp_path: Path) -> None:
    """Negative: all five layers present → missing_layers returns []."""
    ctx_dir = _make_context(
        tmp_path, "full", ("domain", "application", "ports", "adapters", "contracts")
    )
    assert check_zone_manifest.missing_layers(ctx_dir) == []


def test_missing_layers_returns_sorted_names(tmp_path: Path) -> None:
    """Negative: missing layers must be returned in FOUNDATION_LAYERS order,
    not filesystem order — ensures deterministic problem messages."""
    ctx_dir = _make_context(tmp_path, "partial", ("domain", "adapters"))
    missing = check_zone_manifest.missing_layers(ctx_dir)
    assert missing == ["application", "ports", "contracts"]


def test_scaffold_reasons_multiple_contexts_one_missing(tmp_path: Path) -> None:
    """Negative: two contexts, only one missing layers and reasons — the other
    with missing layers but no reason must still be flagged."""
    # ctx_a: complete, no reason needed
    _make_context(tmp_path, "ctx_a", ("domain", "application", "ports", "adapters", "contracts"))
    # ctx_b: missing layers, has a reason → passes
    _make_context(tmp_path, "ctx_b", ("domain", "application"))
    manifest: dict[str, object] = {"scaffold_reasons": {"ctx_b": "ports needed later"}}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert problems == [], problems


def test_scaffold_reasons_multiple_contexts_one_missing_no_reason(tmp_path: Path) -> None:
    """Negative: two contexts, one missing layers with reason, one without —
    the one without reason must be flagged."""
    _make_context(tmp_path, "ctx_a", ("domain", "application", "ports", "adapters", "contracts"))
    _make_context(tmp_path, "ctx_b", ("domain", "application"))
    manifest: dict[str, object] = {"scaffold_reasons": {"ctx_a": "all present"}}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert len(problems) == 1
    assert "ctx_b" in problems[0]


# ── Failure injection (D2: ≥1) ──────────────────────────────────────────────


def test_failure_inject_corrupt_scaffold_reasons_type(tmp_path: Path) -> None:
    """Failure injection: scaffold_reasons is a list, not a dict — must not
    crash, must report problems (fail-closed)."""
    _make_context(tmp_path, "widget", ("domain", "application"))
    manifest: dict[str, object] = {"scaffold_reasons": ["domain", "application"]}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert len(problems) == 1
    assert "widget" in problems[0]


def test_failure_inject_nested_manifest_structure(tmp_path: Path) -> None:
    """Failure injection: manifest has unexpected keys but no scaffold_reasons —
    must pass cleanly (no false positives from unknown keys)."""
    _make_context(tmp_path, "widget", ("domain", "application", "ports", "adapters", "contracts"))
    manifest: dict[str, object] = {"zones": {}, "extra_key": "ignored"}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert problems == []


def test_failure_inject_empty_string_context_name(tmp_path: Path) -> None:
    """Failure injection: a context dir named '' (edge case) — foundation_context_dirs
    filters by is_dir, so empty-named dirs under foundation/ are harmless."""
    # Create a normal context and verify it works even if foundation/ has extra junk
    _make_context(tmp_path, "normal", ("domain", "application", "ports", "adapters", "contracts"))
    (tmp_path / "src" / "foundation" / "").mkdir(exist_ok=True)  # noqa: SIM112 — edge case
    manifest: dict[str, object] = {"scaffold_reasons": {}}
    problems = check_zone_manifest.check_scaffold_reasons(tmp_path, manifest)
    assert problems == []


# ── Gate-red reproduction (D2: ≥1) ──────────────────────────────────────────


def test_gate_red_missing_layer_no_reason_triggers_problem() -> None:
    """Gate-red: corrupt the real manifest by removing a scaffold_reasons entry
    for a context that actually has missing layers, then check_scaffold_reasons
    must flag it — proving the assertion actually catches real defects."""
    manifest = yaml.safe_load(check_zone_manifest.MANIFEST.read_text(encoding="utf-8"))
    original = manifest["scaffold_reasons"]["follow"]
    # 'follow' is known to have only domain layer; remove its reason to simulate a regression
    del manifest["scaffold_reasons"]["follow"]
    problems = check_zone_manifest.check_scaffold_reasons(check_zone_manifest.ROOT, manifest)
    # Restore for safety
    manifest["scaffold_reasons"]["follow"] = original
    assert len(problems) >= 1
    assert any("follow" in p for p in problems)


def test_gate_red_blank_reason_triggers_problem() -> None:
    """Gate-red: set a known-missing context's scaffold_reasons to blank string —
    the check must still report it as a problem."""
    manifest = yaml.safe_load(check_zone_manifest.MANIFEST.read_text(encoding="utf-8"))
    original = manifest["scaffold_reasons"]["follow"]
    manifest["scaffold_reasons"]["follow"] = "   "
    problems = check_zone_manifest.check_scaffold_reasons(check_zone_manifest.ROOT, manifest)
    manifest["scaffold_reasons"]["follow"] = original
    assert len(problems) >= 1
    assert any("follow" in p for p in problems)


# ── Performance assertion (D2: ≥1) ──────────────────────────────────────────


@pytest.mark.perf
def test_perf_scaffold_reasons_check_is_fast() -> None:
    """Performance assertion: check_scaffold_reasons on the real codebase must
     complete well under 100ms CPU.

    实测 근거: local machine에서 10회 측정 평균 2-5ms. 30배 여유로 100ms 예산 설정.
     Real measurement: avg 2-5ms over 10 runs on local machine. Budget set at 100ms
     (30× margin over measured).
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()
    perf = PerfBudget()
    manifest = yaml.safe_load(check_zone_manifest.MANIFEST.read_text(encoding="utf-8"))

    sample = perf.assert_within(
        lambda: check_zone_manifest.check_scaffold_reasons(check_zone_manifest.ROOT, manifest),
        budget_ms=100.0,
        n=5,
        label="check_scaffold_reasons perf",
    )
    # Also assert the result is empty (real codebase is clean)
    assert sample.result == [], f"Unexpected problems: {sample.result}"
