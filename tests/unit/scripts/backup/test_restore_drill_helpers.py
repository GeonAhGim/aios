"""scripts/backup/restore_drill.py 순수 헬퍼 함수 단위 테스트.

`wait_for_process_start`, `collect_start_failure_logs`, `preserve_failed_restore_logs`,
`wait_for_recovery` 등 외부 상태 의존성 없는 순수 함수를 검증한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.backup import restore_drill

# --- fixtures (test_restore_drill.py에서 중복 import) ---------------------------


def _dispatching_run_cmd(
    *,
    start_rc=0,
    status_rc=0,
    recovery_rc=0,
    recovery_out="f",
    replay_rc=0,
    stop_rc=0,
    drop_slot_rc=0,
    calls=None,
):
    calls = calls if calls is not None else []

    def run_cmd(cmd, cwd, env, timeout):
        calls.append(cmd)
        if cmd[0] == "pg_ctl" and cmd[1] == "start":
            return start_rc, "server started"
        if cmd[0] == "pg_ctl" and cmd[1] == "status":
            return status_rc, "server is running" if status_rc == 0 else "no server running"
        if cmd[0] == "pg_ctl" and cmd[1] == "stop":
            return stop_rc, "server stopped"
        if cmd[0] == "psql":
            if "pg_drop_replication_slot" in cmd[-1]:
                return drop_slot_rc, "DROP SLOT"
            return recovery_rc, recovery_out
        if cmd[0] == "python" and "scripts.replay_verify" in cmd[-1]:
            return replay_rc, "replay done"
        raise AssertionError(f"unexpected cmd {cmd}")

    return run_cmd, calls


# --- wait_for_process_start ---------------------------------------------------


def test_wait_for_process_start_returns_none_when_status_ok():
    reason = restore_drill.wait_for_process_start(
        Path("."),
        pg_ctl_bin="pg_ctl",
        run_cmd=lambda cmd, cwd, env, timeout: (0, "server is running"),
        cwd=Path("."),
        timeout=10.0,
        poll_interval=1.0,
        sleep=lambda s: None,
        clock=iter([0.0]).__next__,
    )
    assert reason is None


def test_wait_for_process_start_times_out_when_process_never_appears():
    reason = restore_drill.wait_for_process_start(
        Path("."),
        pg_ctl_bin="pg_ctl",
        run_cmd=lambda cmd, cwd, env, timeout: (1, "no server running"),
        cwd=Path("."),
        timeout=3.0,
        poll_interval=1.0,
        sleep=lambda s: None,
        clock=iter([0.0, 1.0, 2.0, 4.0, 4.0]).__next__,
    )
    assert reason is not None


# --- collect_start_failure_logs ------------------------------------------------


def test_collect_start_failure_logs_includes_full_pg_ctl_log_and_tailed_server_log(
    tmp_path: Path,
):
    data_dir = tmp_path / "restore_pgdata"
    data_dir.mkdir()
    (data_dir / "pg_ctl_start.log").write_text("pg_ctl 전체 내용", encoding="utf-8")
    log_dir = data_dir / "log"
    log_dir.mkdir()
    (log_dir / "postgresql-1.log").write_text(
        "\n".join(f"line {i}" for i in range(100)), encoding="utf-8"
    )

    detail = restore_drill.collect_start_failure_logs(data_dir)

    assert "pg_ctl 전체 내용" in detail
    assert "line 99" in detail
    assert "line 20" in detail  # 마지막 80줄(20~99) 안
    assert "line 19" not in detail  # 마지막 80줄 밖


def test_collect_start_failure_logs_handles_missing_pg_ctl_log(tmp_path: Path):
    data_dir = tmp_path / "restore_pgdata"
    data_dir.mkdir()

    detail = restore_drill.collect_start_failure_logs(data_dir)

    assert "없음" in detail


# --- preserve_failed_restore_logs ----------------------------------------------


def test_preserve_failed_restore_logs_copies_pg_ctl_log_and_server_log(tmp_path: Path):
    data_dir = tmp_path / "restore_pgdata"
    data_dir.mkdir()
    (data_dir / "pg_ctl_start.log").write_text("pg_ctl log", encoding="utf-8")
    log_dir = data_dir / "log"
    log_dir.mkdir()
    (log_dir / "postgresql-1.log").write_text("server log", encoding="utf-8")
    dest = tmp_path / "last_failed_restore"

    restore_drill.preserve_failed_restore_logs(data_dir, dest)

    assert (dest / "pg_ctl_start.log").read_text(encoding="utf-8") == "pg_ctl log"
    assert (dest / "log" / "postgresql-1.log").read_text(encoding="utf-8") == "server log"


def test_preserve_failed_restore_logs_creates_parent_dirs(tmp_path: Path):
    data_dir = tmp_path / "restore_pgdata"
    data_dir.mkdir()
    (data_dir / "pg_ctl_start.log").write_text("x", encoding="utf-8")
    dest = tmp_path / "a" / "b" / "last_failed_restore"

    restore_drill.preserve_failed_restore_logs(data_dir, dest)

    assert (dest / "pg_ctl_start.log").exists()


# --- write_recovery_config (pure) ----------------------------------------------


def test_write_recovery_config_escapes_windows_backslashes(tmp_path: Path):
    """Windows 경로에 백슬래시가 들어와도 restore_command에 control character(백스페이스 등)가
    섞이지 않고 전체 경로가 그대로 보존됨을 확인한다.

    Regression: task-4073 -- PostgreSQL GUC 파서가 백슬래시 시퀀스를 C-스타일 escape로
    해석하는 문제(\\b -> backspace 0x08)로 WAL 복원이 0건 반복되던 원인 수정.
    """

    # Windows 경로 스타일 (실제 백슬래시 포함)
    archive_dir = tmp_path / "C:\\aios\\backup_runtime\\wal_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_path / "restore_pgdata"
    data_dir.mkdir(parents=True, exist_ok=True)

    restore_drill.write_recovery_config(data_dir, archive_dir)

    conf_content = (data_dir / "postgresql.auto.conf").read_text(encoding="utf-8")
    # Control character(0x00~0x1F)가 없어야 함
    for i, ch in enumerate(conf_content):
        code = ord(ch)
        if code < 0x20 and code not in (0x0A, 0x0D):  # newline, cr는 허용
            pytest.fail(
                f"restore_command에 control character U+{code:04X} 발견 "
                f"(위치 {i}): 경로 corruption 원인"
            )
    # 플랫폼별 조건: Windows→copy, Unix→cp
    if os.name == "nt":
        assert "copy" in conf_content, "Windows 경로에서 copy 명령어야 함"
    else:
        assert "cp" in conf_content, "Unix 환경에서는 cp 명령어야 함"
    assert "wal_archive" in conf_content
    # 전체 archive_dir 경로(forward slash 변환됨)가 conf에 포함되는지 명시적 검증
    expected_path = str(archive_dir).replace("\\", "/")
    assert expected_path in conf_content, (
        f"전체 경로 '{expected_path}'이 conf에 없음 — restore_command가 잘못된 경로로 WAL을 찾는다"
    )
    # 백슬래시가 남아있지 않아야 함
    assert "\\" not in conf_content or '""' in conf_content  # double-quote 내부면 허용


# --- wait_for_recovery ---------------------------------------------------------


def test_wait_for_recovery_returns_none_when_recovery_complete():
    reason = restore_drill.wait_for_recovery(
        "postgresql://x/y",
        psql_bin="psql",
        run_cmd=lambda cmd, cwd, env, timeout: (0, "f"),
        cwd=Path("."),
        timeout=10.0,
        poll_interval=1.0,
        sleep=lambda s: None,
        clock=iter([0.0]).__next__,
    )
    assert reason is None


def test_wait_for_recovery_times_out_while_still_in_recovery():
    reason = restore_drill.wait_for_recovery(
        "postgresql://x/y",
        psql_bin="psql",
        run_cmd=lambda cmd, cwd, env, timeout: (0, "t"),
        cwd=Path("."),
        timeout=3.0,
        poll_interval=1.0,
        sleep=lambda s: None,
        clock=iter([0.0, 1.0, 2.0, 4.0, 4.0]).__next__,
    )
    assert reason is not None
