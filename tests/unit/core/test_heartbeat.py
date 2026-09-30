import os
import time
from pathlib import Path

import pytest

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


# --- negative tests: invalid/malformed heartbeat content must never be
# treated as "recently alive" (fail-closed per FD-9.1/9.3) ---------------


def test_empty_file_returns_infinite_age(tmp_path: Path):
    path = tmp_path / "heartbeat"
    path.write_text("", encoding="utf-8")
    assert read_heartbeat_age_seconds(path) == float("inf")


def test_whitespace_only_file_returns_infinite_age(tmp_path: Path):
    path = tmp_path / "heartbeat"
    path.write_text("   \n\t  ", encoding="utf-8")
    assert read_heartbeat_age_seconds(path) == float("inf")


def test_binary_garbage_file_returns_infinite_age(tmp_path: Path):
    path = tmp_path / "heartbeat"
    path.write_bytes(b"\x00\x01\xff\xfe not a float")
    assert read_heartbeat_age_seconds(path) == float("inf")


def test_path_is_directory_returns_infinite_age(tmp_path: Path):
    # A directory can never hold a valid heartbeat timestamp -- reading it
    # must fail closed (infinite age) rather than raise or report freshness.
    path = tmp_path / "heartbeat"
    path.mkdir()
    assert read_heartbeat_age_seconds(path) == float("inf")


# --- failure injection: os.replace() failures during write must not raise
# and must not leave the heartbeat looking "fresh" -------------------------


def test_write_gives_up_silently_when_replace_always_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "heartbeat"
    sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    def _always_fail(_src, _dst):
        raise PermissionError("simulated sharing violation")

    monkeypatch.setattr(os, "replace", _always_fail)

    write_heartbeat(path)  # must not raise despite persistent failure

    assert not path.exists()
    assert read_heartbeat_age_seconds(path) == float("inf")
    # retried (not silently given up on the first attempt)
    assert len(sleep_calls) == 5
    # the temp file must not be left behind after exhausting retries
    assert list(tmp_path.iterdir()) == []


def test_write_returns_cleanly_when_replace_raises_file_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "heartbeat"

    def _missing_target(_src, _dst):
        raise FileNotFoundError("target directory vanished mid-write")

    monkeypatch.setattr(os, "replace", _missing_target)

    write_heartbeat(path)  # must not raise

    assert not path.exists()
    assert read_heartbeat_age_seconds(path) == float("inf")
