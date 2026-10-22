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


def delayed_async(original: Callable[P, Awaitable[R]], seconds: float) -> Callable[P, Awaitable[R]]:
    """Return ``original`` preceded by an ``asyncio.sleep(seconds)`` (wall-clock slowdown)."""

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        await asyncio.sleep(seconds)
        return await original(*args, **kwargs)

    return wrapper


def delayed_sync(original: Callable[P, R], seconds: float) -> Callable[P, R]:
    """Return ``original`` preceded by a busy wait of ``seconds`` (CPU-time slowdown)."""

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            pass
        return original(*args, **kwargs)

    return wrapper
