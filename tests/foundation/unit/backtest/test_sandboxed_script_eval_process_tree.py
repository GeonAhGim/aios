"""SBX-2 process-tree fallback and orphan-worker enforcement (I-07)."""

from __future__ import annotations

import os
import sys
import time

import psutil
import pytest

from src.foundation.backtest.application import sandboxed_script_eval
from src.foundation.backtest.application.sandboxed_script_eval import (
    SandboxLimits,
    ScriptSandboxTimeoutError,
    run_sandboxed,
)


def _hang_forever() -> None:
    time.sleep(3600)


def _live_child_pids() -> set[int]:
    """Exited, unreaped children cannot execute sandbox code."""
    result = set()
    for child in psutil.Process().children():
        try:
            if child.status() != psutil.STATUS_ZOMBIE:
                result.add(child.pid)
        except psutil.NoSuchProcess:
            pass
    return result


def test_find_new_child_pid_identifies_only_the_new_process() -> None:
    # Unit-level check of the OS-process-tree fallback in isolation (no
    # sandbox/executor involved): a pid present before the snapshot must
    # never be reported as "new", and a genuinely new child must be found.
    own_pid = os.getpid()
    before = sandboxed_script_eval._own_child_pids(own_pid)
    assert sandboxed_script_eval._find_new_child_pids(own_pid, before) == []

    proc = psutil.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        found = sandboxed_script_eval._find_new_child_pids(own_pid, before)
        assert found == [proc.pid]
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_pid_capture_exhausted_still_kills_child_via_process_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Failure injection: force `_wait_for_worker_pid` to never see a pid
    # (simulates `executor._processes` bookkeeping lagging past both the
    # caller deadline and `_PID_CAPTURE_GRACE_SEC` under severe host
    # contention -- the exact gap `1a580317b` left open, task-11273). The
    # OS-process-tree fallback must still find and kill the real child so a
    # `time.sleep(3600)` worker never survives as an unkillable orphan.
    monkeypatch.setattr(sandboxed_script_eval, "_wait_for_worker_pid", lambda *a, **k: None)
    before: set[int] = set()
    snapshot = sandboxed_script_eval._own_child_pids
    executor_factory = sandboxed_script_eval.ProcessPoolExecutor
    executor_created = False

    def create_executor(*args, **kwargs):
        nonlocal executor_created
        executor = executor_factory(*args, **kwargs)
        executor_created = True
        return executor

    def capture_baseline(parent_pid: int) -> frozenset[int]:
        # A cold executor starts a shared tracker before it starts a worker.
        assert executor_created, "snapshot must include executor infrastructure"
        baseline = snapshot(parent_pid)
        before.update(baseline)
        return baseline

    monkeypatch.setattr(sandboxed_script_eval, "ProcessPoolExecutor", create_executor)
    monkeypatch.setattr(sandboxed_script_eval, "_own_child_pids", capture_baseline)

    with pytest.raises(ScriptSandboxTimeoutError):
        run_sandboxed(_hang_forever, limits=SandboxLimits(wallclock_sec=1.0, rss_mb=512))

    time.sleep(1.0)  # let the kill signal land
    after = _live_child_pids()
    assert after - before == set(), "sandbox worker survived as an unkillable orphan"


@pytest.mark.parametrize(
    "state", [psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING, psutil.STATUS_STOPPED]
)
def test_live_child_check_rejects_survivors(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    class Child:
        pid = 123

        def status(self) -> str:
            return state

    monkeypatch.setattr(psutil.Process, "children", lambda self: [Child()])
    assert _live_child_pids() == {123}


def test_live_child_check_excludes_zombie_and_disappeared(monkeypatch: pytest.MonkeyPatch) -> None:
    class Child:
        pid = 123

        def status(self) -> str:
            return psutil.STATUS_ZOMBIE

    class Gone(Child):
        def status(self) -> str:
            raise psutil.NoSuchProcess(self.pid)

    monkeypatch.setattr(psutil.Process, "children", lambda self: [Child(), Gone()])
    assert _live_child_pids() == set()


def test_live_child_check_detects_real_unkilled_process() -> None:
    proc = psutil.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
    try:
        assert proc.pid in _live_child_pids()
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_process_tree_assertion_detects_disabled_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """I-07: suppressing enforcement must make the orphan assertion fail."""
    targets: list[int] = []
    monkeypatch.setattr(sandboxed_script_eval, "_kill_pid", targets.append)
    try:
        with pytest.raises(AssertionError, match="sandbox worker survived"):
            test_pid_capture_exhausted_still_kills_child_via_process_tree(monkeypatch)
    finally:
        # Always release the executor's waiting management thread, even on failure.
        children = [child for child in psutil.Process().children() if child.pid in targets]
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(children, timeout=5)
