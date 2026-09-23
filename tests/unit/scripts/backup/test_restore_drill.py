"""scripts/backup/restore_drill.py 단위 테스트 -- H-4(task-2609, ADR-2026-09-09-B).

DoD: 실패 주입 시(각 단계별) ok=False가 나오고, 성공 경로에서는 모든 단계가 기록되며
정리(stop_postgres)가 항상 실행되는지를 실제 Postgres/pg_ctl 없이 검증한다
(run_cmd/which/find_backup/sleep/clock 전부 주입).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.backup import restore_drill


def _fake_backup_dir(tmp_path: Path) -> Path:
    backup = tmp_path / "fake-backup"
    (backup / "base").mkdir(parents=True)
    (backup / "base" / "PG_VERSION").write_text("16", encoding="utf-8")
    return backup / "base"


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
            # DROP_SLOT 쿼리: "pg_drop_replication_slot" 포함
            if "pg_drop_replication_slot" in cmd[-1]:
                return drop_slot_rc, "DROP SLOT"
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
        last_failed_restore_dir=tmp_path / "last_failed_restore",
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
    for step in (
        "find_backup",
        "restore_files",
        "start_postgres",
        "wait_recovery",
        "replay_verify",
        "stop_postgres",
        "drop_replication_slot",
    ):
        assert step in result["steps"], f"missing step: {step}"
        assert result["steps"][step]["ok"] is True, result["steps"]
    stop_calls = [c for c in calls if c[0] == "pg_ctl" and c[1] == "stop"]
    assert len(stop_calls) == 1  # 성공해도 임시 인스턴스는 반드시 내린다
    # DROP_SLOT 쿼리가 호출되었는지 확인
    drop_calls = [
        c for c in calls
        if c[0] == "psql" and "pg_drop_replication_slot" in c[-1]
    ]
    assert len(drop_calls) == 1
    assert not (tmp_path / "restore_pgdata").exists()  # 임시 데이터 디렉터리는 정리된다


def test_start_postgres_failure_stops_drill_without_stopping_unstarted_server(tmp_path: Path):
    run_cmd, calls = _dispatching_run_cmd(start_rc=1)
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is False
    assert result["steps"]["start_postgres"]["ok"] is False
    assert "wait_recovery" not in result["steps"]
    # 못 띄운 서버를 내리려 하지 않는다
    assert not any(c[0] == "pg_ctl" and c[1] == "stop" for c in calls)
    # 서버가 안 떴어도 finally 에서 DROP_SLOT 은 호출된다
    drop_calls = [
        c for c in calls
        if c[0] == "psql" and "pg_drop_replication_slot" in c[-1]
    ]
    assert len(drop_calls) == 1
    assert result["steps"]["drop_replication_slot"]["ok"] is True


def test_drop_slot_called_when_existing_slot_exists(tmp_path: Path):
    """기존 복제 슬롯 aios_drill 이 있을 때 finally 에서 DROP_SLOT 쿼리를 호출한다."""

    run_cmd, calls = _dispatching_run_cmd()
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is True
    drop_calls = [
        c for c in calls
        if c[0] == "psql" and "pg_drop_replication_slot" in c[-1]
    ]
    assert len(drop_calls) == 1
    # DROP_SLOT 쿼리가 성공(ok=True)으로 기록됨
    assert result["steps"]["drop_replication_slot"]["ok"] is True
    assert result["steps"]["drop_replication_slot"]["rc"] == 0


def test_drop_slot_failure_still_cleans_restore_data_dir(tmp_path: Path):
    """DROP_SLOT 이 실패(rc≠0)해도 restore_data_dir 는 정리되고 steps 에 실패 기록된다."""

    run_cmd, calls = _dispatching_run_cmd(drop_slot_rc=1)
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    # 서버 기동→복구→replay_verify 는 ok지만 DROP_SLOT 이 실패하면 전체 ok=False
    assert result["ok"] is False
    assert result["steps"]["start_postgres"]["ok"] is True
    assert result["steps"]["wait_recovery"]["ok"] is True
    assert result["steps"]["replay_verify"]["ok"] is True
    # DROP_SLOT 은 실패했지만
    assert result["steps"]["drop_replication_slot"]["ok"] is False
    assert result["steps"]["drop_replication_slot"]["rc"] == 1
    # 임시 데이터 디렉터리는 여전히 정리된다
    assert not (tmp_path / "restore_pgdata").exists()


def test_start_postgres_failure_captures_diagnostic_logs_and_preserves_them(tmp_path: Path):
    """task-4978: start_postgres 실패 시 pg_ctl_start.log 전체와 postgres 로그 tail이
    steps.start_postgres.detail에 남고, 정리(rmtree) 전에 last_failed_restore_dir로
    복사된다 -- restore_data_dir는 드릴 후 항상 지워지므로 미리 복사해두지 않으면
    원인 분석 근거가 사라진다."""

    def run_cmd(cmd, cwd, env, timeout):
        if cmd[0] == "pg_ctl" and cmd[1] == "start":
            data_dir = Path(cmd[cmd.index("-D") + 1])
            log_path = Path(cmd[-1])
            log_path.write_text(
                "FATAL: could not bind IPv4 address: Address already in use", encoding="utf-8"
            )
            log_dir = data_dir / "log"
            log_dir.mkdir(exist_ok=True)
            (log_dir / "postgresql-1.log").write_text(
                "\n".join(f"line {i}" for i in range(100)), encoding="utf-8"
            )
            return 1, "start failed"
        if cmd[0] == "psql":
            return 0, ""  # 복제 슬롯 정리 쿼리(정리 단계, 결과 무시됨)
        raise AssertionError(f"unexpected cmd {cmd}")

    kwargs = _common_kwargs(tmp_path, run_cmd=run_cmd)
    last_failed_dir = kwargs["last_failed_restore_dir"]

    result = restore_drill.run_drill(**kwargs)

    assert result["steps"]["start_postgres"]["ok"] is False
    detail = result["steps"]["start_postgres"]["detail"]
    assert "Address already in use" in detail
    assert "line 99" in detail  # 마지막 80줄 안에 포함
    assert "line 0" not in detail  # 마지막 80줄 밖은 제외

    assert (last_failed_dir / "pg_ctl_start.log").read_text(encoding="utf-8") == (
        "FATAL: could not bind IPv4 address: Address already in use"
    )
    assert (last_failed_dir / "log" / "postgresql-1.log").exists()
    # restore_data_dir 자체는 드릴 종료 후 정리된다(원본은 last_failed_dir에만 남는다)
    assert not kwargs["restore_data_dir"].exists()


def test_process_start_timeout_fails_start_postgres_without_waiting_for_recovery(
    tmp_path: Path,
):
    """서버 프로세스 자체가 뜨지 않으면(포트 충돌 등) start_postgres에서 바로 실패하고,
    recovery 대기(wait_recovery)로는 넘어가지 않는다 -- 프로세스 기동 실패와 recovery
    재생 시간 초과가 서로 다른 단계로 분류돼야 한다(task-4978)."""
    run_cmd, calls = _dispatching_run_cmd(status_rc=1)  # pg_ctl status가 계속 "안 떠 있음"
    result = restore_drill.run_drill(
        **_common_kwargs(
            tmp_path,
            run_cmd=run_cmd,
            process_start_timeout=2.0,
            process_start_poll_interval=1.0,
        )
    )

    assert result["ok"] is False
    assert result["steps"]["start_postgres"]["ok"] is False
    assert "wait_recovery" not in result["steps"]
    # 뜨지 않은 서버를 stop 하려 하지 않는다
    assert not any(c[0] == "pg_ctl" and c[1] == "stop" for c in calls)


def test_recovery_slower_than_old_60s_process_start_timeout_still_succeeds(tmp_path: Path):
    """회귀 방지: 프로세스는 즉시 뜨지만(WAL replay 중이라 아직 연결은 거부) replay가
    이전 pg_ctl -t 60 예산을 넘겨도, replay가 recovery_poll_timeout 안에만 끝나면 드릴은
    성공해야 한다 -- start_postgres(-t)와 recovery 완료 대기가 분리됐기 때문이다(task-4978)."""
    recovery_outputs = iter(["t", "t", "t", "f"])

    def run_cmd(cmd, cwd, env, timeout):
        calls_list.append(cmd)
        if cmd[0] == "pg_ctl" and cmd[1] == "start":
            return 0, "server started"
        if cmd[0] == "pg_ctl" and cmd[1] == "status":
            return 0, "server is running"
        if cmd[0] == "pg_ctl" and cmd[1] == "stop":
            return 0, "server stopped"
        if cmd[0] == "psql" and "pg_is_in_recovery" in cmd[-1]:
            return 0, next(recovery_outputs)
        if cmd[0] == "psql":
            return 0, ""  # 복제 슬롯 정리 쿼리
        if cmd[0] == "python" and "replay_verify.py" in cmd[-1]:
            return 0, "replay done"
        raise AssertionError(f"unexpected cmd {cmd}")

    calls_list: list = []
    result = restore_drill.run_drill(
        **_common_kwargs(
            tmp_path,
            run_cmd=run_cmd,
            recovery_poll_timeout=300.0,
            recovery_poll_interval=1.0,
            process_start_timeout=30.0,
        )
    )

    assert result["ok"] is True
    assert result["steps"]["wait_recovery"]["ok"] is True
    assert result["steps"]["replay_verify"]["ok"] is True


def test_recovery_timeout_still_stops_server(tmp_path: Path):
    run_cmd, calls = _dispatching_run_cmd(recovery_rc=0, recovery_out="t")  # 계속 recovery 중
    result = restore_drill.run_drill(
        **_common_kwargs(
            tmp_path,
            run_cmd=run_cmd,
            recovery_poll_timeout=3.0,
            recovery_poll_interval=1.0,
        )
    )

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


def test_preserve_failed_restore_logs_does_not_raise_when_source_missing(tmp_path: Path):
    dest = tmp_path / "last_failed_restore"
    restore_drill.preserve_failed_restore_logs(tmp_path / "does_not_exist", dest)
    assert dest.exists()  # dest는 만들지만 없는 파일 복사는 조용히 건너뛴다


def test_with_port_swaps_port_keeps_host_and_user():
    assert restore_drill.with_port("postgresql://alice@dbhost:5432/aios_dev", 15433) == (
        "postgresql://alice@dbhost:15433/aios_dev"
    )


def test_with_port_works_without_userinfo():
    assert restore_drill.with_port("postgresql://dbhost/aios_dev", 15433) == (
        "postgresql://dbhost:15433/aios_dev"
    )


def test_write_recovery_config_creates_signal_and_restore_command(tmp_path: Path):
    """restore_command가 recovery.signal과 함께 postgresql.auto.conf에 기록됨을 검증한다.

    Windows(os.name == "nt")일 때 copy, Unix일 때 cp 명령이 선택되는지 플랫폼별 케이스로
    분리 검증한다. 전체 archive_dir 경로(str(archive_dir) → forward slash 변환)가 conf에
    포함되고 백슬래시가 없어야 한다.
    """
    data_dir = tmp_path / "pgdata"
    data_dir.mkdir()
    archive_dir = tmp_path / "archive"

    restore_drill.write_recovery_config(data_dir, archive_dir)

    assert (data_dir / "recovery.signal").exists()
    conf = (data_dir / "postgresql.auto.conf").read_text(encoding="utf-8")
    assert "restore_command" in conf
    # 전체 archive_dir 경로(forward slash 변환 후)가 conf에 포함됨
    expected_path = str(archive_dir).replace("\\", "/")
    assert expected_path in conf, f"전체 경로 '{expected_path}'이 conf에 없음"
    assert "\\" not in conf
    # 플랫폼별 명령어 검증: Windows→copy, Unix→cp
    if os.name == "nt":
        assert 'copy' in conf, "Windows에서 restore_command는 copy 명령어야 한다"
    else:
        assert 'cp' in conf, "Unix에서 restore_command는 cp 명령어야 한다"


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
        f"전체 경로 '{expected_path}'이 conf에 없음 — "
        "restore_command가 잘못된 경로로 WAL을 찾는다"
    )
    # 백슬래시가 남아있지 않아야 함
    assert "\\" not in conf_content or '""' in conf_content  # double-quote 내부면 허용


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
