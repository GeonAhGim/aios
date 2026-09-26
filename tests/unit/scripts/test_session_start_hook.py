"""session-start.sh must be an immediate no-op off the cloud fleet (task-7981).

The hook provisions PostgreSQL, a venv, and secrets on every SessionStart --
the local Windows fleet (CLAUDE_CODE_REMOTE unset) must never run any of
that. The guard is the very first line of the script (before any `cd`,
subprocess, or filesystem write), so this test runs the real script -- not a
mock -- and asserts zero output and zero side effects.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / ".claude" / "hooks" / "session-start.sh"


def _run(env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_REMOTE"}
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_hook_is_executable_script() -> None:
    assert HOOK.is_file()
    assert os.access(HOOK, os.X_OK)


def test_noop_when_claude_code_remote_unset() -> None:
    result = _run({})
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_noop_when_claude_code_remote_is_not_exactly_true() -> None:
    for value in ("false", "1", "True", ""):
        result = _run({"CLAUDE_CODE_REMOTE": value})
        assert result.returncode == 0, f"CLAUDE_CODE_REMOTE={value!r} was not a no-op"
        assert result.stdout == ""
        assert result.stderr == ""


def test_noop_does_not_touch_the_working_tree(tmp_path: Path) -> None:
    # A copy of the repo file tree with no .venv/.env/node_modules present --
    # the no-op guard must not create any of them either.
    marker_files = {".venv", ".env", "frontend/node_modules"}
    before = {name for name in marker_files if (ROOT / name).exists()}
    _run({})
    after = {name for name in marker_files if (ROOT / name).exists()}
    assert after == before
