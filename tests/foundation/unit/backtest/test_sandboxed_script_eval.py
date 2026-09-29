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

import psutil
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
    # A zero-filled bytearray is lazily committed page by page on Linux, so
    # only a non-zero fill actually raises RSS by `mb` -- touching one byte
    # would commit a single page and the test would pass only because the
    # child inherited a large heap from its parent.
    block = b"\x01" * (mb * 1024 * 1024)
    assert block[-1] == 1
    time.sleep(10)


def _crash_immediately() -> None:
    os._exit(1)  # noqa: SLF001 -- deliberate hard-crash fixture, not a real callsite


@pytest.mark.perf
def test_fast_script_matches_direct_call_byte_identical() -> None:
    # wallclock_sec is generous (not tight): a `spawn` child re-imports the
    # whole DSL runtime tree from scratch every call (no fork copy-on-write),
    # and on a cold Windows CI checkout (no persisted __pycache__) that
    # bytecode-compile-from-source cost alone can run past a minute --
    # observed ~89s cold vs ~11s warm on the same machine. A tight budget
    # here (originally 10s) turned pure import-speed variance into a
    # `ScriptSandboxTimeoutError` before the equality assertion below ever
    # ran, which is what made this test flaky red on Windows CI while
    # green on Linux (task-8732). This is a correctness test, not a
    # timeout-enforcement test (that is `test_wallclock_limit_exceeded_
    # raises_timeout_error`), so a large budget does not weaken what it
    # verifies.
    ir = lower_program(parse(_SAMPLE))
    direct = _execute_ir(ir, bar_count=5, inputs={"close": _CLOSE})
    sandboxed = run_sandboxed(
        _execute_ir,
        ir,
        bar_count=5,
        inputs={"close": _CLOSE},
        limits=SandboxLimits(wallclock_sec=120, rss_mb=512),
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


@pytest.mark.perf
def test_rss_limit_exceeded_raises_memory_error() -> None:
    # Limit sits well above a fresh spawned child's baseline (~100MB with the
    # DSL runtime imported) so the kill is caused by the allocation, not by
    # interpreter startup. wallclock_sec is generous for the same reason as
    # `test_fast_script_matches_direct_call_byte_identical` -- the spawned
    # child re-imports this whole test module (including the DSL runtime
    # imports at module scope) before `_allocate_and_hold` ever runs, and a
    # cold Windows CI checkout pays the full bytecode-compile cost every
    # single spawn.
    with pytest.raises(ScriptSandboxMemoryExceededError):
        run_sandboxed(
            _allocate_and_hold,
            400,
            limits=SandboxLimits(wallclock_sec=120, rss_mb=256),
        )


@pytest.mark.perf
def test_rss_ceiling_measures_the_script_not_the_calling_process() -> None:
    """Gate-red reproduction (CI run 36237321056, main 16063c7a): a pytest-xdist
    worker whose own RSS had grown past 512MB ran the 5-bar sample script and
    the sandbox killed the child at birth. With the `fork` start method the
    child's RSS is the parent's RSS, so the ceiling measured the host. Hold a
    committed ballast larger than the limit in this process and require the
    tiny script to still run: fails under `fork`, passes under `spawn`."""
    limit_mb = 256
    ballast = b"\x01" * ((limit_mb + 128) * 1024 * 1024)
    try:
        assert ballast[-1] == 1
        parent_rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        assert parent_rss_mb > limit_mb, parent_rss_mb  # precondition of the reproduction
        ir = lower_program(parse(_SAMPLE))
        direct = _execute_ir(ir, bar_count=5, inputs={"close": _CLOSE})
        sandboxed = run_sandboxed(
            _execute_ir,
            ir,
            bar_count=5,
            inputs={"close": _CLOSE},
            limits=SandboxLimits(wallclock_sec=120, rss_mb=limit_mb),
        )
    finally:
        del ballast
    assert sandboxed == direct


@pytest.mark.perf
def test_child_crash_does_not_hang_parent() -> None:
    # Bound is generous (well under the wallclock_sec limit) -- this only
    # asserts the parent does not hang on an unrelated crash, it does not
    # pin down exact process-teardown timing. The spawned child still
    # re-imports the whole test module (DSL runtime included) before
    # `_crash_immediately` runs, so a cold Windows CI checkout's
    # bytecode-compile-from-source cost applies here too even though the
    # fixture itself does nothing (observed 22.3s > a previous 20.0s bound
    # on a cold run -- task-8732); both bounds now carry headroom for that.
    started = time.monotonic()
    with pytest.raises(ScriptSandboxCrashError):
        run_sandboxed(_crash_immediately, limits=SandboxLimits(wallclock_sec=120, rss_mb=512))
    elapsed = time.monotonic() - started
    assert elapsed <= 90.0
