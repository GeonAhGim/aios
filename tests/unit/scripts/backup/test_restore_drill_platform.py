"""scripts/backup/restore_drill.py 플랫폼/유틸리티 테스트.

`pg_ctl_start_includes_logfile_flag`, `write_report`, `write_recovery_config`,
`libpq_dsn`, `run_drill_fails_fast` 등 플랫폼 특이성·유틸리티 함수를 검증한다.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from scripts.backup import restore_drill

# --- fixtures (test_restore_drill.py에서 중복 import) ---------------------------


def _fake_backup_dir(tmp_path: Path) -> Path:
    backup = tmp_path / "fake-backup"
    (backup / "base").mkdir(parents=True)
    (backup / "base" / "PG_VERSION").write_text("16", encoding="utf-8")
    return backup / "base"


def _fake_copy_tree(src: Path, dst: Path, timeout: float) -> tuple[bool, str]:
    try:
        import shutil

        shutil.copytree(src, dst)
        return True, ""
    except OSError as exc:
        return False, str(exc)


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


def _common_kwargs(tmp_path: Path, **overrides):
    kwargs = dict(
        backup_dir=tmp_path / "backups",
        archive_dir=tmp_path / "archive",
        restore_data_dir=tmp_path / "restore_pgdata",
        restore_port=15433,
        dsn_template="postgresql://user@dbhost:5432/aios_dev",
        repo_root=tmp_path,
        which=lambda _b: "/usr/bin/" + _b,
        find_backup=lambda _d: _fake_backup_dir(tmp_path),
        sleep=lambda s: None,
        clock=itertools.count(0.0, 1.0).__next__,
        pg_ctl_bin="pg_ctl",
        psql_bin="psql",
        python_bin="python",
        last_failed_restore_dir=tmp_path / "last_failed_restore",
        copy_tree=_fake_copy_tree,
    )
    kwargs.update(overrides)
    return kwargs


# --- pg_ctl start logfile flag -------------------------------------------------


def test_pg_ctl_start_includes_logfile_flag_on_windows(tmp_path: Path):
    """Windows pipe-deadlock 회피: pg_ctl start 명령에 -l 플래그가 반드시 포함됨을 확인한다."""
    run_cmd, calls = _dispatching_run_cmd()
    restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    start_calls = [c for c in calls if c[0] == "pg_ctl" and c[1] == "start"]
    assert len(start_calls) == 1
    cmd = start_calls[0]
    assert "-l" in cmd, "pg_ctl start에 -l 플래그가 없어서 Windows pipe 데드락이 발생할 수 있다"
    log_idx = cmd.index("-l")
    log_path = cmd[log_idx + 1]
    assert log_path.endswith("pg_ctl_start.log")


def test_write_report_writes_json_to_every_path(tmp_path: Path):
    result = {"ok": True, "steps": {}}
    paths = [tmp_path / "a" / "drill_latest.json", tmp_path / "b" / "drill_latest.json"]

    restore_drill.write_report(result, paths)

    for p in paths:
        assert json.loads(p.read_text(encoding="utf-8")) == result


def test_write_report_does_not_raise_when_one_path_unwritable(tmp_path: Path):
    unwritable_parent = tmp_path / "blocked"
    unwritable_parent.write_text("i am a file, not a dir", encoding="utf-8")
    ok_path = tmp_path / "ok" / "drill_latest.json"

    restore_drill.write_report({"ok": False}, [unwritable_parent / "drill_latest.json", ok_path])

    assert json.loads(ok_path.read_text(encoding="utf-8")) == {"ok": False}


def test_pg_ctl_start_includes_log_file_flag_for_windows(tmp_path: Path):
    """pg_ctl start 호출에 -l 플래그(로그 파일 경로)가 반드시 포함됨을 확인한다.

    Windows에서 capture_output=True 로 pg_ctl start 를 호출하면 postgres(daemonized child)가
    stdout/stderr PIPE handle 을 물려받아 Python 의 communicate() 가 EOF 를 영원히 못 받는
    데드락이 발생한다. -l 플래그로 로그를 파일로 직접 리다이렉트하면 handle 상속 체인이
    끊겨 이 문제가 해결된다(task-3933).
    """

    run_cmd, calls = _dispatching_run_cmd()
    restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    # pg_ctl start 호출 하나만 추출
    start_calls = [c for c in calls if c[0] == "pg_ctl" and c[1] == "start"]
    assert len(start_calls) == 1, f"expected 1 start call, got {len(start_calls)}"
    start_cmd = start_calls[0]

    # -l 플래그와 로그 파일 경로가 포함되어야 함
    assert "-l" in start_cmd, (
        "pg_ctl start 에 -l 플래그가 없다 — Windows pipe-inheritance 데드락 원인"
    )
    log_idx = start_cmd.index("-l")
    log_path = Path(start_cmd[log_idx + 1])
    assert log_path.name == "pg_ctl_start.log"
    assert log_path.parent == tmp_path / "restore_pgdata"


# --- libpq_dsn -----------------------------------------------------------------


def test_libpq_dsn_normalizes_sqlalchemy_scheme():
    """CTO 2026-09-23: postgresql+asyncpg:// DSN이 psql에 그대로 전달돼 슬롯 정리가 인증 실패."""
    assert (
        restore_drill.libpq_dsn("postgresql+asyncpg://u:p@dbhost:5432/aios_dev")
        == "postgresql://u:p@dbhost:5432/aios_dev"
    )
    assert restore_drill.libpq_dsn("postgresql://u@h/db") == "postgresql://u@h/db"


# --- run_drill failure injection -----------------------------------------------


def test_run_drill_fails_fast_when_copy_tree_fails(tmp_path: Path):
    """restore_files 복사가 실패하면(로컬 디스크 가득/robocopy 오류 등) 뒤 단계(pg_ctl
    기동 등)로 넘어가지 않고 즉시 실패로 끝난다 -- find_backup_없음과 동일한 단락 계약."""

    def failing_copy_tree(src, dst, timeout):
        return False, "디스크 공간 부족(시뮬레이션)"

    result = restore_drill.run_drill(**_common_kwargs(tmp_path, copy_tree=failing_copy_tree))

    assert result["ok"] is False
    assert result["steps"]["restore_files"]["ok"] is False
    assert result["steps"]["restore_files"]["detail"] == "디스크 공간 부족(시뮬레이션)"
    assert "start_postgres" not in result["steps"]


def test_write_recovery_config_isolates_scratch_instance(tmp_path: Path):
    """CTO 2026-09-30(task-9469): 리허설 사본은 운영 아카이브에 쓰지 않고(archive_mode=off),
    백업의 타임라인만 재생하며(recovery_target_timeline=current), 기동 fsync를 건너뛴다."""
    data_dir = tmp_path / "pgdata"
    data_dir.mkdir()
    (data_dir / "postgresql.auto.conf").write_text("archive_mode = on\n", encoding="utf-8")
    restore_drill.write_recovery_config(data_dir, tmp_path / "archive")
    lines = (data_dir / "postgresql.auto.conf").read_text(encoding="utf-8").splitlines()
    # postgresql.auto.conf는 같은 키의 마지막 값이 이긴다 — 원본의 on 뒤에 off가 와야 한다
    assert [x for x in lines if x.startswith("archive_mode")][-1] == "archive_mode = off"
    assert "recovery_target_timeline = 'current'" in lines and "fsync = off" in lines
