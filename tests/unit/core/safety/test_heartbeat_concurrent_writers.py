"""Two writers sharing one heartbeat path must never crash each other (unique temp names)."""
from __future__ import annotations

import threading
from pathlib import Path

from src.core.safety.heartbeat import read_heartbeat_age_seconds, write_heartbeat


def test_concurrent_writers_do_not_raise(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "main_process.heartbeat"
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(300):
                write_heartbeat(path)
        except BaseException as exc:  # noqa: BLE001 - the test records any failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    assert read_heartbeat_age_seconds(path) < 5.0
    leftovers = [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
