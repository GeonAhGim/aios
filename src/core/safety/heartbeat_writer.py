"""Periodic main-process heartbeat writer.

The watchdog (src/watchdog_process.py) judges main liveness from the heartbeat file;
this loop is the only production writer. The interval is derived from the watchdog's
unresponsive threshold so a few missed ticks never trip a false HALT, while a truly
dead writer still does.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.core.safety.heartbeat import resolve_heartbeat_path, write_heartbeat
from src.core.safety.watchdog import DEFAULT_UNRESPONSIVE_SEC_THRESHOLD

logger = logging.getLogger(__name__)

# At most 1/3 of the watchdog threshold: two consecutive failed ticks still leave slack.
HEARTBEAT_INTERVAL_SEC = DEFAULT_UNRESPONSIVE_SEC_THRESHOLD / 3


async def run_heartbeat_writer(path: Path, interval_sec: float = HEARTBEAT_INTERVAL_SEC) -> None:
    """Write the heartbeat every ``interval_sec`` until cancelled.

    Write failures are logged at error level and retried on the next tick."""
    while True:
        try:
            write_heartbeat(path)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat write failed: %s", path)
        await asyncio.sleep(interval_sec)


def start_heartbeat_writer() -> asyncio.Task[None]:
    """Start the writer on the env-resolved path (AIOS_HEARTBEAT_PATH or default)."""
    return asyncio.create_task(run_heartbeat_writer(resolve_heartbeat_path()))
