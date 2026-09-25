"""scripts/backup/wal_archive.py 단위 테스트 -- H-4(task-2609, ADR-2026-09-09-B).

evaluate()/check_archive_advanced()는 순수 함수라 실제 Postgres 없이 위반 주입이
가능하다. verify_archiving_advances()는 switch_wal/list_dir/sleep/clock을 모두 주입해
실제 대기 없이 정상/타임아웃 경로를 검증한다.
"""
from __future__ import annotations

from scripts.backup import wal_archive

# --- evaluate ---------------------------------------------------------------

_OK_COMMAND = "cp %p /archive/%f"


def test_evaluate_passes_when_all_settings_correct():
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": _OK_COMMAND}
    assert wal_archive.evaluate(settings) == []


def test_evaluate_flags_wrong_wal_level():
    settings = {"wal_level": "minimal", "archive_mode": "on", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    assert any("wal_level" in i for i in issues)


def test_evaluate_flags_archive_mode_off():
    settings = {"wal_level": "replica", "archive_mode": "off", "archive_command": _OK_COMMAND}
    issues = wal_archive.evaluate(settings)
    assert any("archive_mode" in i for i in issues)


def test_evaluate_flags_empty_archive_command():
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": ""}
    issues = wal_archive.evaluate(settings)
    assert any("archive_command" in i for i in issues)


def test_evaluate_flags_disabled_archive_command():
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": "(disabled)"}
    issues = wal_archive.evaluate(settings)
    assert any("archive_command" in i for i in issues)


def test_evaluate_accepts_always_archive_mode_and_logical_wal_level():
    settings = {"wal_level": "logical", "archive_mode": "always", "archive_command": _OK_COMMAND}
    assert wal_archive.evaluate(settings) == []


# --- check_archive_advanced ---------------------------------------------------

def test_check_archive_advanced_ok_when_new_file_appears():
    assert wal_archive.check_archive_advanced({"a"}, {"a", "b"}) is None


def test_check_archive_advanced_fails_when_no_new_file():
    reason = wal_archive.check_archive_advanced({"a"}, {"a"})
    assert reason is not None


# --- verify_archiving_advances ------------------------------------------------

def test_verify_archiving_advances_detects_new_file_before_timeout():
    calls = {"n": 0}

    def list_dir(_d):
        calls["n"] += 1
        return {"a"} if calls["n"] == 1 else {"a", "new-wal-file"}

    reason = wal_archive.verify_archiving_advances(
        "postgresql://x/y", "archive-dir",
        timeout=10.0, poll_interval=1.0,
        switch_wal=lambda dsn: None,
        list_dir=list_dir,
        sleep=lambda s: None,
        clock=iter([0.0, 1.0, 2.0]).__next__,
    )
    assert reason is None


def test_verify_archiving_advances_times_out_when_nothing_new():
    clock_values = iter([0.0, 1.0, 5.0, 11.0, 11.0])
    reason = wal_archive.verify_archiving_advances(
        "postgresql://x/y", "archive-dir",
        timeout=10.0, poll_interval=1.0,
        switch_wal=lambda dsn: None,
        list_dir=lambda _d: {"a"},
        sleep=lambda s: None,
        clock=lambda: next(clock_values),
    )
    assert reason is not None
    assert "새 파일" in reason
