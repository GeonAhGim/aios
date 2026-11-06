"""ADR-2026-09-26-B Decision 1 -- sandbox error classification tests.

Negative tests for review findings (task-22571): (1) BrokenProcessPool without
RSS-exceeded maps to `ScriptSandboxCrashError` with the original executor
exception as `__cause__`, (2) `BrokenProcessPool` + memory-exceeded maps to
`ScriptSandboxMemoryExceededError` (fail-closed priority).
"""

from __future__ import annotations

import time
from concurrent.futures import BrokenExecutor

import pytest

from src.foundation.backtest.application import sandboxed_script_eval
from src.foundation.backtest.application.sandboxed_script_eval import (
    SandboxLimits,
    ScriptSandboxCrashError,
    ScriptSandboxMemoryExceededError,
    run_sandboxed,
)


def _hang_forever() -> None:
    time.sleep(3600)


def _crash_immediately() -> None:
    import os

    os._exit(1)  # noqa: SLF001 -- deliberate hard-crash fixture, not a real callsite


# ── Negative tests for review findings (task-22571) ────────────────────────


def test_broken_pool_maps_to_crash_error_with_broken_executor_cause() -> None:
    """N/A: BrokenProcessPool → ScriptSandboxCrashError with BrokenExecutor __cause__.

    Negative test: a child that calls `os._exit` produces `BrokenProcessPool`
    from `Executor.map`. The sandbox must re-raise as `ScriptSandboxCrashError`
    and preserve the original `BrokenExecutor` (or `BrokenProcessPool`) as
    `__cause__`, so operators do not misdiagnose it as a memory error.
    """
    with pytest.raises(ScriptSandboxCrashError) as info:
        run_sandboxed(
            _crash_immediately,
            limits=SandboxLimits(wallclock_sec=10, rss_mb=512),
        )
    # The __cause__ must be the executor exception, not a generic error.
    assert info.value.__cause__ is not None, (
        "ScriptSandboxCrashError.__cause__ must carry the BrokenExecutor"
    )
    # BrokenProcessPool wraps BrokenExecutor; check the chain.
    cause = info.value.__cause__
    assert isinstance(cause, BrokenExecutor) or hasattr(cause, "exception"), (
        f"Expected BrokenExecutor or BrokenProcessPool __cause__, got {type(cause).__name__}"
    )


def test_broken_process_pool_raises_crash_error_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BrokenProcessPool without exceeded → ScriptSandboxCrashError (fail-closed).

    Simulates a worker that died (OOM kill, segfault) before the RSS watcher
    could flag a memory exceed. The sandbox must never return success or
    silently ignore the failure.
    """
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool

    class BrokenFuture(Future):
        def result(self, timeout=None):
            raise BrokenProcessPool("worker died unexpectedly")

        def cancel(self):
            return False

        def cancelled(self):
            return False

        def done(self):
            return True

        def exception(self, timeout=None):
            return BrokenProcessPool("worker died unexpectedly")

    # Patch submit to return our broken future.
    import concurrent.futures

    def _patched_submit(self, fn, /, *args, **kwargs):
        return BrokenFuture()

    monkeypatch.setattr(concurrent.futures.ProcessPoolExecutor, "submit", _patched_submit)
    # No real process will be created, so pid capture returns None.
    monkeypatch.setattr(sandboxed_script_eval, "_wait_for_worker_pid", lambda *a, **kw: None)

    with pytest.raises(ScriptSandboxCrashError, match="worker died unexpectedly"):
        run_sandboxed(_hang_forever, limits=SandboxLimits(wallclock_sec=120, rss_mb=512))


def test_broken_process_pool_with_memory_exceeded_raises_memory_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BrokenProcessPool + exceeded → ScriptSandboxMemoryExceededError (fail-closed).

    Simulates a child that exceeded RSS (setting the ``exceeded`` event) and then
    the process pool detected the worker died. The sandbox must classify this as a
    memory error, not a generic crash, so the caller knows the RSS ceiling worked.

    Strategy: patch ``_watch_rss`` so it immediately sets ``exceeded``, and patch
    ``submit`` to raise ``BrokenProcessPool``. The ``run_sandboxed`` handler at
    line 139 checks ``exceeded.wait(timeout=0.5)`` — with the patched watcher it
    will be True, so the error is classified as memory-exceeded.
    """
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool

    class BrokenFuture(Future):
        def result(self, timeout=None):
            raise BrokenProcessPool("worker died after memory kill")

        def cancel(self):
            return False

        def cancelled(self):
            return False

        def done(self):
            return True

        def exception(self, timeout=None):
            return BrokenProcessPool("worker died after memory kill")

    # Patch submit to return our broken future.
    import concurrent.futures

    def _patched_submit(self, fn, /, *args, **kwargs):
        return BrokenFuture()

    monkeypatch.setattr(concurrent.futures.ProcessPoolExecutor, "submit", _patched_submit)
    # No real process will be created, so pid capture returns None.
    monkeypatch.setattr(sandboxed_script_eval, "_wait_for_worker_pid", lambda *a, **kw: None)

    # Patch _watch_rss so it immediately sets ``exceeded`` (simulating RSS
    # watcher having flagged the exceed before the process pool noticed).
    def _fake_watcher(pid, rss_mb, stop_watching, exceeded):
        exceeded.set()

    monkeypatch.setattr(sandboxed_script_eval, "_watch_rss", _fake_watcher)

    with pytest.raises(ScriptSandboxMemoryExceededError, match="RSS limit"):
        run_sandboxed(_hang_forever, limits=SandboxLimits(wallclock_sec=120, rss_mb=512))
