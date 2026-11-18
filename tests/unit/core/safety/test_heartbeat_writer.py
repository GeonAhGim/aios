"""Heartbeat writer: cadence, cancellation, failure persistence, path resolution."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from src.core.safety import heartbeat_writer
from src.core.safety.heartbeat import (
    DEFAULT_HEARTBEAT_PATH,
    read_heartbeat_age_seconds,
    resolve_heartbeat_path,
)
from src.core.safety.heartbeat_writer import HEARTBEAT_INTERVAL_SEC, run_heartbeat_writer
from src.core.safety.watchdog import DEFAULT_UNRESPONSIVE_SEC_THRESHOLD


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_writes_periodically(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    task = asyncio.create_task(run_heartbeat_writer(path, 0.01))
    await asyncio.sleep(0.05)
    first = path.read_text(encoding="utf-8")
    await asyncio.sleep(0.05)
    assert path.read_text(encoding="utf-8") != first
    assert read_heartbeat_age_seconds(path) < 1.0
    await _stop(task)


async def test_cancel_terminates_and_stops_writing(tmp_path: Path) -> None:
    path = tmp_path / "hb"
    task = asyncio.create_task(run_heartbeat_writer(path, 0.01))
    await asyncio.sleep(0.03)
    await _stop(task)
    assert task.done()
    frozen = path.read_text(encoding="utf-8")
    await asyncio.sleep(0.05)
    assert path.read_text(encoding="utf-8") == frozen


async def test_write_failure_logged_and_loop_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    def flaky(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk full")

    monkeypatch.setattr(heartbeat_writer, "write_heartbeat", flaky)
    with caplog.at_level(logging.ERROR):
        task = asyncio.create_task(run_heartbeat_writer(tmp_path / "hb", 0.01))
        await asyncio.sleep(0.08)
        await _stop(task)
    assert calls >= 2
    assert any(
        r.levelno == logging.ERROR and "heartbeat write failed" in r.getMessage()
        for r in caplog.records
    )


def test_env_path_overrides_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "shared.heartbeat"
    monkeypatch.setenv("AIOS_HEARTBEAT_PATH", str(target))
    assert resolve_heartbeat_path() == target


def test_default_path_when_env_unset_or_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIOS_HEARTBEAT_PATH", raising=False)
    assert resolve_heartbeat_path() == DEFAULT_HEARTBEAT_PATH
    monkeypatch.setenv("AIOS_HEARTBEAT_PATH", "  ")
    assert resolve_heartbeat_path() == DEFAULT_HEARTBEAT_PATH


def test_interval_at_most_third_of_watchdog_threshold() -> None:
    assert 0 < HEARTBEAT_INTERVAL_SEC <= DEFAULT_UNRESPONSIVE_SEC_THRESHOLD / 3
