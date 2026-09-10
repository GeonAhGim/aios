"""FA-20(task-2677, ADR-2026-09-09-C) -- PITR 복구 리허설 통합 엔트리포인트 + 스케줄.

명세(docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md FA-20)가 못박은 경로가
`ops/backup_verify.py`다. H-4(task-2609, ADR-2026-09-09-B)가 이미 PITR 사슬의 세 단계를
`scripts/backup/{base_backup,wal_archive,restore_drill}.py`로 나눠 만들어 뒀으므로, 이
스크립트는 새 로직을 추가하지 않고 그 세 단계를 한 커맨드로 순서대로 묶는 진입점만
맡는다: 베이스 백업 생성 -> WAL 아카이빙 전제조건(+선택적 실제 쓰기) 검증 -> 최신
백업으로 복구 리허설 + replay_verify. 앞 단계가 실패하면 뒷 단계는 건너뛴다(예: 베이스
백업 자체가 안 되면 복구 리허설을 돌 이유가 없다) -- 그래도 `steps`에는 실패한 단계까지만
남고 `ok`는 False로 끝난다(restore_drill.run_drill과 동일한 "부분 실패 관측 가능" 설계).

## 스케줄

운영 배포는 OS cron/Task Scheduler로 하루 1회 이 스크립트를 호출하는 것이 기본이다
(docs/runbooks/RB-09-restore.md "정기 리허설"). cron이 없는 대상을 위해
`--schedule-interval-hours`를 주면 그 주기로 무한 반복한다 -- 한 주기 전체가 예외를
던져도 프로세스는 죽지 않고 다음 주기에 재시도한다(market_data/ledger 스케줄러와 동일
설계, `src/foundation/market_data/application/scheduler.py` 참조). 기본값은 24시간으로
`healthcheck.check_backup_drill()`의 24시간 staleness 창과 맞춘다.

실행은 저장소 루트에서 모듈 형태로만: `python -m ops.backup_verify --archive-dir ...`
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from scripts.backup.base_backup import run_base_backup, server_url
from scripts.backup.restore_drill import LOCAL_REPORT_PATH, PM_REPORT_PATH, run_drill, write_report
from scripts.backup.wal_archive import (
    asyncpg_dsn,
    evaluate,
    read_wal_settings,
    verify_archiving_advances,
)

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)

DEFAULT_SCHEDULE_HOURS = 24.0


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _finish(steps: dict[str, dict], started: dt.datetime) -> dict:
    return {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": _now().isoformat(timespec="seconds"),
        "ok": bool(steps) and all(v["ok"] for v in steps.values()),
        "steps": steps,
    }


def run_backup_verify(
    *,
    backup_dest_dir: Path,
    archive_dir: Path,
    restore_data_dir: Path,
    restore_port: int,
    dsn: str,
    repo_root: Path = ROOT,
    verify_wal_write: bool = False,
    base_backup: Callable[[Path, str], dict] = run_base_backup,
    read_wal: Callable[[str], dict[str, str]] = read_wal_settings,
    verify_write: Callable[[str, Path], str | None] = verify_archiving_advances,
    drill: Callable[..., dict] = run_drill,
) -> dict:
    """세 단계(베이스 백업 -> WAL 아카이빙 검증 -> 복구 리허설)를 순서대로 실행한다.
    각 단계는 주입 가능한 콜러블이라 실제 pg_basebackup/asyncpg/pg_ctl 없이도 성공/실패
    경로를 단위 테스트로 검증할 수 있다(각 단계 자체의 세부 로직은 이미
    scripts/backup/*.py의 전용 테스트가 증명하므로 여기서는 순서·단락(short-circuit)
    만 검증 대상이다)."""
    started = _now()
    steps: dict[str, dict] = {}

    try:
        manifest = base_backup(backup_dest_dir, dsn)
    except RuntimeError as e:
        steps["base_backup"] = {"ok": False, "detail": str(e)}
        return _finish(steps, started)
    steps["base_backup"] = {"ok": bool(manifest["ok"]), "detail": manifest.get("tail", "")}
    if not manifest["ok"]:
        return _finish(steps, started)

    settings = read_wal(asyncpg_dsn(dsn))
    issues = evaluate(settings)
    if not issues and verify_wal_write:
        reason = verify_write(asyncpg_dsn(dsn), archive_dir)
        if reason:
            issues.append(reason)
    steps["wal_archive"] = {"ok": not issues, "detail": "; ".join(issues) or "ok"}
    if issues:
        return _finish(steps, started)

    drill_result = drill(
        backup_dir=backup_dest_dir,
        archive_dir=archive_dir,
        restore_data_dir=restore_data_dir,
        restore_port=restore_port,
        dsn_template=dsn,
        repo_root=repo_root,
    )
    steps["restore_drill"] = {"ok": bool(drill_result["ok"]), "detail": drill_result["steps"]}
    return _finish(steps, started)


def run_scheduled(
    *,
    interval_hours: float,
    run_once: Callable[[], dict],
    report_paths: list[Path],
    sleep: Callable[[float], None] = time.sleep,
    max_iterations: int | None = None,
) -> None:
    """`interval_hours`마다 `run_once`를 반복하고 매 주기 결과를 `report_paths`에 남긴다.
    한 주기가 예외를 던져도(주입 실패 포함) 프로세스는 죽지 않고 다음 주기에 재시도한다
    -- market_data/ledger 스케줄러(`run_forever`)와 동일한 fail-forward 설계. 테스트는
    `max_iterations`로 무한루프를 끊는다."""
    if interval_hours <= 0:
        raise ValueError(f"interval_hours는 양수여야 한다: {interval_hours!r}")

    i = 0
    while max_iterations is None or i < max_iterations:
        try:
            result = run_once()
            write_report(result, report_paths)
            if not result["ok"]:
                logger.error("backup_verify: 이번 주기 실패 -- %s", result["steps"])
        except Exception:
            logger.exception("backup_verify: 이번 주기 전체 실패 -- 다음 주기에 재시도")
        i += 1
        if max_iterations is None or i < max_iterations:
            sleep(interval_hours * 3600.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dest-dir",
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
        "--verify-wal-write",
        action="store_true",
        help="WAL 스위치를 강제해 아카이브 디렉터리가 실제로 느는지까지 확인",
    )
    parser.add_argument(
        "--schedule-interval-hours",
        type=float,
        default=None,
        help="주면 그 시간마다 무한 반복(기본: 1회 실행 후 종료)",
    )
    parser.add_argument(
        "--report-path",
        action="append",
        help="결과 JSON을 남길 경로(반복 가능). 생략 시 기본 2곳(로컬 + fleet)",
    )
    args = parser.parse_args(argv)

    dsn = args.dsn or server_url()
    report_paths = (
        [Path(p) for p in args.report_path]
        if args.report_path
        else [LOCAL_REPORT_PATH, PM_REPORT_PATH]
    )

    def run_once() -> dict:
        return run_backup_verify(
            backup_dest_dir=Path(args.dest_dir),
            archive_dir=Path(args.archive_dir),
            restore_data_dir=Path(args.restore_data_dir),
            restore_port=args.restore_port,
            dsn=dsn,
            verify_wal_write=args.verify_wal_write,
        )

    if args.schedule_interval_hours:
        run_scheduled(
            interval_hours=args.schedule_interval_hours,
            run_once=run_once,
            report_paths=report_paths,
        )
        return 0

    result = run_once()
    write_report(result, report_paths)
    out = sys.stdout if result["ok"] else sys.stderr
    print(json.dumps(result, ensure_ascii=False, indent=2), file=out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
