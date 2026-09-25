"""FA-17: 2-second ceiling on how long a DB session/transaction may be held.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-17
(SS9, budget table row FA-17: "long transaction exception").

A connection held past a couple of seconds is the precursor to lock
contention and pool exhaustion under the RPO=0/RTO<=5min operating posture
(SS1 "operational continuity"). This module does not shorten transactions
itself -- it only detects and fails loudly when a caller's session/decorator
scope overruns the ceiling, so the violation surfaces in tests and logs
instead of silently degrading the pool under load.
"""
from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

DEFAULT_SESSION_LIMIT_SECONDS = 2.0

T = TypeVar("T")


class SessionPolicyViolation(Exception):
    """A DB session/transaction was held longer than the FA-17 ceiling."""


class SessionTimeLimit:
    """Context manager enforcing the FA-17 session-duration ceiling.

    Works as both a sync (`with`) and async (`async with`) context manager,
    since the codebase has both asyncpg-driven async callers and sync
    migration/script callers. `clock` defaults to `time.monotonic` (immune
    to wall-clock adjustments) and is injectable so tests can simulate
    elapsed time without a real sleep.

    Raises on `__exit__`/`__aexit__` only when the wrapped body completed
    without its own exception -- a body that already raised propagates that
    exception untouched, since it is the more specific failure.
    """

    def __init__(
        self,
        *,
        limit_seconds: float = DEFAULT_SESSION_LIMIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit_seconds <= 0:
            raise ValueError("limit_seconds must be positive")
        self._limit = limit_seconds
        self._clock = clock
        self._start: float | None = None

    def _enter(self) -> None:
        self._start = self._clock()

    def _exit(self, exc_type: type[BaseException] | None) -> None:
        if self._start is None:
            raise RuntimeError("SessionTimeLimit exited without entering")
        elapsed = self._clock() - self._start
        self._start = None
        if exc_type is None and elapsed > self._limit:
            raise SessionPolicyViolation(
                f"DB session held for {elapsed:.3f}s, exceeding the "
                f"{self._limit:.3f}s FA-17 ceiling"
            )

    def __enter__(self) -> SessionTimeLimit:
        self._enter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self._exit(exc_type)

    async def __aenter__(self) -> SessionTimeLimit:
        self._enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        self._exit(exc_type)


def enforce_session_policy(
    func: Callable[..., Awaitable[T]] | None = None,
    *,
    limit_seconds: float = DEFAULT_SESSION_LIMIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Decorator applying `SessionTimeLimit` around an async function call.

    Usable bare (`@enforce_session_policy`) or parameterized
    (`@enforce_session_policy(limit_seconds=1.0, clock=fake_clock)`).
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            async with SessionTimeLimit(limit_seconds=limit_seconds, clock=clock):
                return await fn(*args, **kwargs)

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
