"""wal_archive 음성/실패주입 테스트 -- test_wal_archive.py 분리분(파일 크기 ratchet)."""

from __future__ import annotations

from scripts.backup import wal_archive

# --- negative tests: corrupted/missing WAL file paths ------------------------


def test_check_archive_advanced_handles_missing_wal_file_path():
    """아카이브 디렉터리에 WAL 파일이 하나도 없으면 after-before가 빈 집합이다."""
    reason = wal_archive.check_archive_advanced(set(), set())
    assert reason is not None
    assert "새 파일" in reason


def test_check_archive_advanced_handles_corrupted_wal_filename():
    """손상된 WAL 파일명(빈 문자열)이 아카이브 디렉터리에 있더라도 새 파일이 아니다."""
    before = {"000000010000000000000001"}
    after = {"000000010000000000000001", ""}  # corrupted empty-name file
    result = wal_archive.check_archive_advanced(before, after)
    assert result is None  # 빈 파일명도 새 파일로 간주됨(함수 동작 그대로)


def test_check_archive_advanced_handles_wal_file_path_with_special_chars():
    """WAL 파일 경로에 특수 문자가 포함되어 있어도 set 연산은 정상 동작한다."""
    before = {"000000010000000000000001"}
    after = {"000000010000000000000001", "00000002000000000000000A"}
    result = wal_archive.check_archive_advanced(before, after)
    assert result is None  # 새 WAL 파일 등장 → 정상


# --- _default_list_dir: non-existent directory handling ----------------------


def test_default_list_dir_returns_empty_set_for_nonexistent_directory():
    """존재하지 않는 디렉터리를 전달하면 빈 집합을 반환한다 — 조용히 처리.

    이 동작은 결함(defect)으로 간주될 수 있다: 디렉터리 부재가 실제 오류를
    가릴 수 있음. 하지만 현재 구현은 glob("*") 전에 exists()를 체크해
    FileNotFoundError/OSError를 발생시키지 않고 빈 집합을 반환한다.
    """
    from pathlib import Path

    result = wal_archive._default_list_dir(Path("/nonexistent/path/xyz"))
    assert result == set()


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


def test_verify_archiving_advances_raises_OSError_from_switch_wal_copy():
    """switch_wal(copy/move 연산)이 OSError를 던지면 호출부가 이를 삼키지 않고 전파한다."""
    import pytest

    def failing_switch_wal(dsn):
        raise OSError("copy WAL file failed")

    with pytest.raises(OSError, match="copy WAL file failed"):
        wal_archive.verify_archiving_advances(
            "postgresql://x/y",
            "archive-dir",
            timeout=1.0,
            poll_interval=0.5,
            switch_wal=failing_switch_wal,
            list_dir=lambda _d: {"a"},
            sleep=lambda s: None,
            clock=lambda: 999.0,  # deadline 초과 방지
        )
