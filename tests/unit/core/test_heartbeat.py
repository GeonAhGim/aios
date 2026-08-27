import time
from pathlib import Path

from src.core.safety.heartbeat import read_heartbeat_age_seconds, write_heartbeat


def test_missing_file_returns_infinite_age(tmp_path: Path):
    assert read_heartbeat_age_seconds(tmp_path / "missing") == float("inf")


def test_fresh_heartbeat_has_near_zero_age(tmp_path: Path):
    path = tmp_path / "heartbeat"
    write_heartbeat(path)
    assert read_heartbeat_age_seconds(path) < 1.0


def test_corrupted_file_returns_infinite_age(tmp_path: Path):
    path = tmp_path / "heartbeat"
    path.write_text("not-a-number", encoding="utf-8")
    assert read_heartbeat_age_seconds(path) == float("inf")


def test_age_increases_over_time(tmp_path: Path):
    path = tmp_path / "heartbeat"
    path.write_text(str(time.time() - 10), encoding="utf-8")
    assert read_heartbeat_age_seconds(path) >= 10.0
