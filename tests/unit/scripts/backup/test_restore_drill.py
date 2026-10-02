"""scripts/backup/restore_drill.py 단위 테스트 -- H-4(task-2609, ADR-2026-09-09-B).

DoD: 실패 주입 시(각 단계별) ok=False가 나오고, 성공 경로에서는 모든 단계가 기록되며
정리(stop_postgres)가 항상 실행되는지를 실제 Postgres/pg_ctl 없이 검증한다
(run_cmd/which/find_backup/sleep/clock 전부 주입).

분리된 파일:
- `test_restore_drill_copy_extract.py` — 복사·tar 추출 경로 테스트
- `test_restore_drill_helpers.py` — 순수 헬퍼 함수(wait_for_process_start 등) 테스트
- `test_restore_drill_platform.py` — 플랫폼 특이성(pg_ctl -l, libpq_dsn) 테스트
"""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

from scripts.backup import restore_drill


def _fake_backup_dir(tmp_path: Path) -> Path:
    backup = tmp_path / "fake-backup"
    (backup / "base").mkdir(parents=True)
    (backup / "base" / "PG_VERSION").write_text("16", encoding="utf-8")
    return backup / "base"


def _fake_copy_tree(src: Path, dst: Path, timeout: float) -> tuple[bool, str]:
    """단위 테스트용 restore_files 단계 -- 실제 robocopy 서브프로세스를 스폰하지 않고
    plain shutil.copytree로 대체한다(테스트 픽스처는 파일 몇 개뿐이라 실제 robocopy
    호출은 매 테스트마다 초당 프로세스 기동 비용만 추가하고 아무것도 검증하지 못한다
    -- _copy_backup_tree 자체의 동작은 별도 단위 테스트로 검증한다)."""
    try:
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
            # DROP_SLOT 쿼리: "pg_drop_replication_slot" 포함
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
    drop_calls = [c for c in calls if c[0] == "psql" and "pg_drop_replication_slot" in c[-1]]
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
    drop_calls = [c for c in calls if c[0] == "psql" and "pg_drop_replication_slot" in c[-1]]
    assert len(drop_calls) == 1
    assert result["steps"]["drop_replication_slot"]["ok"] is True


def test_drop_slot_called_when_existing_slot_exists(tmp_path: Path):
    """기존 복제 슬롯 aios_drill 이 있을 때 finally 에서 DROP_SLOT 쿼리를 호출한다."""

    run_cmd, calls = _dispatching_run_cmd()
    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is True
    drop_calls = [c for c in calls if c[0] == "psql" and "pg_drop_replication_slot" in c[-1]]
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
        if cmd[0] == "python" and "scripts.replay_verify" in cmd[-1]:
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


def test_replay_verify_invoked_as_module_not_bare_script(tmp_path: Path):
    """task-9912 근본 원인 재현: task-7877(replay_verify.py LOC 압축)이 `from scripts import
    replay_verify_db_pressure`를 도입한 뒤로, `python scripts/replay_verify.py`처럼 파일
    경로로 직접 실행하면 sys.path[0]이 scripts/ 자신이 돼 `scripts` 패키지를 못 찾고
    ImportError로 죽는다(실제 드릴 재현: rc=1, "cannot import name 'replay_verify_db_pressure'
    from 'scripts'"). `-m scripts.replay_verify`로 모듈 실행해야 repo_root가 sys.path에
    남아 패키지 임포트가 깨지지 않는다 -- 이 테스트는 바로 그 잘못된 호출 형태로
    되돌아가면 실패 주입으로 잡는다."""

    def run_cmd(cmd, cwd, env, timeout):
        if cmd[0] == "pg_ctl" and cmd[1] == "start":
            return 0, "server started"
        if cmd[0] == "pg_ctl" and cmd[1] == "status":
            return 0, "server is running"
        if cmd[0] == "pg_ctl" and cmd[1] == "stop":
            return 0, "server stopped"
        if cmd[0] == "psql" and "pg_is_in_recovery" in cmd[-1]:
            return 0, "f"
        if cmd[0] == "psql":
            return 0, ""  # 복제 슬롯 정리 쿼리
        if cmd[0] == "python":
            # 실제 replay_verify.py의 회귀 재현: 파일 경로 직접 실행(bare .py)이면
            # ImportError, 모듈 형태(-m scripts.replay_verify)면 성공.
            if cmd[1:] == ["scripts/replay_verify.py"]:
                return (
                    1,
                    "ImportError: cannot import name 'replay_verify_db_pressure' "
                    "from 'scripts' (unknown location)",
                )
            if cmd[1:] == ["-m", "scripts.replay_verify"]:
                return 0, "replay done"
        raise AssertionError(f"unexpected cmd {cmd}")

    result = restore_drill.run_drill(**_common_kwargs(tmp_path, run_cmd=run_cmd))

    assert result["ok"] is True, result["steps"]
    assert result["steps"]["replay_verify"]["ok"] is True
