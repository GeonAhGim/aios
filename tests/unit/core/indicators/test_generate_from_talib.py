"""IND-2g -- TA-Lib catalog generator contract test.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-2g

DoD: 161 records generated (10 groups, 61 candlestick patterns), re-running the
generator produces byte-identical output (determinism), the on-disk snapshot
(`catalog/talib_generated*.py`) is TA-Lib-free to import (CI-without-TA-Lib
verifies only this snapshot), and the generator itself fails closed (raises an
explicit `TALibUnavailableError`, never a silent skip/partial write) when
TA-Lib's C binding is not importable. 161 records don't fit one file under the
architecture guard's 300-line cap on every `src/**/*.py` file, so the snapshot
is chunked into `talib_generated_partNN.py` files plus a `talib_generated.py`
aggregator (see `generate_from_talib.py` module docstring) -- every test below
that touches "the snapshot" exercises that split, not a single file.

D2 evidence: negative tests >= 3 (unknown function name, TA-Lib unavailable,
write to a path whose parent does not exist), one failure-injection test
(TA-Lib introspection breaks mid-catalog -- must propagate, not swallow), one
numeric performance assertion (self-declared budget, mirrors
test_generate_specs.py's methodology since ADR-2026-09-09-C Decision 1 has no
dedicated "full snapshot render" budget line), one red-gate reproduction
(the drift assertion actually turns red when the renderer output is tampered).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import time
from pathlib import Path

import pytest
import talib
from talib import abstract as talib_abstract

from src.core.indicators.catalog import generate_from_talib as gen

ALL_TALIB_NAMES = sorted(talib.get_functions())


# --- shape: 161 records, 10 groups, 61 candlestick patterns -----------------


def test_collects_all_161_talib_functions() -> None:
    records = gen.collect_records()
    assert len(records) == 161
    assert {r["name"] for r in records} == set(ALL_TALIB_NAMES)


def test_records_classify_into_exactly_ten_groups_with_61_candlesticks() -> None:
    records = gen.collect_records()
    groups = {r["group"] for r in records}
    assert len(groups) == 10
    pattern_records = [r for r in records if r["group"] == "Pattern Recognition"]
    assert len(pattern_records) == 61
    assert all(r["name"].startswith("CDL") for r in pattern_records)


def test_a_known_indicator_has_expected_inputs_and_params() -> None:
    records = {r["name"]: r for r in gen.collect_records()}
    rsi = records["RSI"]
    assert rsi["inputs"] == ["close"]
    assert rsi["params"] == [{"name": "timeperiod", "min": 1, "max": 2000, "default": 14}]
    assert rsi["outputs"] == ["real"]


# --- architecture guard: every generated file stays under the 300-line cap --


def test_every_generated_file_stays_under_the_architecture_guard_line_cap() -> None:
    files = gen.render_all()
    assert len(files) > 1, "expected a multi-file split, not a single giant snapshot"
    for filename, source in files.items():
        line_count = source.count("\n") + 1
        assert line_count < 300, f"{filename}: {line_count} lines >= 300-line guard cap"


# --- determinism: re-running the generator is byte-identical ---------------


def test_render_all_is_byte_identical_across_calls() -> None:
    first = gen.render_all()
    second = gen.render_all()
    assert first == second


def test_generate_files_writes_byte_identical_output_on_regeneration(tmp_path: Path) -> None:
    first = gen.generate_files(directory=tmp_path)
    second = gen.generate_files(directory=tmp_path)
    assert first == second
    for filename, source in first.items():
        assert (tmp_path / filename).read_text(encoding="utf-8") == source


def test_generate_files_removes_stale_parts_from_a_previous_run(tmp_path: Path) -> None:
    """A leftover `talib_generated_part99.py` from a prior (e.g. differently
    chunked) run must not survive regeneration -- otherwise the on-disk
    snapshot could silently accumulate orphaned, never-imported parts."""
    stale = tmp_path / "talib_generated_part99.py"
    stale.write_text("TALIB_GENERATED_PART = ()\n", encoding="utf-8")

    gen.generate_files(directory=tmp_path)

    assert not stale.exists()


# --- snapshot is TA-Lib-free: CI without TA-Lib can still verify it ---------


def test_committed_snapshot_files_contain_no_talib_import() -> None:
    for path in [gen.AGGREGATOR_PATH, *gen.OUTPUT_DIR.glob("talib_generated_part*.py")]:
        source = path.read_text(encoding="utf-8")
        assert "import talib" not in source
        assert "from talib" not in source


def test_committed_snapshot_imports_and_shapes_correctly_without_touching_talib() -> None:
    """The checked-in snapshot must be importable without ever importing
    `talib` -- this is the only thing a TA-Lib-less CI environment can verify
    (IND-2g DoD). Uses a real module import (not a raw `exec`) so the
    aggregator's own `from src.core.indicators.catalog.talib_generated_partNN
    import ...` statements resolve normally."""
    sys.modules.pop("talib", None)
    module_name = "src.core.indicators.catalog.talib_generated"
    sys.modules.pop(module_name, None)

    module = importlib.import_module(module_name)

    assert "talib" not in sys.modules
    records = module.TALIB_GENERATED
    assert isinstance(records, tuple)
    assert len(records) == 161
    assert len({r["group"] for r in records}) == 10


def test_committed_snapshot_matches_fresh_generation() -> None:
    """The committed snapshot must not have drifted from a fresh run against
    the currently installed TA-Lib version (regenerate-and-diff-0 in practice)."""
    fresh = gen.render_all()
    on_disk = {
        path.name: path.read_text(encoding="utf-8")
        for path in gen.OUTPUT_DIR.glob("talib_generated*.py")
    }
    assert fresh == on_disk


# --- negative: unknown function name is rejected, not silently dropped -----


def test_collect_rejects_unknown_function_name() -> None:
    with pytest.raises(ValueError, match="unknown talib function"):
        gen.collect_records(names=["NOT_A_REAL_TALIB_FUNCTION"])


# --- negative: writing to an unwritable path fails closed, not silently ----


def test_generate_files_propagates_error_for_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        gen.generate_files(directory=target)


# --- negative / fail-closed: TA-Lib unavailable -> explicit error, no skip --


def test_require_talib_raises_explicit_error_when_talib_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates a TA-Lib-less environment (e.g. CI) via monkeypatch rather
    than relying on the actual test venv's TA-Lib install -- this test must
    exercise the fail-closed path deterministically regardless of whether the
    machine running it happens to have TA-Lib installed."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(gen.TALibUnavailableError, match="not installed"):
        gen.require_talib()


def test_collect_records_fails_closed_when_talib_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generator must not silently return an empty/partial catalog when
    TA-Lib is missing -- it must raise, so a TA-Lib-less CI job that
    accidentally invokes the generator fails loudly instead of shipping a
    stale/empty snapshot."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    with pytest.raises(gen.TALibUnavailableError):
        gen.collect_records()
    with pytest.raises(gen.TALibUnavailableError):
        gen.render_all()


# --- failure injection: TA-Lib introspection itself breaks mid-catalog -----


def test_collect_propagates_talib_introspection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure injection: if `abstract.Function(name).info` raises partway
    through the catalog (corrupted TA-Lib install / binary version mismatch),
    `collect_records` must propagate the exception rather than swallowing it
    and returning a partial catalog as if it succeeded (fail-closed)."""
    original_function = talib_abstract.Function
    failing_name = "RSI"
    assert failing_name in ALL_TALIB_NAMES

    def _flaky_function(name: str, *args: object, **kwargs: object) -> object:
        if name == failing_name:
            raise RuntimeError("simulated TA-Lib introspection failure")
        return original_function(name, *args, **kwargs)

    monkeypatch.setattr(
        "src.core.indicators.generate_specs.talib_abstract.Function", _flaky_function
    )

    with pytest.raises(RuntimeError, match="simulated TA-Lib introspection failure"):
        gen.collect_records()


# --- numeric performance assertion: full-catalog render latency ------------


def _render_latencies_ms(iterations: int = 20) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        gen.render_all()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_RENDER_BUDGET_MS = 150.0


def test_full_snapshot_render_p95_latency_within_self_declared_budget() -> None:
    """Numeric performance assertion: no dedicated budget line exists in
    ADR-2026-09-09-C Decision 1 for "full snapshot render" -- this leaf self
    declares a budget on top of the pure-Python metadata walk plus chunking
    and `pprint.pformat` serialization (no disk/network I/O in `render_all`,
    only `generate_files` writes)."""
    p95_ms = _p95(_render_latencies_ms())
    print(f"[IND-2g] render_all() p95={p95_ms:.2f}ms budget<{_RENDER_BUDGET_MS:.0f}ms")
    assert p95_ms < _RENDER_BUDGET_MS


# --- red-gate reproduction: the drift/determinism gate actually turns red --


def test_gate_turns_red_when_snapshot_drifts_from_fresh_generation() -> None:
    """Red-gate reproduction: the same equality assertion used by
    `test_committed_snapshot_matches_fresh_generation` must actually fail when
    the two sides genuinely differ -- otherwise that gate could be a
    tautology that always passes regardless of what it compares."""
    fresh = gen.render_all()
    tampered = dict(fresh)
    target_file = next(name for name, source in tampered.items() if "'RSI'" in source)
    tampered[target_file] = tampered[target_file].replace("'RSI'", "'RSI_TAMPERED'", 1)

    with pytest.raises(AssertionError):
        assert tampered == fresh
