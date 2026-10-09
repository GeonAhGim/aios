"""ADR-2026-09-26-B Decision 1 (SBX-2) — process-isolated sandbox for
`src.core.script.runtime.interpreter.execute()`.

BT-10b (`script_signal_source.py`) currently calls `execute()` in the
caller's own process. SBX-1's compile-time step budget only rejects a
statically-unbounded IR (there is no loop/recursion in this DSL at all —
see `interpreter.py` module docstring); it does nothing against a runtime
that legitimately compiles but pathologically explodes wall-clock time or
memory once real builtin implementations run (large lookback windows,
expensive indicator math). This module runs an arbitrary callable in a
single-worker `ProcessPoolExecutor` and enforces two independent, fail-closed
ceilings: a wall-clock deadline via `future.result(timeout=...)`, and an RSS
ceiling via a polling watcher thread that reads `psutil.Process(pid).memory_info().rss`
(the same mechanism on Windows and POSIX — no platform-specific rlimit/cgroup
call, since Windows has neither).

Both violations kill the child process and raise instead of returning a
partial or guessed result. A child that dies for any other reason (crash,
OS-level OOM kill) also raises rather than silently propagating whatever
`future.result()` says, so the caller can never mistake "child vanished" for
"child returned nothing".

This leaf only adds the wrapper. `script_signal_source.py`'s call to
`execute()` and any API router that reaches it are intentionally left
unchanged here — swapping that call site is a separate follow-up leaf (file
overlap avoidance; see task-7616 note).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Any, TypeVar

import psutil

DEFAULT_WALLCLOCK_LIMIT_SEC: float = float(os.environ.get("SCRIPT_WALLCLOCK_LIMIT_SEC", "30"))
DEFAULT_RSS_LIMIT_MB: float = float(os.environ.get("SCRIPT_RSS_LIMIT_MB", "512"))
_PID_POLL_INTERVAL_SEC = 0.02
_RSS_POLL_INTERVAL_SEC = 0.02
# Grace window to keep polling for the worker pid after `future.result()` has
# already timed out, independent of `limits.wallclock_sec`. Process creation
# (not `fn`'s own execution) can lag past a tight caller-supplied deadline on
# a cold spawn (see the module comments on Windows cold-spawn cost elsewhere
# in this file); without this, a pid that never registered within
# `wallclock_sec` was permanently un-killable (task-8928).
_PID_CAPTURE_GRACE_SEC = 10.0

_T = TypeVar("_T")


class ScriptSandboxTimeoutError(Exception):
    """Wall-clock budget exceeded before the child process returned; the
    child was cancelled/killed and no result was produced."""


class ScriptSandboxMemoryExceededError(Exception):
    """RSS budget exceeded; the watcher thread killed the child process
    before it could return a result."""


class ScriptSandboxCrashError(Exception):
    """The child process ended without hitting either the wall-clock or the
    RSS ceiling we imposed (segfault, OS-level OOM kill, etc.). Fail-closed:
    never mistaken for a successful empty result."""


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    wallclock_sec: float = DEFAULT_WALLCLOCK_LIMIT_SEC
    rss_mb: float = DEFAULT_RSS_LIMIT_MB


_DEFAULT_LIMITS = SandboxLimits()


def run_sandboxed(
    fn: Callable[..., _T],
    /,
    *args: Any,
    limits: SandboxLimits = _DEFAULT_LIMITS,
    **kwargs: Any,
) -> _T:
    """Run `fn(*args, **kwargs)` in a single-worker `ProcessPoolExecutor`
    under `limits`. `fn` and its arguments/return value must be picklable
    (standard `multiprocessing` constraint)."""
    own_pid = os.getpid()
    executor = ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn"))
    try:
        # Executor construction starts the shared resource tracker on POSIX.
        # Snapshot before submit starts our worker so shared helpers survive.
        children_before = _own_child_pids(own_pid)
        future = executor.submit(fn, *args, **kwargs)
        pid = _wait_for_worker_pid(executor, limits.wallclock_sec)
        exceeded = threading.Event()
        stop_watching = threading.Event()
        watcher = threading.Thread(
            target=_watch_rss,
            args=(pid, limits.rss_mb, stop_watching, exceeded),
            daemon=True,
        )
        watcher.start()
        try:
            result = future.result(timeout=limits.wallclock_sec)
        except FutureTimeoutError:
            future.cancel()
            if pid is None:
                # The worker hadn't registered its pid within `wallclock_sec` --
                # keep looking past that deadline so we can still kill it instead
                # of leaving it running (see `_PID_CAPTURE_GRACE_SEC`).
                pid = _wait_for_worker_pid(executor, _PID_CAPTURE_GRACE_SEC)
            if pid is None:
                # Both windows elapsed and `executor._processes` (a private,
                # management-thread-populated dict) still never saw the
                # worker -- under severe host contention that bookkeeping can
                # lag arbitrarily far behind the OS actually creating the
                # process. Fall back to the OS process tree directly, which
                # reflects the real child the instant it exists, so we never
                # leave a true orphan (e.g. a 3600s `time.sleep`) running
                # unkillable for the rest of its natural life (task-11273).
                # Kill every new child, not just the first. Shared executor
                # infrastructure is already in children_before and must not
                # be killed: multiprocessing would relaunch its tracker.
                for candidate_pid in _find_new_child_pids(own_pid, children_before):
                    _kill_pid(candidate_pid)
            else:
                _kill_pid(pid)
            raise ScriptSandboxTimeoutError(
                f"script sandbox exceeded wall-clock limit ({limits.wallclock_sec}s)"
            ) from None
        except Exception as exc:
            # The child may have just been killed by the watcher; give it a
            # brief window to flip `exceeded` before we classify the failure
            # as an unexplained crash (fail-closed either way -- both paths raise).
            if exceeded.wait(timeout=0.5):
                raise ScriptSandboxMemoryExceededError(
                    f"script sandbox exceeded RSS limit ({limits.rss_mb}MB)"
                ) from exc
            raise ScriptSandboxCrashError(
                f"script sandbox child process died unexpectedly: {exc}"
            ) from exc
        else:
            return result
        finally:
            stop_watching.set()
            watcher.join(timeout=1.0)
    finally:
        # `wait=False`: we've already killed anything we could find a pid for
        # above. Blocking here on `shutdown(wait=True)` (the `with` statement's
        # default `__exit__`) turned a failed/racy kill into an indefinite hang
        # -- the child process (e.g. a test fixture sleeping 3600s) would keep
        # this call from ever returning, which is how one bad kill took down an
        # entire CI run's coverage measurement (task-8928).
        executor.shutdown(wait=False, cancel_futures=True)


def _wait_for_worker_pid(executor: ProcessPoolExecutor, deadline_sec: float) -> int | None:
    """Best-effort read of the spawned worker pid via the (private, but
    stable across supported Python versions) `_processes` map. Returns
    `None` if the pool never spawns before `deadline_sec` (e.g. `fn`
    already finished and the pool tore the worker down first) -- callers
    treat a missing pid as "nothing to watch", not an error."""
    deadline = time.monotonic() + deadline_sec
    while time.monotonic() < deadline:
        processes = getattr(executor, "_processes", None)
        if processes:
            return int(next(iter(processes)))
        time.sleep(_PID_POLL_INTERVAL_SEC)
    return None


def _own_child_pids(parent_pid: int) -> frozenset[int]:
    """Snapshot of the current process's direct OS child pids, taken before
    submitting the sandboxed worker so a later diff can identify the new
    child even if `executor._processes` never registers it."""
    try:
        return frozenset(p.pid for p in psutil.Process(parent_pid).children())
    except psutil.NoSuchProcess:
        return frozenset()


def _find_new_child_pids(parent_pid: int, known_pids: frozenset[int]) -> list[int]:
    """OS-process-tree fallback for `_wait_for_worker_pid`: returns the pids
    of every direct child of `parent_pid` not present in `known_pids` (empty
    if none exist, e.g. they already exited). A `spawn` worker can bring up
    more than one new direct child at once (the worker itself plus a
    multiprocessing helper process such as the resource tracker), so callers
    must kill all of them, not just the first."""
    try:
        children = psutil.Process(parent_pid).children()
    except psutil.NoSuchProcess:
        return []
    return [child.pid for child in children if child.pid not in known_pids]


def _watch_rss(
    pid: int | None,
    limit_mb: float,
    stop: threading.Event,
    exceeded: threading.Event,
) -> None:
    """Monitor child process RSS and kill if ``limit_mb`` is exceeded.

    Uses delta-based comparison (RSS increase from baseline) so that a fresh spawn
    worker on Linux/fork doesn't get killed immediately due to inheriting the parent's
    RSS footprint.
    """
    if pid is None:
        return
    limit_bytes = limit_mb * 1024 * 1024
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    baseline_rss: int | None = None
    while not stop.is_set():
        try:
            rss = process.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        if baseline_rss is None:
            baseline_rss = rss
            stop.wait(_RSS_POLL_INTERVAL_SEC)
            continue
        if (rss - baseline_rss) > limit_bytes:
            exceeded.set()
            _kill_pid(pid)
            return
        stop.wait(_RSS_POLL_INTERVAL_SEC)


def _kill_pid(pid: int | None) -> None:
    if pid is None:
        return
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass
