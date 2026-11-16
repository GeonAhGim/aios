"""scripts/backup/wal_archive.py 실패 경로 테스트 -- task-25243 DEEPEN.

test_wal_archive.py가 loc_growth 래칫(500줄) 경계라 negative/실패주입 증빙을 별도 파일로 분리했다
(CLAUDE.md §6.12). 실제 verify_archiving_advances 경로를 타고, 대기/시계/DB 연결만 주입한다.
"""

from __future__ import annotations

import pytest

from scripts.backup import wal_archive

_OK_COMMAND = "cp %p /archive/%f"


# --- DEEPEN task-25243: unmocked negative / failure-injection paths ----------


def _fast_clock():
    """호출마다 100초씩 전진하는 가짜 시계 -- 실제 대기 없이 타임아웃 경로를 탄다."""
    t = {"now": 0.0}

    def _clock() -> float:
        t["now"] += 100.0
        return t["now"]

    return _clock


def _unmocked_verify(monkeypatch):
    """main()이 쓰는 verify_archiving_advances를 실제 구현 그대로 두고 대기/시계만 주입한다."""
    real_verify = wal_archive.verify_archiving_advances

    def _wrapped(dsn, archive_dir, **kwargs):
        return real_verify(
            dsn,
            archive_dir,
            switch_wal=lambda _dsn: None,
            sleep=lambda _s: None,
            clock=_fast_clock(),
            **kwargs,
        )

    monkeypatch.setattr(wal_archive, "verify_archiving_advances", _wrapped)


def _healthy_settings(monkeypatch):
    monkeypatch.setattr(
        wal_archive,
        "read_wal_settings",
        lambda dsn: {"wal_level": "replica", "archive_mode": "on", "archive_command": _OK_COMMAND},
    )


def test_main_exits_1_when_verify_write_dir_does_not_exist(monkeypatch, tmp_path, capsys):
    """존재하지 않는 --verify-write 디렉터리는 실제 verify 경로의 타임아웃 위반(종료코드 1)이다."""
    _healthy_settings(monkeypatch)
    _unmocked_verify(monkeypatch)
    missing = tmp_path / "no-such-archive-dir"

    code = wal_archive.main(["--dsn", "postgresql://x/y", "--verify-write", str(missing)])

    assert code == 1
    assert "새 파일" in capsys.readouterr().err


def test_main_exits_1_when_archive_dir_never_receives_new_wal(monkeypatch, tmp_path, capsys):
    """아카이브 디렉터리에 스위치 후 새 WAL이 끝까지 생기지 않으면 종료코드 1 -- 조용한 성공 금지.

    주의: 스크립트는 디렉터리 쓰기 권한을 직접 검사하지 않는다. 쓰기 불가 상황은 '새 파일이
    나타나지 않음'으로만 드러나므로 그 경로를 단언한다(권한 검사 추가는 별도 결정 사항)."""
    _healthy_settings(monkeypatch)
    _unmocked_verify(monkeypatch)
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "000000010000000000000001").write_text("old")

    code = wal_archive.main(["--dsn", "postgresql://x/y", "--verify-write", str(archive)])

    assert code == 1
    assert "새 파일" in capsys.readouterr().err


def test_main_propagates_oserror_from_db_connect(monkeypatch):
    """DB 연결 OSError 주입 -- main()/read_wal_settings()가 이를 삼키지 않고 전파한다."""

    async def _boom(*_a, **_k):
        raise OSError("connection refused (injected)")

    monkeypatch.setattr(wal_archive.asyncpg, "connect", _boom)

    with pytest.raises(OSError, match="connection refused"):
        wal_archive.main(["--dsn", "postgresql://x/y"])


# --- negative: verify-write path with non-directory path ----------------------


def test_main_exits_1_when_verify_write_path_is_file(monkeypatch, tmp_path, capsys):
    """--verify-write에 파일을 넘기면 실제 verify 경로가 타임아웃 위반(종료코드 1).

    디렉터리가 존재하지만 파일인 경로는 glob("*")이 빈 집합을 돌려
    pg_switch_wal() 이후에도 새 파일이 나타나지 않는 결과와 같다.
    """
    _healthy_settings(monkeypatch)
    _unmocked_verify(monkeypatch)
    regular_file = tmp_path / "not_a_dir"
    regular_file.write_text("I am a file")

    code = wal_archive.main(["--dsn", "postgresql://x/y", "--verify-write", str(regular_file)])

    assert code == 1
    assert "새 파일" in capsys.readouterr().err


# --- failure injection: PermissionError from list_dir (Path.glob) ------------


def test_verify_raises_PermissionError_from_list_dir(monkeypatch, tmp_path):
    """_default_list_dir의 Path.glob가 PermissionError를 던지면 verify_archiving_advances가
    예외를 전파한다 -- 실패 모드를 실제 fs 호출 대상으로 단언."""

    def _boom_list_dir(_d):
        raise PermissionError("Permission denied (injected)")

    with pytest.raises(PermissionError, match="Permission denied"):
        wal_archive.verify_archiving_advances(
            "postgresql://x/y",
            tmp_path,
            switch_wal=lambda _dsn: None,
            list_dir=_boom_list_dir,
            sleep=lambda _s: None,
            clock=_fast_clock(),
        )


# --- negative: 0-byte WAL file (content check not implemented) ----------------


def test_verify_treats_zero_byte_wal_as_new_file_no_content_check():
    """0바이트 WAL 이름 파일은 이름만 보는 현재 코드가 '새 파일'로 판정한다.

    현재 verify_archiving_advances/list_dir는 파일 이름만 set에 담고
    내용을 검사하지 않는다. 따라서 0바이트 파일이 아카이브 디렉터리에
    있으면 glob("*")이 그 이름을 돌려 after-before가 비어 있지 않게 되고
    check_archive_advanced()는 None(정상)을 반환한다.

    내용 검사가 구현되지 않았음을 명시적으로 고정한다.
    """
    # check_archive_advanced는 이름만 비교 — 0바이트 파일 이름이
    # after set에 있으면 새 파일로 판정(내용 검사는 하지 않음)
    result = wal_archive.check_archive_advanced(
        {"000000010000000000000001"},
        {"000000010000000000000001", "000000010000000000000002"},
    )
    # 이름만 보면 새 파일이 있으므로 None(정상) — 내용 검사는 하지 않음
    assert result is None, (
        "0-byte WAL file is treated as new file (name-only check, no content validation)"
    )


# --- negative: archive_command whitespace-only -------------------------------


def test_evaluate_flags_whitespace_only_archive_command():
    """archive_command가 공백만("  ")이면 strip() 후 빈 값으로 위반 1건."""
    settings = {"wal_level": "replica", "archive_mode": "on", "archive_command": "  "}
    issues = wal_archive.evaluate(settings)
    assert len(issues) == 1
    assert "archive_command" in issues[0]
    assert "비어" in issues[0]
