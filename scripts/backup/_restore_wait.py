"""restore_drill.py 기동 대기 + 실패 진단 로그 수집.

ADR-2026-09-10-C 파일 정책에 따라 restore_drill.py(627줄, 500줄 경고)에서 분리됐다 -- "서버
프로세스가 떴는가/WAL replay가 끝났는가"를 폴링하는 책임과, 그 폴링이 실패했을 때 남길 진단
로그를 모으고 보존하는 책임은 run_drill의 복구 오케스트레이션 자체와는 독립된 변경 축이다.
공개 이름은 restore_drill.py가 그대로 재노출해 호출부가 바뀌지 않는다.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

LAST_FAILED_RESTORE_DIR = Path(r"C:\aios\pm") / "backup_runtime" / "last_failed_restore"


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
    """postgres 프로세스가 실제로 떠 있는지(`pg_ctl status`)만 폴링한다 -- WAL replay 완료는
    wait_for_recovery로 분리했다(이전엔 `pg_ctl start -w -t 60`이 둘 다 기다려 735MB 베이스
    백업 replay가 60초를 넘기면 정상 진행 중인데도 실패로 오분류됐다, task-4978). 정상이면
    None, 타임아웃까지 확인 안 되면 사유 문자열을 돌려준다."""
    deadline = clock() + timeout
    while True:
        rc, tail = run_cmd([pg_ctl_bin, "status", "-D", str(data_dir)], cwd, env, 30)
        if rc == 0:
            return None
        if clock() >= deadline:
            return (
                f"{timeout:.0f}초 안에 서버 프로세스가 뜨지 않았다(rc={rc}, tail={tail[-200:]!r})"
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
