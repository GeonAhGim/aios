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
    _make_context(
        tmp_path, "complete", ("domain", "application", "ports", "adapters", "contracts")
    )
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
