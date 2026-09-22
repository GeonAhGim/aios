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
from urllib.parse import urlsplit, urlunsplit

from scripts.backup.base_backup import latest_backup_dir, server_url

ROOT = Path(__file__).resolve().parents[2]
PM_REPORT_PATH = Path(r"C:\aios\pm") / "backup" / "drill_latest.json"
LOCAL_REPORT_PATH = ROOT / "runtime" / "backup" / "drill_latest.json"
LAST_FAILED_RESTORE_DIR = Path(r"C:\aios\pm") / "backup_runtime" / "last_failed_restore"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _run(cmd: list[str], cwd: Path, env: dict | None, timeout: int) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return r.returncode, (r.stdout + r.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        return 124, f"timeout {timeout}s"


def with_port(url: str, port: int) -> str:
    """접속 URL의 host는 유지하고 port만 별도 인스턴스 것으로 바꾼다."""
    parts = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://"))
    userinfo, _, hostport = parts.netloc.rpartition("@")
    host = parts.hostname or "127.0.0.1"
    new_netloc = f"{userinfo}@{host}:{port}" if userinfo else f"{host}:{port}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


def _escape_path_for_pg_conf(path: Path) -> str:
    """PostgreSQL postgresql.conf 값에 넣기 전에 Windows 경로를 forward slash로 변환한다.

    PostgreSQL GUC 문자열 파서는 따옴표로 감싼 값 안의 백슬래시 시퀀스를
    C-스타일 escape로 해석한다(\\b -> backspace, \\a -> bell 등).
    Windows 경로에 역슬래시가 섞이면 restore_command 등에 치명적인 corruption을
    일으키므로, conf 파일에 쓰기 전에 모든 역슬래시를 forward slash로 통일한다.
    PostgreSQL 문서도 Windows 경로는 conf 파일에 forward slash로 쓰라고 권장한다.
    """
    return str(path).replace("\\", "/")


def write_recovery_config(data_dir: Path, archive_dir: Path) -> None:
    (data_dir / "recovery.signal").touch()
    # backslash -> forward slash (GUC escape corruption 방지)
    safe_archive = _escape_path_for_pg_conf(archive_dir)
    restore_command = f'cp "{safe_archive}/%f" "%p"'
    conf = data_dir / "postgresql.auto.conf"
    existing = conf.read_text(encoding="utf-8") if conf.exists() else ""
    conf.write_text(existing + f"\nrestore_command = '{restore_command}'\n", encoding="utf-8")


def _tail_lines(text: str, n: int) -> str:
    return "\n".join(text.splitlines()[-n:])


def collect_start_failure_logs(restore_data_dir: Path) -> str:
    """start_postgres 실패 시 진단용 로그를 모은다: pg_ctl_start.log 전체 +
    restore_data_dir/log/*.log(logging_collector 사용 시 postgres 자체 로그) 마지막 80줄.
    포트 충돌/복구 재생 시간 초과/권한 오류를 로그 내용으로 구분할 수 있게 한다(task-4978)."""
    parts: list[str] = []
    pg_ctl_log = restore_data_dir / "pg_ctl_start.log"
    if pg_ctl_log.exists():
        content = pg_ctl_log.read_text(encoding="utf-8", errors="replace")
        parts.append(f"--- pg_ctl_start.log (전체) ---\n{content}")
    else:
        parts.append("--- pg_ctl_start.log 없음 ---")
    log_dir = restore_data_dir / "log"
    if log_dir.is_dir():
        for log_file in sorted(log_dir.glob("*.log")):
            content = log_file.read_text(encoding="utf-8", errors="replace")
            parts.append(f"--- {log_file.name} (마지막 80줄) ---\n{_tail_lines(content, 80)}")
    return "\n\n".join(parts)


def preserve_failed_restore_logs(
    restore_data_dir: Path, dest_dir: Path = LAST_FAILED_RESTORE_DIR
) -> None:
    """정리(rmtree) 전에 실패한 드릴의 로그를 fleet 저장소로 복사한다 -- restore_data_dir는
    드릴 후 항상 지워지므로, 여기 복사해두지 않으면 원인 분석 근거가 남지 않는다(task-4978)."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        pg_ctl_log = restore_data_dir / "pg_ctl_start.log"
        if pg_ctl_log.exists():
            shutil.copy2(pg_ctl_log, dest_dir / "pg_ctl_start.log")
        log_dir = restore_data_dir / "log"
        if log_dir.is_dir():
            dest_log_dir = dest_dir / "log"
            dest_log_dir.mkdir(exist_ok=True)
            for log_file in log_dir.glob("*.log"):
                shutil.copy2(log_file, dest_log_dir / log_file.name)
    except OSError:
        pass


def wait_for_process_start(
    data_dir: Path,
    *,
    pg_ctl_bin: str,
    run_cmd: Callable[[list[str], Path, dict | None, int], tuple[int, str]],
    cwd: Path,
    timeout: float,
    poll_interval: float,
    env: dict | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """postgres 프로세스가 실제로 떠 있는지(`pg_ctl status`)만 폴링한다 -- WAL replay가
    끝나는지(pg_is_in_recovery)는 기다리지 않는다. 정상이면 None, 타임아웃까지 프로세스가
    확인되지 않으면 사유 문자열을 돌려준다.

    이전에는 `pg_ctl start -w -t 60`을 써서 -t가 '프로세스 기동'과 'WAL replay 완료'를
    함께 기다렸다 -- archive recovery 중인 서버는 replay가 끝나야 연결을 받아들이므로
    (hot_standby 없이는 recovery 중 연결이 거부된다), 735MB 베이스 백업 replay가 60초를
    넘기면 실제로는 정상 진행 중인데도 start_postgres가 실패로 오분류됐다(task-4978).
    이제 -t/이 함수의 timeout은 프로세스 기동(포트 바인딩 등)만 기다리고, replay 완료
    대기는 wait_for_recovery로 분리했다."""
    deadline = clock() + timeout
    while True:
        rc, tail = run_cmd([pg_ctl_bin, "status", "-D", str(data_dir)], cwd, env, 30)
        if rc == 0:
            return None
        if clock() >= deadline:
            return (
                f"{timeout:.0f}초 안에 서버 프로세스가 뜨지 않았다"
                f"(rc={rc}, tail={tail[-200:]!r})"
            )
        sleep(poll_interval)


def wait_for_recovery(
    dsn: str,
    *,
    psql_bin: str,
    run_cmd: Callable[[list[str], Path, dict | None, int], tuple[int, str]],
    cwd: Path,
    timeout: float,
    poll_interval: float,
    env: dict | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """`pg_is_in_recovery()`가 f가 될 때까지 psql로 폴링한다. 정상이면 None, 타임아웃까지
    끝나지 않으면 사유 문자열을 돌려준다(run_cmd/sleep/clock 주입으로 실제 대기 없이
    타임아웃 경로를 테스트할 수 있다)."""
    deadline = clock() + timeout
    while True:
        rc, tail = run_cmd([psql_bin, dsn, "-tAc", "SELECT pg_is_in_recovery();"], cwd, env, 30)
        if rc == 0 and tail.strip() == "f":
            return None
        if clock() >= deadline:
            return f"{timeout:.0f}초 안에 복구가 끝나지 않았다(rc={rc}, tail={tail[-200:]!r})"
        sleep(poll_interval)


def _finish(steps: dict[str, dict], started: dt.datetime) -> dict:
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": _now().isoformat(timespec="seconds"),
        "ok": bool(steps) and all(v["ok"] for v in steps.values()),
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

    if restore_data_dir.exists():
        shutil.rmtree(restore_data_dir, ignore_errors=True)
    try:
        shutil.copytree(backup, restore_data_dir)
        steps["restore_files"] = {"ok": True, "detail": str(restore_data_dir)}
    except OSError as e:
        steps["restore_files"] = {"ok": False, "detail": str(e)}
        return _finish(steps, started)

    write_recovery_config(restore_data_dir, archive_dir)
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
        launch_rc, launch_tail = run_cmd(
            [
                pg_ctl_bin,
                "start",
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

        if not started_server:
            steps["start_postgres"]["detail"] = collect_start_failure_logs(restore_data_dir)
            preserve_failed_restore_logs(restore_data_dir, last_failed_restore_dir)

        if started_server:
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
            steps["wait_recovery"] = {"ok": reason is None, "detail": reason or "recovery complete"}

            if reason is None:
                env = {**pg_env, "DATABASE_URL": restore_dsn, "TEST_DATABASE_URL": restore_dsn}
                rc, tail = run_cmd([python_bin, "scripts/replay_verify.py"], repo_root, env, 300)
                steps["replay_verify"] = {"ok": rc == 0, "rc": rc, "tail": tail}
    finally:
        if started_server:
            rc, tail = run_cmd(
                [pg_ctl_bin, "stop", "-D", str(restore_data_dir), "-m", "fast"],
                repo_root,
                pg_env,
                60,
            )
            steps["stop_postgres"] = {"ok": rc == 0, "rc": rc, "tail": tail}
        # 복제 슬롯 aios_drill 제거 (pg_basebackup -C -S 가 생성함)
        # 소스 서버 DSN 으로 연결해 슬롯을 정리한다.
        try:
            run_cmd(
                [
                    psql_bin,
                    dsn_template,
                    "-tAc",
                    "SELECT pg_drop_replication_slot('aios_drill') "
                    "WHERE EXISTS (SELECT 1 FROM pg_replication_slots "
                    "WHERE slot_name='aios_drill')",
                ],
                repo_root,
                None,
                30,
            )
        except OSError:
            pass  # 슬롯이 없거나 소스 서버 연결 실패 → 무시
        shutil.rmtree(restore_data_dir, ignore_errors=True)

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
