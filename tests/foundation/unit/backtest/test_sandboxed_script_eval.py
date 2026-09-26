"""ADR-2026-09-26-B Decision 1 (SBX-2) -- `sandboxed_script_eval.py` tests.

Verifies: (1) a fast script produces a byte-identical result via the sandbox
vs a direct in-process call (no regression), (2) a wall-clock-exceeding
child is killed and reported as `ScriptSandboxTimeoutError` within the
budget + 2s, (3) an RSS-exceeding child is killed and reported as
`ScriptSandboxMemoryExceededError`, (4) a child that crashes for an
unrelated reason (`os._exit`) never hangs the parent and is reported as
`ScriptSandboxCrashError`.

Fixture functions used as the sandboxed callable must be picklable
(`multiprocessing` constraint on Windows' `spawn` start method), so they are
module-level, not closures/lambdas.
"""

from __future__ import annotations

import os
import time

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.ir import IRProgram, lower_program
from src.core.script.runtime.interpreter import execute
from src.core.script.runtime.interpreter_types import ExecutionResult
from src.core.script.runtime.series import Series, Value
from src.foundation.backtest.application.sandboxed_script_eval import (
    SandboxLimits,
    ScriptSandboxCrashError,
    ScriptSandboxMemoryExceededError,
    ScriptSandboxTimeoutError,
    run_sandboxed,
)

_SAMPLE = (
    "input close: series<float> = 0\n"
    "input length: int = 3\n"
    "let doubled = close * 2\n"
    "signal go_long = close > close[1]\n"
    "plot(doubled, 1)\n"
    "order(buy, length, 7) when go_long"
)
_CLOSE = Series.of_floats([1, 3, 2, 5, 1])


def _execute_ir(ir: IRProgram, *, bar_count: int, inputs: dict[str, Value]) -> ExecutionResult:
    return execute(ir, bar_count=bar_count, inputs=inputs)


def _hang_forever() -> None:
    time.sleep(3600)


def _allocate_and_hold(mb: int) -> None:
    block = bytearray(mb * 1024 * 1024)
    block[0] = 1  # force the pages to actually commit, not just be reserved
    time.sleep(10)


def _crash_immediately() -> None:
    os._exit(1)  # noqa: SLF001 -- deliberate hard-crash fixture, not a real callsite


def test_fast_script_matches_direct_call_byte_identical() -> None:
    ir = lower_program(parse(_SAMPLE))
    direct = _execute_ir(ir, bar_count=5, inputs={"close": _CLOSE})
    sandboxed = run_sandboxed(
        _execute_ir,
        ir,
        bar_count=5,
        inputs={"close": _CLOSE},
        limits=SandboxLimits(wallclock_sec=10, rss_mb=512),
    )
    assert sandboxed == direct
    assert sandboxed.bindings == direct.bindings
    assert sandboxed.signals == direct.signals
    assert sandboxed.orders == direct.orders


@pytest.mark.perf
def test_wallclock_limit_exceeded_raises_timeout_error() -> None:
    limit = 1.0
    started = time.monotonic()
    with pytest.raises(ScriptSandboxTimeoutError):
        run_sandboxed(_hang_forever, limits=SandboxLimits(wallclock_sec=limit, rss_mb=512))
    elapsed = time.monotonic() - started
    assert elapsed <= limit + 2.0


def test_rss_limit_exceeded_raises_memory_error() -> None:
    with pytest.raises(ScriptSandboxMemoryExceededError):
        run_sandboxed(
            _allocate_and_hold,
            200,
            limits=SandboxLimits(wallclock_sec=15, rss_mb=50),
        )


@pytest.mark.perf
def test_child_crash_does_not_hang_parent() -> None:
    # Bound is generous (well under the 30s wallclock_sec limit) -- this only
    # asserts the parent does not hang on an unrelated crash, it does not
    # pin down exact process-teardown timing (which varies under pytest's
    # own multiprocessing spawn overhead on Windows).
    started = time.monotonic()
    with pytest.raises(ScriptSandboxCrashError):
        run_sandboxed(_crash_immediately, limits=SandboxLimits(wallclock_sec=30, rss_mb=512))
    elapsed = time.monotonic() - started
    assert elapsed <= 20.0
