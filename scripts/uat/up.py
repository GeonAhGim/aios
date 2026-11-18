"""Bring the UAT (paper-only) stack up with one command.

Standard library only.  Subcommands: ``up`` (default), ``down``, ``reset``.
The tailscale relay commands are printed, never executed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = "compose.uat.yml"
ENV_FILE = ".env.uat"
WEB_PORT = 18080
API_PORT = 18000
HEALTH_TIMEOUT_S = 120.0
HEALTH_INTERVAL_S = 3.0

Runner = Callable[[Sequence[str], Mapping[str, str] | None], int]
HealthCheck = Callable[[str], bool]


def default_runner(cmd: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """Run a command in the repo root, inheriting stdio."""
    merged = {**os.environ, **(env or {})}
    return subprocess.run(list(cmd), cwd=REPO_ROOT, env=merged, check=False).returncode


def default_health(url: str) -> bool:
    """Return True when the URL answers with HTTP 2xx."""
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except OSError:
        return False


def compose(*args: str) -> list[str]:
    """Build a docker compose command for the UAT project."""
    return ["docker", "compose", "-f", COMPOSE_FILE, *args]


def stack_env(host: str) -> dict[str, str]:
    """Environment values compose needs; runtime mode is pinned to PAPER."""
    return {
        "UAT_API_BASE_URL": f"http://{host}:{API_PORT}",
        "UAT_WEB_ORIGIN": f"http://{host}:{WEB_PORT}",
        "AIOS_RUNTIME_MODE": "PAPER",
    }


def tailscale_commands() -> list[str]:
    """Phone-access relay commands (printed only)."""
    return [
        f"tailscale serve --bg --tcp {WEB_PORT} tcp://127.0.0.1:{WEB_PORT}",
        f"tailscale serve --bg --tcp {API_PORT} tcp://127.0.0.1:{API_PORT}",
    ]


def wait_healthy(
    url: str,
    *,
    check: HealthCheck = default_health,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = HEALTH_TIMEOUT_S,
    interval: float = HEALTH_INTERVAL_S,
) -> bool:
    """Poll ``url`` until healthy or ``timeout`` seconds elapse."""
    deadline = clock() + timeout
    while True:
        if check(url):
            return True
        if clock() >= deadline:
            return False
        sleep(interval)


def cmd_up(
    host: str,
    *,
    runner: Runner = default_runner,
    check: HealthCheck = default_health,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    env_exists: Callable[[], bool] | None = None,
) -> int:
    """Start the stack, wait for health, seed the demo user, print access info."""
    env = stack_env(host)
    exists = env_exists if env_exists is not None else (REPO_ROOT / ENV_FILE).exists
    if not exists():
        rc = runner([sys.executable, "scripts/uat/gen_env.py"], env)
        if rc != 0:
            return rc
    steps: list[list[str]] = [
        compose("up", "-d", "--build", "postgres", "redis"),
        compose("--profile", "migrate", "run", "--rm", "migrate"),
        compose("up", "-d", "--build", "api", "worker", "web"),
    ]
    for step in steps:
        rc = runner(step, env)
        if rc != 0:
            return rc
    api_url = f"http://127.0.0.1:{API_PORT}"
    if not wait_healthy(f"{api_url}/livez", check=check, sleep=sleep, clock=clock):
        print(f"API health check failed within {HEALTH_TIMEOUT_S:.0f}s: {api_url}", file=sys.stderr)
        return 1
    rc = runner([sys.executable, "scripts/uat/seed_demo.py", "--base-url", api_url], env)
    if rc != 0:
        return rc
    print(f"Web: http://{host}:{WEB_PORT}")
    print(f"API: http://{host}:{API_PORT}")
    print("Demo login: UAT_DEMO_EMAIL / UAT_DEMO_PASSWORD in .env.uat (local file, PAPER only)")
    print("Phone access (run these yourself):")
    for line in tailscale_commands():
        print(f"  {line}")
    return 0


def cmd_down(host: str, *, runner: Runner = default_runner) -> int:
    """Stop the stack; volumes are preserved."""
    return runner(compose("down"), stack_env(host))


def cmd_reset(
    host: str,
    *,
    runner: Runner = default_runner,
    confirm: Callable[[str], str] = input,
) -> int:
    """Stop the stack and delete its volumes after a confirmation prompt."""
    answer = confirm("This deletes all UAT data volumes. Type yes to continue: ")
    if answer.strip().lower() != "yes":
        print("Aborted.")
        return 1
    return runner(compose("down", "-v"), stack_env(host))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Start/stop the UAT (paper-only) stack.")
    parser.add_argument("command", nargs="?", default="up", choices=["up", "down", "reset"])
    parser.add_argument("--host", default="127.0.0.1", help="Host used in printed URLs/origins.")
    args = parser.parse_args(argv)
    if args.command == "down":
        return cmd_down(args.host)
    if args.command == "reset":
        return cmd_reset(args.host)
    return cmd_up(args.host)


if __name__ == "__main__":
    sys.exit(main())
