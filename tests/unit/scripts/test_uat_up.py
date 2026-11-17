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
