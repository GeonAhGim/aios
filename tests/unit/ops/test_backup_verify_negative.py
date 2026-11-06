"""ops/backup_verify.py 음성(negative) 단위 테스트 -- FA-20(task-22922, ADR-2026-09-09-C).

쓰기 검증 거부·비-RuntimeError 전파·WAL 설정 조회 실패가 성공으로 위장되지 않고
뒷 단계(restore_drill)를 막는지 증명한다. 정상 경로·성능·동시성은 test_backup_verify.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ops import backup_verify


def _ok_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
    return {"ok": True, "dest": str(dest_dir / "20260910T000000Z"), "tail": "backup ok"}


def _failing_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
    return {"ok": False, "dest": "", "tail": "pg_basebackup rc=1"}


def _raising_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
    raise RuntimeError("pg_basebackup 실행 파일을 찾을 수 없습니다")


def _clean_wal(dsn: str) -> dict[str, str]:
    return {"wal_level": "replica", "archive_mode": "on", "archive_command": "cp %p /archive/%f"}


def _dirty_wal(dsn: str) -> dict[str, str]:
    return {"wal_level": "minimal", "archive_mode": "off", "archive_command": ""}


def _ok_drill(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, "steps": {"replay_verify": {"ok": True}}}


def _failing_drill(**kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "steps": {"replay_verify": {"ok": False, "detail": "mismatch"}}}


def _common_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        backup_dest_dir=tmp_path / "base",
        archive_dir=tmp_path / "archive",
        restore_data_dir=tmp_path / "restore_pgdata",
        restore_port=15433,
        dsn="postgresql://user@dbhost:5432/aios_dev",
        repo_root=tmp_path,
        base_backup=_ok_base_backup,
        read_wal=_clean_wal,
        verify_write=lambda dsn, archive_dir: None,
        drill=_ok_drill,
    )
    kwargs.update(overrides)
    return kwargs


# --- 음성(negative) 보강 -- 쓰기 검증 거부·비-RuntimeError 전파·WAL 읽기 실패 -----------


def test_negative_wal_write_verification_rejection_blocks_drill(tmp_path: Path) -> None:
    """설정은 깨끗하지만 실제 WAL 아카이빙이 전진하지 않으면(verify_write가 사유를 반환)
    wal_archive가 적색이 되고 restore_drill은 호출되지 않는다."""
    drill_called = False

    def spy_drill(**kwargs: Any) -> dict[str, Any]:
        nonlocal drill_called
        drill_called = True
        return _ok_drill(**kwargs)

    result = backup_verify.run_backup_verify(
        **_common_kwargs(
            tmp_path,
            verify_wal_write=True,
            verify_write=lambda dsn, archive_dir: "archive stalled",
            drill=spy_drill,
        )
    )

    assert result["ok"] is False
    assert result["steps"]["wal_archive"]["ok"] is False
    assert "archive stalled" in result["steps"]["wal_archive"]["detail"]
    assert "restore_drill" not in result["steps"]
    assert drill_called is False


def test_negative_non_runtime_error_from_base_backup_is_not_swallowed(tmp_path: Path) -> None:
    """RuntimeError만 단계 실패로 변환된다. 그 밖의 예외(프로그래밍 오류)는 삼키지 않고
    전파되어야 한다 -- 조용히 ok=False로 위장되면 원인이 사라진다."""

    def broken_base_backup(dest_dir: Path, dsn: str) -> dict[str, Any]:
        raise KeyError("manifest")

    with pytest.raises(KeyError):
        backup_verify.run_backup_verify(**_common_kwargs(tmp_path, base_backup=broken_base_backup))


def test_negative_wal_settings_read_failure_propagates_and_skips_drill(tmp_path: Path) -> None:
    """WAL 설정 조회 자체가 실패하면(DB 불가) 성공으로 위장하지 않고 예외가 전파되며
    복구 리허설은 시작되지 않는다."""
    drill_called = False

    def dead_read_wal(dsn: str) -> dict[str, str]:
        raise ConnectionError("db down")

    def spy_drill(**kwargs: Any) -> dict[str, Any]:
        nonlocal drill_called
        drill_called = True
        return _ok_drill(**kwargs)

    with pytest.raises(ConnectionError):
        backup_verify.run_backup_verify(
            **_common_kwargs(tmp_path, read_wal=dead_read_wal, drill=spy_drill)
        )
    assert drill_called is False
