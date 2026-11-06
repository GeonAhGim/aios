"""Typed delay injection for gate-red reproduction tests (needs no type-ignore comment).

The ``type: ignore`` budget is a ratchet. Every PERF-detection test used to add one or two
ignores (``no-any-return`` on the wrapped call, ``attr-defined`` on ``monkeypatch.setattr``)
and the cumulative breach (129 -> 134) made pre-push reject unrelated work (2026-10-22).
Use these helpers instead of writing the wrapper by hand.

    monkeypatch.setattr(asyncpg.Connection, "fetch", delayed_async(asyncpg.Connection.fetch, 5.0))
    monkeypatch.setattr(mod, "compute", delayed_sync(mod.compute, 0.5))
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

# Virtual delay ledger: gate-red reproduction tests only need the *measurement* to read as slow,
# not the process to actually sleep/spin (a 10x injection used to cost up to 126s per test).
# ``virtual_delayed_*`` add the delay to this ledger; ``PerfBudget.sample*`` adds whatever accrued
# during a timed call to the sample it returns, so the budget assertion sees the same number a
# real delay would have produced. No injection -> the ledger never moves -> measurements unchanged.
#
# The ledger is kept in integer nanoseconds. Accumulating float seconds made ``after - before`` lose
# precision as the per-process total grew (a 50 ms injection read back as 49.99999999999716 ms and a
# ``>= 50.0`` gate-red assertion failed, 2026-10-29); integer deltas are exact.
_virtual_cpu_ns = 0
_virtual_wall_ns = 0


def virtual_totals_ns() -> tuple[int, int]:
    """Nanoseconds of (cpu, wall) delay injected virtually so far (exact integers)."""
    return _virtual_cpu_ns, _virtual_wall_ns


def reset_virtual_totals() -> None:
    """Zero the virtual delay ledger. Call at the start of gate-red tests
    so they remain idempotent regardless of prior test state."""
    global _virtual_cpu_ns, _virtual_wall_ns
    _virtual_cpu_ns = 0
    _virtual_wall_ns = 0


def virtual_totals() -> tuple[float, float]:
    """Seconds of (cpu, wall) delay injected virtually so far in this process."""
    return _virtual_cpu_ns / 1e9, _virtual_wall_ns / 1e9


def _add_virtual(cpu_s: float, wall_s: float) -> None:
    global _virtual_cpu_ns, _virtual_wall_ns
    _virtual_cpu_ns += round(cpu_s * 1e9)
    _virtual_wall_ns += round(wall_s * 1e9)


def virtual_delayed_async(  # noqa: UP047
    original: Callable[P, Awaitable[R]], seconds: float
) -> Callable[P, Awaitable[R]]:
    """Like ``delayed_async`` but adds ``seconds`` to the wall reading without sleeping."""

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        _add_virtual(0.0, seconds)
        return await original(*args, **kwargs)

    return wrapper


def virtual_delayed_sync(  # noqa: UP047
    original: Callable[P, R], seconds: float
) -> Callable[P, R]:
    """Like ``delayed_sync`` but adds ``seconds`` to the cpu and wall readings without spinning."""

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        _add_virtual(seconds, seconds)
        return original(*args, **kwargs)

    return wrapper


def delayed_async(  # noqa: UP047
    original: Callable[P, Awaitable[R]], seconds: float
) -> Callable[P, Awaitable[R]]:
    """Return ``original`` preceded by an ``asyncio.sleep(seconds)`` (wall-clock slowdown)."""

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        await asyncio.sleep(seconds)
        return await original(*args, **kwargs)

    return wrapper


# Wall-clock safety cap for the CPU spin below: a process starved of CPU entirely must not hang
# the test. 50x is far beyond any contention seen on the CI host (4 busy competitors on one
# core gave ~5x), and a capped spin still leaves the sample slower than the budget in wall terms.
_SPIN_WALL_CAP_FACTOR = 50.0


def delayed_sync(  # noqa: UP047
    original: Callable[P, R], seconds: float
) -> Callable[P, R]:
    """Return ``original`` preceded by a busy wait that burns ``seconds`` of *CPU time*.

    The spin ends when ``time.process_time()`` has advanced by ``seconds`` -- not when the wall
    clock has. Spinning on the wall clock under-injects whenever the process is descheduled: with
    four competitors on the same core a 200 ms injection accrued only 31-172 ms of CPU (idle:
    188-203 ms), so PERF-detection tests asserting "the injected delay breaks the CPU budget"
    raised nothing (``DID NOT RAISE``; ``109.375 > 150``) in full CI runs 106662982 and e001874dc,
    while passing 6/6 on pinned idle cores. Wall time is always >= CPU time, so wall-budget
    assertions still trip.
    """

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        cpu_end = time.process_time() + seconds
        wall_start = time.perf_counter()
        wall_end = wall_start + seconds
        wall_cap = wall_start + seconds * _SPIN_WALL_CAP_FACTOR
        # Both clocks must reach the target: process_time ticks coarsely on Windows (15.6 ms), so
        # it alone can report ``seconds`` of CPU before ``seconds`` of wall time have passed (wall
        # 48.7 ms for a 50 ms injection) and break ``wall >= seconds`` gate-red assertions.
        while (
            time.process_time() < cpu_end or time.perf_counter() < wall_end
        ) and time.perf_counter() < wall_cap:
            pass
        return original(*args, **kwargs)

    return wrapper
