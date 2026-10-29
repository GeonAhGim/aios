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


def virtual_totals() -> tuple[float, float]:
    """Seconds of (cpu, wall) delay injected virtually so far in this process."""
    return _virtual_cpu_ns / 1e9, _virtual_wall_ns / 1e9


def _add_virtual(cpu_s: float, wall_s: float) -> None:
    global _virtual_cpu_ns, _virtual_wall_ns
    _virtual_cpu_ns += round(cpu_s * 1e9)
    _virtual_wall_ns += round(wall_s * 1e9)


def virtual_delayed_async[**P, R](
    original: Callable[P, Awaitable[R]], seconds: float
) -> Callable[P, Awaitable[R]]:
    """Like ``delayed_async`` but adds ``seconds`` to the wall reading without sleeping."""

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        _add_virtual(0.0, seconds)
        return await original(*args, **kwargs)

    return wrapper


def virtual_delayed_sync[**P, R](original: Callable[P, R], seconds: float) -> Callable[P, R]:
    """Like ``delayed_sync`` but adds ``seconds`` to the cpu and wall readings without spinning."""

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        _add_virtual(seconds, seconds)
        return original(*args, **kwargs)

    return wrapper


def delayed_async[**P, R](
    original: Callable[P, Awaitable[R]], seconds: float
) -> Callable[P, Awaitable[R]]:
    """Return ``original`` preceded by an ``asyncio.sleep(seconds)`` (wall-clock slowdown)."""

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        await asyncio.sleep(seconds)
        return await original(*args, **kwargs)

    return wrapper


def delayed_sync[**P, R](original: Callable[P, R], seconds: float) -> Callable[P, R]:
    """Return ``original`` preceded by a busy wait of ``seconds`` (CPU-time slowdown)."""

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            pass
        return original(*args, **kwargs)

    return wrapper
