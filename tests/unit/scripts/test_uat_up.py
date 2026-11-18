"""Unit tests for scripts/uat/up.py with an injected command runner (no docker)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("uat_up", ROOT / "scripts" / "uat" / "up.py")
assert _spec is not None and _spec.loader is not None
up = importlib.util.module_from_spec(_spec)
sys.modules["uat_up"] = up
_spec.loader.exec_module(up)


class Recorder:
    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail_at = fail_at

    def __call__(self, cmd, env=None) -> int:
        self.calls.append((list(cmd), dict(env or {})))
        return 1 if self.fail_at == len(self.calls) - 1 else 0


def _up(rec, *, env_exists=True, healthy=True, host="127.0.0.1"):
    return up.cmd_up(
        host,
        runner=rec,
        check=lambda _u: healthy,
        sleep=lambda _s: None,
        clock=iter(range(0, 10_000, 50)).__next__,
        env_exists=lambda: env_exists,
    )


def test_up_order_and_args(capsys):
    rec = Recorder()
    assert _up(rec, host="100.1.2.3") == 0
    cmds = [c for c, _ in rec.calls]
    assert cmds[0][:6] == ["docker", "compose", "-f", "compose.uat.yml", "up", "-d"]
    assert cmds[0][-2:] == ["postgres", "redis"] and "--build" in cmds[0]
    assert cmds[1][-5:] == ["--profile", "migrate", "run", "--rm", "migrate"][-5:]
    assert cmds[2][-3:] == ["api", "worker", "web"]
    assert cmds[3][1].endswith("seed_demo.py")
    env = rec.calls[0][1]
    assert env["UAT_WEB_ORIGIN"] == "http://100.1.2.3:18080"
    assert env["UAT_API_BASE_URL"] == "http://100.1.2.3:18000"
    assert env["AIOS_RUNTIME_MODE"] == "PAPER"
    out = capsys.readouterr().out
    assert "tailscale serve --bg --tcp 18080 tcp://127.0.0.1:18080" in out
    assert "tailscale serve --bg --tcp 18000 tcp://127.0.0.1:18000" in out


def test_gen_env_only_when_missing():
    rec = Recorder()
    _up(rec, env_exists=False)
    assert rec.calls[0][0][1].endswith("gen_env.py")
    rec2 = Recorder()
    _up(rec2, env_exists=True)
    assert not any(c[1].endswith("gen_env.py") for c, _ in rec2.calls if len(c) > 1)


def test_failure_stops_sequence():
    rec = Recorder(fail_at=1)
    assert _up(rec) == 1
    assert len(rec.calls) == 2


def test_health_timeout_skips_seed():
    rec = Recorder()
    assert _up(rec, healthy=False) == 1
    assert not any("seed_demo" in " ".join(c) for c, _ in rec.calls)


def test_wait_healthy_retries_then_succeeds():
    results = iter([False, False, True])
    assert up.wait_healthy("u", check=lambda _u: next(results), sleep=lambda _s: None)


def test_down_keeps_volumes():
    rec = Recorder()
    up.cmd_down("127.0.0.1", runner=rec)
    assert rec.calls[0][0][-1] == "down" and "-v" not in rec.calls[0][0]


@pytest.mark.parametrize("answer", ["", "no", "y"])
def test_reset_requires_confirmation(answer):
    rec = Recorder()
    assert up.cmd_reset("127.0.0.1", runner=rec, confirm=lambda _p: answer) == 1
    assert rec.calls == []


def test_reset_confirmed_removes_volumes():
    rec = Recorder()
    assert up.cmd_reset("127.0.0.1", runner=rec, confirm=lambda _p: "yes") == 0
    assert rec.calls[0][0][-2:] == ["down", "-v"]


# --- D2 depth: negative / failure injection / perf / gate-red ---


def test_up_rejects_runner_failure_at_every_step():
    """Negative: a failing command at any step returns non-zero and stops the sequence."""
    for step in range(3):
        rec = Recorder(fail_at=step)
        assert _up(rec) != 0
        assert len(rec.calls) == step + 1


def test_up_health_failure_denies_seed_and_returns_nonzero():
    """Negative: unhealthy stack is refused -- no seed command and a non-zero exit."""
    rec = Recorder()
    code = _up(rec, healthy=False)
    assert code != 0
    assert [c for c, _ in rec.calls if "seed_demo" in " ".join(c)] == []


def test_wait_healthy_timeout_rejected_returns_false():
    """Negative: wait_healthy never succeeding returns a falsy result instead of raising."""
    clock = iter(range(0, 100_000, 100)).__next__
    result = up.wait_healthy("u", check=lambda _u: False, sleep=lambda _s: None, clock=clock)
    assert not result


def test_reset_invalid_answer_rejected_keeps_volumes():
    """Negative: anything but exact confirmation never issues a command."""
    rec = Recorder()
    with pytest.raises(AssertionError):
        assert up.cmd_reset("127.0.0.1", runner=rec, confirm=lambda _p: "YES!") == 0
    assert rec.calls == []


def test_runner_exception_propagates_and_stops_sequence():
    """Failure injection: a runner raising mid-sequence aborts without running later steps."""
    rec = Recorder()

    def boom(cmd, env=None):
        rec(cmd, env)
        if len(rec.calls) == 2:
            raise OSError("docker unavailable")
        return 0

    with pytest.raises(OSError):
        up.cmd_up(
            "127.0.0.1",
            runner=boom,
            check=lambda _u: True,
            sleep=lambda _s: None,
            clock=iter(range(0, 10_000, 50)).__next__,
            env_exists=lambda: True,
        )
    assert len(rec.calls) == 2


@pytest.mark.perf
def test_perf_cmd_up_within_budget(perf_budget):
    """Perf: injected-runner cmd_up best-of-5 CPU time under 50 ms.

    Budget rationale: pure in-memory command construction; local measurement is well under
    1 ms (expected <1.5 ms incl. stdout capture), so 50 ms leaves >30x headroom.
    """
    sample = perf_budget.best_of(lambda: _up(Recorder()), n=5, warmup=1)
    assert sample.cpu_ms < 50.0, f"cpu={sample.cpu_ms:.3f}ms budget<50ms"


@pytest.mark.perf
def test_perf_cmd_up_goes_red_with_10x_virtual_delay(perf_budget):
    """Gate-red reproduction: a 500 ms virtual delay (10x budget) makes the budget fail."""
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    reset_virtual_totals()
    slow = virtual_delayed_sync(lambda: _up(Recorder()), 0.5)
    with pytest.raises(AssertionError):
        perf_budget.assert_within(slow, budget_ms=50.0, n=3, warmup=1, label="cmd_up_10x")
