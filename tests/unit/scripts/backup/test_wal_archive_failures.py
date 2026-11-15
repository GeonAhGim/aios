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
