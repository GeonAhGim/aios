from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.data.models.base import ProvenanceStatus
from src.data.models.memory import MemoryEntry, MemoryType
from tests.conftest import PerfBudget


def _valid_kwargs(**overrides):
    kwargs = dict(
        memory_type=MemoryType.DECISION,
        content={"action": "buy"},
        source_agent="strategy-agent",
    )
    # Default timestamps for override-friendly base
    base_created = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    kwargs["created_at"] = base_created
    kwargs.update(overrides)
    return kwargs


def test_memory_entry_defaults():
    entry = MemoryEntry(**_valid_kwargs())
    assert entry.status == ProvenanceStatus.UNVERIFIED
    assert entry.confidence == 0.5
    assert entry.verified_at is None


def test_memory_entry_confidence_upper_bound_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(confidence=1.5))


def test_memory_entry_confidence_lower_bound_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(confidence=-0.1))


def test_memory_entry_invalid_memory_type_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(memory_type="NOT_A_REAL_TYPE"))


def test_memory_entry_missing_required_field_raises():
    kwargs = _valid_kwargs()
    del kwargs["source_agent"]
    with pytest.raises(ValidationError):
        MemoryEntry(**kwargs)


def test_memory_entry_content_wrong_type_raises():
    with pytest.raises(ValidationError):
        MemoryEntry(**_valid_kwargs(content="not-a-dict"))


def test_memory_entry_dependency_failure_propagates_fail_closed(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(MemoryEntry, "__init__", boom)
    with pytest.raises(RuntimeError):
        MemoryEntry(**_valid_kwargs())


def test_memory_verified_at_before_created_at_raises():
    """negative: verified_at cannot precede created_at (provenance ordering invariant)."""
    created = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    verified = datetime(2026, 9, 30, 11, 59, 59, tzinfo=timezone.utc)  # 1s earlier
    with pytest.raises(ValidationError):
        MemoryEntry(
            **_valid_kwargs(created_at=created, verified_at=verified),
        )


def test_memory_verified_by_requires_verified_status():
    """negative: setting verified_by without VERIFIED status is rejected."""
    with pytest.raises(ValidationError):
        MemoryEntry(
            **_valid_kwargs(verified_by="auditor-agent", status=ProvenanceStatus.UNVERIFIED),
        )


def test_memory_uuid4_failure_propagates(monkeypatch):
    """failure-injection: uuid4() exception propagates instead of being swallowed.

    Pydantic v2's compiled validator captures default_factory at class-definition
    time, so we can't patch it after import. Instead we run a subprocess with
    uuid4 patched before the model is imported.
    """

    import subprocess
    import sys

    code = """
import uuid
uuid.uuid4 = lambda *a, **k: (_ for _ in ()).throw(OSError("UUID generation service unavailable"))
from src.data.models.memory import MemoryEntry
MemoryEntry(memory_type="DECISION", content={"action": "buy"}, source_agent="test")
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "Expected OSError from uuid4 failure"
    assert "UUID generation service unavailable" in result.stderr, (
        f"Expected error message not found in stderr: {result.stderr}"
    )


@pytest.mark.perf
def test_memory_entry_construction_throughput(perf_budget: PerfBudget):
    perf_budget.assert_within(
        lambda: MemoryEntry(**_valid_kwargs()),
        budget_ms=10.0,
        batch=32,
    )


# -- D2 0 수렴 검증 -------------------------------------------------------------


@pytest.mark.perf
def test_memory_entry_construction_cpu_converges_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """Single `MemoryEntry` construction must consume ~0 CPU ms — a pathological
    validator or heavy default_factory would register measurable CPU even for
    one call. 100 samples, best-of-N minimum must stay below 1 ms (a wide floor
    for single-call Pydantic model overhead)."""

    def one_call() -> None:
        MemoryEntry(**_valid_kwargs())

    sample = perf_budget.best_of(one_call, n=100, batch=1)
    assert sample.cpu_ms < 1.0, (
        f"single-call CPU={sample.cpu_ms:.3f}ms exceeds 1ms floor — "
        f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
    )


# -- D2 게이트 적색 재현 -------------------------------------------------------


@pytest.mark.perf
def test_memory_entry_construction_budget_detects_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """Prove the budget assertion actually fires red: wrap `MemoryEntry`
    construction in a function that burns CPU (busy-loop), then assert the
    measurement exceeds the budget. This is the gate-red reproduction — if the
    assertion logic is broken (e.g. always passes), this test will incorrectly
    green. Uses CPU-bound work because `time.process_time()` ignores sleep."""

    injected_calls: list[int] = [0]

    def cpu_heavy_call() -> None:
        injected_calls[0] += 1
        # Busy-loop to burn ~5ms of real CPU time.
        total = 0
        for _ in range(5_000_000):
            total += 1
        _ = total  # prevent optimisation away
        MemoryEntry(**_valid_kwargs())

    # Budget 1 ms — a 5ms busy-loop must exceed it.
    sample = perf_budget.best_of(cpu_heavy_call, n=3, batch=1)
    assert sample.cpu_ms >= 1.0, (
        f"delay-injection gate-red failed: cpu={sample.cpu_ms:.3f}ms "
        f"should be >= 1ms — assertion did not catch the injected CPU load"
    )


def test_memory_entry_construction_budget_assertion_catches_violation() -> None:
    """Verify that `PerfBudget.assert_within` raises on budget violation.
    Monkey-patch `time.process_time` to return incrementing values that
    simulate a 10x budget overrun, proving the assertion fires correctly."""
    import time as _time

    original_process_time = _time.process_time
    call_counter: list[int] = [0]

    def fake_process_time() -> float:
        call_counter[0] += 1
        # Simulate each call consuming 5ms worth of CPU time
        return call_counter[0] * 0.005

    _time.process_time = fake_process_time
    try:
        # Budget 0.001 ms — fake clock makes each call ~5ms, must exceed.
        with pytest.raises(AssertionError):
            _pb = PerfBudget()
            _pb.assert_within(
                lambda: MemoryEntry(**_valid_kwargs()),
                budget_ms=0.001,
                n=1,
                batch=1,
            )
    finally:
        _time.process_time = original_process_time
