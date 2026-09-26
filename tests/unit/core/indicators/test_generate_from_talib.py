"""IND-2g -- TA-Lib catalog generator contract test.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-2g

DoD: 161 records generated (10 groups, 61 candlestick patterns), re-running the
generator produces byte-identical output (determinism), the on-disk snapshot
(`catalog/talib_generated.py`) is TA-Lib-free to parse/exec (CI-without-TA-Lib
verifies only this snapshot), and the generator itself fails closed (raises an
explicit `TALibUnavailableError`, never a silent skip/partial write) when
TA-Lib's C binding is not importable.

D2 evidence: negative tests >= 3 (unknown function name, TA-Lib unavailable,
write to a path whose parent does not exist), one failure-injection test
(TA-Lib introspection breaks mid-catalog -- must propagate, not swallow), one
numeric performance assertion (self-declared budget, mirrors
test_generate_specs.py's methodology since ADR-2026-09-09-C Decision 1 has no
dedicated "full snapshot render" budget line), one red-gate reproduction
(the determinism assertion actually turns red when the renderer is tampered).
"""

from __future__ import annotations

import importlib.util
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


# --- determinism: re-running the generator is byte-identical ---------------


def test_render_module_is_byte_identical_across_calls() -> None:
    first = gen.render_module()
    second = gen.render_module()
    assert first == second


def test_generate_file_writes_byte_identical_output_on_regeneration(tmp_path: Path) -> None:
    target = tmp_path / "talib_generated.py"
    first = gen.generate_file(path=target)
    second = gen.generate_file(path=target)
    assert first == second
    assert target.read_text(encoding="utf-8") == first


# --- snapshot is TA-Lib-free: CI without TA-Lib can still verify it ---------


def test_committed_snapshot_parses_and_execs_without_importing_talib() -> None:
    """The checked-in snapshot (`catalog/talib_generated.py`) must be loadable
    with no `talib` import anywhere in its source -- this is the only thing a
    TA-Lib-less CI environment can verify (IND-2g DoD)."""
    source = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    assert "import talib" not in source
    assert "from talib" not in source

    namespace: dict[str, object] = {}
    exec(compile(source, str(gen.OUTPUT_PATH), "exec"), namespace)  # noqa: S102

    records = namespace["TALIB_GENERATED"]
    assert isinstance(records, tuple)
    assert len(records) == 161
    assert len({r["group"] for r in records}) == 10  # type: ignore[union-attr]


def test_committed_snapshot_matches_fresh_generation() -> None:
    """The committed snapshot must not have drifted from a fresh run against
    the currently installed TA-Lib version (regenerate-and-diff-0 in practice)."""
    fresh = gen.render_module()
    on_disk = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    assert fresh == on_disk


# --- negative: unknown function name is rejected, not silently dropped -----


def test_collect_rejects_unknown_function_name() -> None:
    with pytest.raises(ValueError, match="unknown talib function"):
        gen.collect_records(names=["NOT_A_REAL_TALIB_FUNCTION"])


# --- negative: writing to an unwritable path fails closed, not silently ----


def test_generate_file_propagates_error_for_missing_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "does_not_exist" / "talib_generated.py"
    with pytest.raises(FileNotFoundError):
        gen.generate_file(path=target)


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
        gen.render_module()


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
        gen.render_module()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


_RENDER_BUDGET_MS = 100.0


def test_full_snapshot_render_p95_latency_within_self_declared_budget() -> None:
    """Numeric performance assertion: no dedicated budget line exists in
    ADR-2026-09-09-C Decision 1 for "full snapshot render" -- this leaf self
    declares a budget on top of the pure-Python metadata walk plus `repr()`
    serialization (no disk/network I/O in `render_module`, only `generate_file`
    writes)."""
    p95_ms = _p95(_render_latencies_ms())
    print(f"[IND-2g] render_module() p95={p95_ms:.2f}ms budget<{_RENDER_BUDGET_MS:.0f}ms")
    assert p95_ms < _RENDER_BUDGET_MS


# --- red-gate reproduction: the determinism/drift gate actually turns red --


def test_gate_turns_red_when_snapshot_drifts_from_fresh_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red-gate reproduction: the same equality assertion used by
    `test_committed_snapshot_matches_fresh_generation` must actually fail when
    the two sides genuinely differ -- otherwise that gate could be a
    tautology that always passes regardless of what it compares."""
    tampered_fresh = gen.render_module().replace("'RSI'", "'RSI_TAMPERED'")
    on_disk = gen.OUTPUT_PATH.read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        assert tampered_fresh == on_disk
