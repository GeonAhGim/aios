"""scripts/backup/restore_drill.py 단위 테스트 -- H-4(task-2609, ADR-2026-09-09-B).

DoD: 실패 주입 시(각 단계별) ok=False가 나오고, 성공 경로에서는 모든 단계가 기록되며
정리(stop_postgres)가 항상 실행되는지를 실제 Postgres/pg_ctl 없이 검증한다
(run_cmd/which/find_backup/sleep/clock 전부 주입).
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.backup import restore_drill


def _fake_backup_dir(tmp_path: Path) -> Path:
    backup = tmp_path / "fake-backup"
    (backup / "base").mkdir(parents=True)
    (backup / "base" / "PG_VERSION").write_text("16", encoding="utf-8")
    return backup / "base"


def _dispatching_run_cmd(*, start_rc=0, recovery_rc=0, recovery_out="f",
                          replay_rc=0, stop_rc=0, calls=None):
    calls = calls if calls is not None else []

    def run_cmd(cmd, cwd, env, timeout):
        calls.append(cmd)
        if cmd[0] == "pg_ctl" and cmd[1] == "start":
            return start_rc, "server started"
        if cmd[0] == "pg_ctl" and cmd[1] == "stop":
            return stop_rc, "server stopped"
        if cmd[0] == "psql":
            return recovery_rc, recovery_out
        if cmd[0] == "python" and "replay_verify.py" in cmd[-1]:
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
        clock=iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0]).__next__,
        pg_ctl_bin="pg_ctl",
        psql_bin="psql",
        python_bin="python",
    )
    kwargs.update(overrides)
    return kwargs


def test_preflight_fails_when_binary_missing(tmp_path: Path):
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, which=lambda _b: None))
    assert result["ok"] is False
    assert result["steps"]["preflight"]["ok"] is False


def test_fails_when_no_successful_backup_found(tmp_path: Path):
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, find_backup=lambda _d: None))
    assert result["ok"] is False
    assert result["steps"]["find_backup"]["ok"] is False


def test_success_path_records_all_steps_and_stops_server(tmp_path: Path):
    run_cmd, calls = _dispatching_run_cmd()
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is True
    for step in ("find_backup", "restore_files", "start_postgres", "wait_recovery",
                 "replay_verify", "stop_postgres"):
        assert result["steps"][step]["ok"] is True, result["steps"]
    stop_calls = [c for c in calls if c[0] == "pg_ctl" and c[1] == "stop"]
    assert len(stop_calls) == 1  # 성공해도 임시 인스턴스는 반드시 내린다
    assert not (tmp_path / "restore_pgdata").exists()  # 임시 데이터 디렉터리는 정리된다


def test_start_postgres_failure_stops_drill_without_stopping_unstarted_server(tmp_path: Path):
    run_cmd, calls = _dispatching_run_cmd(start_rc=1)
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is False
    assert result["steps"]["start_postgres"]["ok"] is False
    assert "wait_recovery" not in result["steps"]
    # 못 띄운 서버를 내리려 하지 않는다
    assert not any(c[0] == "pg_ctl" and c[1] == "stop" for c in calls)


def test_recovery_timeout_still_stops_server(tmp_path: Path):
    run_cmd, calls = _dispatching_run_cmd(recovery_rc=0, recovery_out="t")  # 계속 recovery 중
    result = restore_drill.run_drill(**_common_kwargs(
        tmp_path, run_cmd=run_cmd, recovery_poll_timeout=3.0, recovery_poll_interval=1.0,
    ))

    assert result["ok"] is False
    assert result["steps"]["wait_recovery"]["ok"] is False
    assert "replay_verify" not in result["steps"]
    assert any(c[0] == "pg_ctl" and c[1] == "stop" for c in calls)  # 실패해도 정리는 한다


def test_replay_verify_mismatch_fails_drill_and_still_cleans_up(tmp_path: Path):
    run_cmd, calls = _dispatching_run_cmd(replay_rc=1)
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is False
    assert result["steps"]["replay_verify"]["ok"] is False
    assert any(c[0] == "pg_ctl" and c[1] == "stop" for c in calls)


# --- 순수 헬퍼 ------------------------------------------------------------------

def test_with_port_swaps_port_keeps_host_and_user():
    assert restore_drill.with_port("postgresql://alice@dbhost:5432/aios_dev", 15433) == (
        "postgresql://alice@dbhost:15433/aios_dev"
    )


def test_with_port_works_without_userinfo():
    assert restore_drill.with_port("postgresql://dbhost/aios_dev", 15433) == (
        "postgresql://dbhost:15433/aios_dev"
    )


def test_write_recovery_config_creates_signal_and_restore_command(tmp_path: Path):
    data_dir = tmp_path / "pgdata"
    data_dir.mkdir()
    archive_dir = tmp_path / "archive"

    restore_drill.write_recovery_config(data_dir, archive_dir)

    assert (data_dir / "recovery.signal").exists()
    conf = (data_dir / "postgresql.auto.conf").read_text(encoding="utf-8")
    assert "restore_command" in conf
    assert str(archive_dir) in conf


def test_wait_for_recovery_returns_none_when_recovery_complete():
    reason = restore_drill.wait_for_recovery(
        "postgresql://x/y", psql_bin="psql",
        run_cmd=lambda cmd, cwd, env, timeout: (0, "f"),
        cwd=Path("."), timeout=10.0, poll_interval=1.0,
        sleep=lambda s: None, clock=iter([0.0]).__next__,
    )
    assert reason is None


def test_wait_for_recovery_times_out_while_still_in_recovery():
    reason = restore_drill.wait_for_recovery(
        "postgresql://x/y", psql_bin="psql",
        run_cmd=lambda cmd, cwd, env, timeout: (0, "t"),
        cwd=Path("."), timeout=3.0, poll_interval=1.0,
        sleep=lambda s: None, clock=iter([0.0, 1.0, 2.0, 4.0, 4.0]).__next__,
    )
    assert reason is not None


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
