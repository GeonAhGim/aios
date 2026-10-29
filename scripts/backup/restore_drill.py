"""H-4(task-2609, ADR-2026-09-09-B) -- 별도 DB로 복구 리허설: 백업이 실제로 복구되는지 증명한다.

블루팀 감사(docs/blue_team/09-...md #10)가 지적한 것: "backup 있음"만으로는 부족하다.
필요한 것은 backup -> restore -> consistency verification -> healthcheck -> periodic drill의
전체 사슬이다. 이 스크립트가 그 사슬 중 restore~verification을 맡는다:

  1) base_backup.py가 만든 가장 최근 성공 베이스 백업을 찾는다.
  2) 별도 데이터 디렉터리(운영 인스턴스가 아닌 곳)에 그 파일들을 복사하고,
     archive_dir의 WAL을 replay하도록 recovery.signal + restore_command를 심는다.
  3) 별도 포트로 그 인스턴스를 기동해 recovery가 끝나기(pg_is_in_recovery() = f)를 기다린다.
  4) scripts/replay_verify.py를 그 인스턴스에 대해 그대로 돌려 이벤트 재생과 현재 테이블이
     byte-identical한지 확인한다 -- "복구됨"이 아니라 "복구된 데이터가 맞음"을 증명한다.
  5) 성공/실패와 무관하게 임시 인스턴스를 내리고 임시 데이터 디렉터리를 지운다.

결과는 JSON으로 남겨 C:\\aios\\pm\\healthcheck.py의 check_backup_drill()이 읽는다(fleet
저장소의 runtime 상태 -- OPS-16 risk_replay_latest.json과 같은 배선 방식).

실행은 저장소 루트에서 모듈 형태로만: `python -m scripts.backup.restore_drill --archive-dir ...`
(직접 실행하면 scripts/가 sys.path[0]이 되어 `from scripts.backup...`가 깨진다 --
scripts/rotate_credential_keys.py와 동일 규약).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from scripts.backup._restore_config import (  # noqa: F401 (재노출 -- 호출부는 restore_drill.* 사용)
    libpq_dsn,
    with_port,
    write_recovery_config,
)
from scripts.backup._restore_files import _restore_backup_files  # noqa: F401 (재노출)
from scripts.backup._restore_slot import drop_slot
from scripts.backup._restore_wait import (  # noqa: F401 (재노출)
    LAST_FAILED_RESTORE_DIR,
    _decode_process_output,
    collect_start_failure_logs,
    preserve_failed_restore_logs,
    wait_for_process_start,
    wait_for_recovery,
)
from scripts.backup.base_backup import latest_backup_dir, server_url

ROOT = Path(__file__).resolve().parents[2]
PM_REPORT_PATH = Path(r"C:\aios\pm") / "backup" / "drill_latest.json"
LOCAL_REPORT_PATH = ROOT / "runtime" / "backup" / "drill_latest.json"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _run(cmd: list[str], cwd: Path, env: dict | None, timeout: int) -> tuple[int, str]:
    """CTO 2026-09-23: PIPE 대신 임시 파일로 stdout/stderr를 받는다. pg_basebackup -Xs·pg_ctl은
    자식(WAL 수신기/서버)이 파이프 핸들을 상속해 부모가 끝나도 communicate()가 EOF를 못 받아
    Windows에서 무기한 멈춘다(task-5350의 pg_ctl 데드락과 같은 부류 — 직접 실행 82초 vs
    파이프 캡처 1시간 타임아웃 재현). 파일이면 상속돼도 EOF 대기가 없다."""
    import tempfile

    try:
        with tempfile.TemporaryFile(mode="w+b") as out:
            try:
                r = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdout=out,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return 124, f"timeout {timeout}s"
            out.seek(0)
            text = _decode_process_output(out.read())
        return r.returncode, text[-4000:]
    except OSError as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def _finish(steps: dict[str, dict], started: dt.datetime) -> dict:
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": _now().isoformat(timespec="seconds"),
        "ok": bool(steps) and all(v["ok"] for v in steps.values() if not v.get("advisory")),
        "steps": steps,
    }


def run_drill(
    *,
    backup_dir: Path,
    archive_dir: Path,
    restore_data_dir: Path,
    restore_port: int,
    dsn_template: str,
    repo_root: Path = ROOT,
    pg_ctl_bin: str = "pg_ctl",
    psql_bin: str = "psql",
    python_bin: str = sys.executable,
    run_cmd: Callable[[list[str], Path, dict | None, int], tuple[int, str]] = _run,
    which: Callable[[str], str | None] = shutil.which,
    find_backup: Callable[[Path], Path | None] = latest_backup_dir,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    recovery_poll_timeout: float = 300.0,
    recovery_poll_interval: float = 2.0,
    process_start_timeout: float = 30.0,
    process_start_poll_interval: float = 1.0,
    last_failed_restore_dir: Path = LAST_FAILED_RESTORE_DIR,
    copy_tree: Callable[[Path, Path, float], tuple[bool, str]] = _restore_backup_files,
    copy_timeout: float = 300.0,
) -> dict:
    """복구 리허설 1회. 어느 단계에서 멈추든(백업 없음/기동 실패/복구 타임아웃/replay_verify
    불일치) `steps`에 실패한 단계가 남고 `ok`는 False가 된다 -- healthcheck의
    check_backup_drill()이 그대로 high finding으로 표면화한다."""
    started = _now()
    steps: dict[str, dict] = {}

    missing = [b for b in (pg_ctl_bin, psql_bin) if which(b) is None]
    if missing:
        steps["preflight"] = {"ok": False, "detail": f"실행 파일을 찾을 수 없다: {missing}"}
        return _finish(steps, started)

    backup = find_backup(backup_dir)
    if backup is None:
        steps["find_backup"] = {"ok": False, "detail": f"{backup_dir}에 ok=true 베이스 백업이 없다"}
        return _finish(steps, started)
    steps["find_backup"] = {"ok": True, "detail": str(backup)}

    # Pre-drop: a force-killed earlier run may have left the slot behind (breaks pg_basebackup -C).
    steps["pre_drop_replication_slot"] = drop_slot(psql_bin, dsn_template, repo_root, run_cmd)

    if restore_data_dir.exists():
        shutil.rmtree(restore_data_dir, ignore_errors=True)
    phase_start = clock()
    copy_ok, copy_detail = copy_tree(backup, restore_data_dir, copy_timeout)
    copy_elapsed = round(clock() - phase_start, 1)
    steps["restore_files"] = {
        "ok": copy_ok,
        "detail": copy_detail or str(restore_data_dir),
        "elapsed_s": copy_elapsed,
    }
    print(
        f"[drill] restore_files {'ok' if copy_ok else 'FAILED'} in {copy_elapsed}s", file=sys.stderr
    )
    if not copy_ok:
        return _finish(steps, started)

    write_recovery_config(restore_data_dir, archive_dir)
    # CTO 2026-09-23: DATABASE_URL은 SQLAlchemy 스킴(postgresql+asyncpg://)이라 psql이 파싱하지
    # 못해 기본값(localhost:5432, OS 사용자)으로 붙다가 인증 실패 — 슬롯 정리 단계가 매번
    # 실패해 stale 슬롯 aios_drill이 남았다(다음 리허설의 -C 충돌 원인). libpq 스킴으로 정규화.
    dsn_template = libpq_dsn(dsn_template)
    restore_dsn = with_port(dsn_template, restore_port)
    started_server = False
    # task-5203: pg_ctl/psql이 이 PC(cp949 로케일)에서 실패 메시지를 OS 코드페이지로
    # 내보내는데 collect_start_failure_logs()는 항상 utf-8(errors=replace)로 읽어
    # 실제 오류가 U+FFFD로 뭉개졌다(esc-health-backup_drill_failed). LC_ALL=C/LANG=C를
    # 주입하면 같은 실패가 ASCII 영어 메시지로 나와 디코딩 문제 자체가 사라진다
    # (이 PC에서 psql로 실측 확인).
    pg_env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        # -l <logfile> prevents Windows pipe-inheritance deadlock: when pg_ctl start
        # is called with capture_output=True (PIPE), the postgres daemon inherits the
        # stdout/stderr handle and Python's communicate() never sees EOF. Writing to
        # a file directly (official pg_ctl pattern) breaks the handle chain.
        # -w/-t는 일부러 쓰지 않는다: 이 서버는 archive recovery로 뜨는 동안(hot_standby
        # 없이는) 연결을 거부하므로, pg_ctl -w -t는 프로세스 기동이 아니라 WAL replay
        # 완료까지 기다리다 큰 백업에서 60초를 넘겨 오탐 실패를 냈다(task-4978). 대신
        # 비동기로 기동만 시키고, wait_for_process_start로 프로세스 기동만, wait_for_recovery로
        # replay 완료만 각각 따로 기다린다.
        log_path = restore_data_dir / "pg_ctl_start.log"
        phase_start = clock()
        launch_rc, launch_tail = run_cmd(
            [
                pg_ctl_bin,
                "start",
                # CTO 2026-09-23: PostgreSQL 10+는 start의 기본이 -w(서버 준비까지 대기)라
                # -w를 "안 쓰는" 것만으로는 비동기가 아니다 — archive recovery 중 연결 거부로
                # 30초 타임아웃(rc=124) 재현. -W로 명시해 즉시 반환시킨다.
                "-W",
                "-D",
                str(restore_data_dir),
                "-o",
                f"-p {restore_port}",
                "-l",
                str(log_path),
            ],
            repo_root,
            pg_env,
            30,
        )

        if launch_rc != 0:
            started_server = False
            steps["start_postgres"] = {"ok": False, "rc": launch_rc, "tail": launch_tail}
        else:
            start_reason = wait_for_process_start(
                restore_data_dir,
                pg_ctl_bin=pg_ctl_bin,
                run_cmd=run_cmd,
                cwd=repo_root,
                timeout=process_start_timeout,
                poll_interval=process_start_poll_interval,
                env=pg_env,
                sleep=sleep,
                clock=clock,
            )
            started_server = start_reason is None
            steps["start_postgres"] = {
                "ok": started_server,
                "rc": launch_rc,
                "tail": start_reason or launch_tail,
            }

        steps["start_postgres"]["elapsed_s"] = round(clock() - phase_start, 1)
        print(
            f"[drill] start_postgres {'ok' if started_server else 'FAILED'} "
            f"in {steps['start_postgres']['elapsed_s']}s",
            file=sys.stderr,
        )

        if not started_server:
            steps["start_postgres"]["detail"] = collect_start_failure_logs(restore_data_dir)
            preserve_failed_restore_logs(restore_data_dir, last_failed_restore_dir)

        if started_server:
            recovery_phase_start = clock()
            reason = wait_for_recovery(
                restore_dsn,
                psql_bin=psql_bin,
                run_cmd=run_cmd,
                cwd=repo_root,
                timeout=recovery_poll_timeout,
                poll_interval=recovery_poll_interval,
                env=pg_env,
                sleep=sleep,
                clock=clock,
            )
            recovery_elapsed = round(clock() - recovery_phase_start, 1)
            steps["wait_recovery"] = {
                "ok": reason is None,
                "detail": reason or "recovery complete",
                "elapsed_s": recovery_elapsed,
            }
            print(
                f"[drill] wait_recovery {'ok' if reason is None else 'FAILED'} "
                f"in {recovery_elapsed}s",
                file=sys.stderr,
            )

            if reason is not None:
                # task-12010: finally의 rmtree 전에 start_postgres 분기와 같이 진단 로그를 보존한다.
                steps["wait_recovery"]["detail"] = (
                    f"{reason}\n\n{collect_start_failure_logs(restore_data_dir)}"
                )
                preserve_failed_restore_logs(restore_data_dir, last_failed_restore_dir)

            if reason is None:
                env = {**pg_env, "DATABASE_URL": restore_dsn, "TEST_DATABASE_URL": restore_dsn}
                replay_phase_start = clock()
                rc, tail = run_cmd([python_bin, "-m", "scripts.replay_verify"], repo_root, env, 300)
                replay_elapsed = round(clock() - replay_phase_start, 1)
                steps["replay_verify"] = {
                    "ok": rc == 0,
                    "rc": rc,
                    "tail": tail,
                    "elapsed_s": replay_elapsed,
                }
                print(
                    f"[drill] replay_verify {'ok' if rc == 0 else 'FAILED'} in {replay_elapsed}s",
                    file=sys.stderr,
                )
    finally:
        # --- 서버 중지 (실행 중이었으면) ---
        if started_server:
            rc, tail = run_cmd(
                [pg_ctl_bin, "stop", "-D", str(restore_data_dir), "-m", "fast"],
                repo_root,
                pg_env,
                60,
            )
            steps["stop_postgres"] = {"ok": rc == 0, "rc": rc, "tail": tail}

        # --- replication slot cleanup (success and failure paths; advisory) ---
        steps["drop_replication_slot"] = drop_slot(psql_bin, dsn_template, repo_root, run_cmd)

        # --- 임시 데이터 디렉터리 정리 (항상 실행) ---
        try:
            shutil.rmtree(restore_data_dir)
        except OSError as exc:
            steps["cleanup"] = {"ok": False, "detail": f"rmtree failed: {exc}", "advisory": True}

    return _finish(steps, started)


def write_report(result: dict, paths: list[Path]) -> None:
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    for p in paths:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(payload, encoding="utf-8")
        except OSError as e:
            print(f"경고: {p}에 결과를 쓰지 못했다: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--backup-dir",
        default=str(ROOT / "runtime" / "backup" / "base"),
        help="base_backup.py --dest-dir와 같은 경로",
    )
    parser.add_argument(
        "--archive-dir", required=True, help="wal_archive.py가 검증하는 아카이브 디렉터리"
    )
    parser.add_argument(
        "--restore-data-dir", default=str(ROOT / "runtime" / "backup" / "restore_pgdata")
    )
    parser.add_argument("--restore-port", type=int, default=15433)
    parser.add_argument("--dsn", help="생략하면 DATABASE_URL(.env)을 쓴다")
    parser.add_argument(
        "--report-path",
        action="append",
        help="결과 JSON을 남길 경로(반복 가능). 생략 시 기본 2곳(로컬 + fleet)",
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or server_url()
    result = run_drill(
        backup_dir=Path(args.backup_dir),
        archive_dir=Path(args.archive_dir),
        restore_data_dir=Path(args.restore_data_dir),
        restore_port=args.restore_port,
        dsn_template=dsn,
    )
    report_paths = (
        [Path(p) for p in args.report_path]
        if args.report_path
        else [LOCAL_REPORT_PATH, PM_REPORT_PATH]
    )
    write_report(result, report_paths)

    out = sys.stdout if result["ok"] else sys.stderr
    print(json.dumps(result, ensure_ascii=False, indent=2), file=out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
