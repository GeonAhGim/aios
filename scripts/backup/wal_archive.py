"""H-4(task-2609, ADR-2026-09-09-B) -- WAL 아카이브 설정 검증.

베이스 백업(base_backup.py)만으로는 PITR이 안 된다 -- 그 이후 WAL이 끊김 없이
아카이브되고 있어야 임의 시점 복구가 가능하다. 이 스크립트는 서버 GUC(wal_level/
archive_mode/archive_command)를 읽어 PITR 전제조건이 실제로 켜져 있는지 검증하고,
`--verify-write`가 있으면 WAL 스위치를 강제해 최근 아카이브 파일이 실제로 늘어나는지까지
확인한다(설정만 켜져 있고 archive_command 자체가 죽어 있는 경우 -- 가장 흔한 조용한
실패 -- 를 잡는다).

실행: `python -m scripts.backup.wal_archive` (저장소 루트, DATABASE_URL은 .env에서).
종료코드 0=정상, 1=위반 발견.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
_OK_WAL_LEVELS = ("replica", "logical")
_OK_ARCHIVE_MODES = ("on", "always")


def server_url() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL이 환경변수 또는 .env에 없습니다.")
    return url


def asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _fetch_settings(dsn: str) -> dict[str, str]:
    conn = await asyncpg.connect(dsn)
    try:
        names = ("wal_level", "archive_mode", "archive_command")
        rows = await conn.fetch(
            "SELECT name, setting FROM pg_settings WHERE name = ANY($1::text[])", list(names))
        return {r["name"]: r["setting"] for r in rows}
    finally:
        await conn.close()


def read_wal_settings(dsn: str) -> dict[str, str]:
    return asyncio.run(_fetch_settings(dsn))


def evaluate(settings: dict[str, str]) -> list[str]:
    """설정값만으로 판단 가능한 PITR 전제조건 위반 목록(비어 있으면 정상) -- DB/네트워크 없이
    단위 테스트로 실패 주입이 가능하도록 순수 함수로 분리했다."""
    issues = []
    wal_level = settings.get("wal_level")
    if wal_level not in _OK_WAL_LEVELS:
        issues.append(f"wal_level={wal_level!r} -- PITR에는 {_OK_WAL_LEVELS} 중 하나가 필요하다")
    archive_mode = settings.get("archive_mode")
    if archive_mode not in _OK_ARCHIVE_MODES:
        issues.append(f"archive_mode={archive_mode!r} -- {_OK_ARCHIVE_MODES} 중 하나가 필요하다")
    archive_command = (settings.get("archive_command") or "").strip()
    if not archive_command or archive_command == "(disabled)":
        issues.append("archive_command가 비어 있다 -- WAL이 아카이브되지 않는다")
    return issues


def check_archive_advanced(before: set[str], after: set[str]) -> str | None:
    """WAL 스위치 전/후 아카이브 디렉터리 파일 목록을 비교한다. 새 파일이 없으면
    사유 문자열(실패), 있으면 None(정상)을 돌려준다 -- 순수 함수라 실제 타이밍 없이
    검증 가능하다."""
    if after - before:
        return None
    return "pg_switch_wal() 이후에도 아카이브 디렉터리에 새 파일이 나타나지 않았다"


async def _switch_wal(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("SELECT pg_switch_wal()")
    finally:
        await conn.close()


def _default_switch_wal(dsn: str) -> None:
    asyncio.run(_switch_wal(dsn))


def _default_list_dir(d: Path) -> set[str]:
    return {p.name for p in d.glob("*")} if d.exists() else set()


def verify_archiving_advances(
    dsn: str,
    archive_dir: Path,
    *,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
    switch_wal: Callable[[str], None] = _default_switch_wal,
    list_dir: Callable[[Path], set[str]] = _default_list_dir,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    before = list_dir(archive_dir)
    switch_wal(dsn)
    deadline = clock() + timeout
    while True:
        reason = check_archive_advanced(before, list_dir(archive_dir))
        if reason is None:
            return None
        if clock() >= deadline:
            return f"{reason}({timeout:.0f}초 대기)"
        sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dsn", help="생략하면 DATABASE_URL(.env)을 쓴다")
    parser.add_argument("--verify-write", metavar="ARCHIVE_DIR",
                         help="이 디렉터리를 감시해 WAL 스위치 후 실제로 아카이브되는지 확인한다")
    args = parser.parse_args(argv)

    dsn = asyncpg_dsn(args.dsn or server_url())
    settings = read_wal_settings(dsn)
    issues = evaluate(settings)

    if not issues and args.verify_write:
        reason = verify_archiving_advances(dsn, Path(args.verify_write))
        if reason:
            issues.append(reason)

    if issues:
        for issue in issues:
            print(f"wal_archive 위반: {issue}", file=sys.stderr)
        return 1
    print(f"wal_archive 정상: {settings}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
