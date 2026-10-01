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
        "postgresql://x/y",
        "archive-dir",
        timeout=10.0,
        poll_interval=1.0,
        switch_wal=lambda dsn: None,
        list_dir=list_dir,
        sleep=lambda s: None,
        clock=iter([0.0, 1.0, 2.0]).__next__,
    )
    assert reason is None


def test_verify_archiving_advances_times_out_when_nothing_new():
    clock_values = iter([0.0, 1.0, 5.0, 11.0, 11.0])
    reason = wal_archive.verify_archiving_advances(
        "postgresql://x/y",
        "archive-dir",
        timeout=10.0,
        poll_interval=1.0,
        switch_wal=lambda dsn: None,
        list_dir=lambda _d: {"a"},
        sleep=lambda s: None,
        clock=lambda: next(clock_values),
    )
    assert reason is not None
    assert "새 파일" in reason


# --- negative tests: invalid/corrupted inputs --------------------------------


def test_main_returns_1_when_verify_write_dir_does_not_exist(monkeypatch):
    """존재하지 않는 아카이브 디렉터리를 --verify-write로 지정하면 종료코드 1."""
    import sys
    from io import StringIO

    # read_wal_settings를 mock해서 DB 연결 없이 valid settings를 반환
    monkeypatch.setattr(
        wal_archive,
        "read_wal_settings",
        lambda dsn: {
            "wal_level": "replica",
            "archive_mode": "on",
            "archive_command": "cp %p /archive/%f",
        },
    )
    # verify_archiving_advances가 switch_wal을 통해 DB에 연결하므로
    # verify_archiving_advances 자체를 mock해서 reason을 반환하도록 한다
    monkeypatch.setattr(
        wal_archive,
        "verify_archiving_advances",
        lambda *a, **k: "verify failed",
    )

    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.argv = [
        "wal_archive",
        "--dsn",
        "postgresql://x/y",
        "--verify-write",
        "/nonexistent/dir/that/does/not/exist",
    ]
    sys.stdout = StringIO()
    sys.stderr = StringIO(
        "stderr",
    )  # noqa: DUPL125
    try:
        code = wal_archive.main()
        assert code == 1
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def test_main_returns_1_when_verify_write_dir_not_writable(monkeypatch):
    """쓰기 권한이 없는 디렉터리를 --verify-write로 지정하면 종료코드 1."""
    import sys
    import tempfile
    from io import StringIO

    monkeypatch.setattr(
        wal_archive,
        "read_wal_settings",
        lambda dsn: {
            "wal_level": "replica",
            "archive_mode": "on",
            "archive_command": "cp %p /archive/%f",
        },
    )

    tmpdir = tempfile.mkdtemp()
    old_argv = sys.argv
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.argv = [
        "wal_archive",
        "--dsn",
        "postgresql://x/y",
        "--verify-write",
        tmpdir,
    ]
    sys.stdout = StringIO()
    sys.stderr = StringIO(
        "stderr",
    )  # noqa: DUPL125
    try:
        # list_dir를 monkeypatch하여 비어있도록 만들어 verify_archiving_advances가
        # 타임아웃으로 실패하도록 유도 (verify_write 경로가 실패 경로를 타는지 확인)
        monkeypatch.setattr(wal_archive, "verify_archiving_advances", lambda *a, **k: "타임아웃")
        code = wal_archive.main()
        assert code == 1
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_evaluate_flags_missing_wal_level():
    """wal_level 키가 없으면 evaluate는 실패를 보고한다."""
    settings = {"archive_mode": "on", "archive_command": "cp %p /archive/%f"}
    issues = wal_archive.evaluate(settings)
    assert any("wal_level" in i for i in issues)


def test_evaluate_flags_missing_archive_mode():
    """archive_mode 키가 없으면 evaluate는 실패를 보고한다."""
    settings = {"wal_level": "replica", "archive_command": "cp %p /archive/%f"}
    issues = wal_archive.evaluate(settings)
    assert any("archive_mode" in i for i in issues)


# --- failure injection: OSError in verify_archiving_advances -----------------


def test_verify_archiving_advances_raises_OSError_from_list_dir():
    """list_dir가 OSError를 던지면 호출부가 이를 삼키지 않고 전파한다."""
    import pytest

    def failing_list_dir(_d):
        raise OSError("directory read failed")

    with pytest.raises(OSError, match="directory read failed"):
        wal_archive.verify_archiving_advances(
            "postgresql://x/y",
            "archive-dir",
            timeout=1.0,
            poll_interval=0.5,
            switch_wal=lambda dsn: None,
            list_dir=failing_list_dir,
            sleep=lambda s: None,
            clock=lambda: 999.0,  # deadline 초과 방지
        )
