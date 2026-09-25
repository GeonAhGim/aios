"""FA-18: multi-host DB failover -- read-only detection, backoff reconnect.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-18
(SS7 SLO: "failover -> first successful write <= 60s"; SS6 failure-mode
table: "write during failover -> read-only detection -> reconnect/retry
(idempotent), local halt on lease expiry").

Real HA topology (Patroni, streaming replication promotion, etc.) is
deployment/ops scope (SS10 "미확정"; ADR-2026-09-06-B) -- this module only
guarantees the *code* path reconnects to a writable host within the FA-18
ceiling once one exists, via an injectable `connect` port so tests can drive
a simulated failover without a real Postgres cluster.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol


class FailoverExhaustedError(Exception):
    """No configured host produced a writable connection within the retry ceiling."""


class DbConnection(Protocol):
    async def is_read_only(self) -> bool: ...

    async def close(self) -> None: ...


ConnectFn = Callable[[str], Awaitable[DbConnection]]


@dataclass(frozen=True)
class HostEndpoint:
    """One candidate host in the multi-host connection list.

    `priority` breaks ties deterministically (lower tried first) so the
    manager always attempts the primary before replicas, matching the
    intended topology instead of an arbitrary dict/set ordering.
    """

    host: str
    priority: int = 0


@dataclass(frozen=True)
class BackoffPolicy:
    """Exponential backoff between full host-list sweeps, capped by the
    FA-18 60s "first successful write" ceiling (SS7)."""

    initial_seconds: float = 0.1
    max_seconds: float = 5.0
    multiplier: float = 2.0
    ceiling_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.initial_seconds <= 0 or self.max_seconds <= 0 or self.ceiling_seconds <= 0:
            raise ValueError("backoff seconds must be positive")
        if self.multiplier <= 1.0:
            raise ValueError("multiplier must be > 1.0 to actually back off")

    def delays(self) -> Iterator[float]:
        delay = self.initial_seconds
        while True:
            yield delay
            delay = min(delay * self.multiplier, self.max_seconds)


class FailoverConnectionManager:
    """Holds a multi-host list, detects read-only (demoted/standby) hosts,
    and reconnects with backoff until a writable host is found or the
    FA-18 ceiling elapses.

    `connect`/`clock`/`sleep` are injectable ports (I-10 wiring-proof
    pattern also used by `session_policy.SessionTimeLimit`): production
    wires a real asyncpg connect + `time.monotonic` + `asyncio.sleep`,
    tests wire fakes to simulate a promotion/failover deterministically.
    """

    def __init__(
        self,
        hosts: Sequence[HostEndpoint],
        *,
        connect: ConnectFn,
        backoff: BackoffPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not hosts:
            raise ValueError("at least one host is required")
        self._hosts = sorted(hosts, key=lambda h: h.priority)
        self._connect = connect
        self._backoff = backoff or BackoffPolicy()
        self._clock = clock
        self._sleep = sleep
        self._conn: DbConnection | None = None
        self._current_host: str | None = None

    @property
    def current_host(self) -> str | None:
        return self._current_host

    async def get_writable_connection(self) -> DbConnection:
        """Return a connection confirmed writable (`is_read_only()` False).

        Sweeps `_hosts` in priority order; a host that fails to connect or
        answers read-only is skipped (its connection, if any, is closed
        immediately -- fail-closed, never held open speculatively). If no
        host in a sweep is writable, sleeps for the next backoff delay and
        sweeps again, until `backoff.ceiling_seconds` elapses, at which
        point it raises `FailoverExhaustedError` rather than retrying
        forever in a hot write path.
        """
        deadline = self._clock() + self._backoff.ceiling_seconds
        delays = self._backoff.delays()
        last_error: Exception | None = None
        while True:
            for host in self._hosts:
                try:
                    conn = await self._connect(host.host)
                except Exception as exc:  # noqa: BLE001 -- any driver failure (refused/timeout/DNS) means "try the next host", not a crash
                    last_error = exc
                    continue
                try:
                    read_only = await conn.is_read_only()
                except Exception as exc:  # noqa: BLE001 -- a probe failure must not be trusted as writable; fail closed to the next host
                    await conn.close()
                    last_error = exc
                    continue
                if read_only:
                    await conn.close()
                    continue
                self._conn = conn
                self._current_host = host.host
                return conn
            if self._clock() >= deadline:
                tried = [h.host for h in self._hosts]
                raise FailoverExhaustedError(
                    f"no writable host among {tried} within "
                    f"{self._backoff.ceiling_seconds:.0f}s"
                ) from last_error
            await self._sleep(next(delays))

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._current_host = None
