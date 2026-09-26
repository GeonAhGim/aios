"""세션별 격리 테스트 DB 생성 + 마이그레이션 — 전수감사 §1/§9 "공유 DB 격리 부재" 대응.

여러 Claude 세션·개발자가 하나의 `aios_dev`를 공유하면서 TRUNCATE·롤백 없이
uuid 접미사로만 격리해 왔고, 마이그레이션 적용 시점이 세션마다 달라
"N passed" 숫자가 실행할 때마다 달라졌다(2차 114 errors, 3차 7 failed —
모두 환경 간섭). CI는 세션당 새 Postgres를 쓰므로 이 문제가 없다. 이
스크립트는 로컬에서도 같은 조건을 만든다: 세션 이름별 DB 하나.

사용 (저장소 루트, .venv 활성 또는 .venv/Scripts/python.exe):

    python scripts/setup_test_db.py pm            # aios_test_pm 생성(없으면) + alembic upgrade head
    python scripts/setup_test_db.py pm --reset    # DROP 후 재생성
    python scripts/setup_test_db.py pm --print-env

마지막 줄에 출력되는 `TEST_DATABASE_URL=...`을 pytest 실행 전에 export한다.
서버 접속 정보는 DATABASE_URL(환경변수 → .env 순)에서 host/port/user/password만
빌려 쓰고 DB 이름만 바꾼다. 이름은 `aios_test_` 접두어가 강제된다 —
`aios_dev`·운영 DB를 실수로 지우는 일이 없도록.

    python scripts/setup_test_db.py --template   # aios_test_template 생성 + 마이그레이션

PLT-36: `--template`은 이름 고정 `aios_test_template` DB를 만들고 마이그레이션한다.
이 DB 자체는 테스트가 직접 쓰지 않는다 — `tests/support/db.py`의
`ensure_worker_database`가 pytest-xdist 워커별 DB를 여기서
`CREATE DATABASE ... TEMPLATE`로 복제해, 워커마다 마이그레이션을 재실행하지
않고도(각 ~1초) 격리된 DB를 준다.

task-5807: worker 하나가 끝날 때마다 `--reset`이 새로 만들고 아무도 지우지 않아
`aios_test_*`가 무한히 쌓였다(50개, 0.78GB, 클러스터 백업의 96%). 두 명령을
더한다 — 둘 다 pm/worker_runner.py·pm/scripts/cleanup_orphan_test_dbs.py가
서브프로세스로만 호출하고, 여기서 직접 사람이 칠 일은 드물다:

    python scripts/setup_test_db.py pm --drop         # aios_test_pm DROP(없으면 조용히 통과)
    python scripts/setup_test_db.py --list            # "aios_test_<name> <size_bytes>" 한 줄씩
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "aios_test_"
_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")


def _server_url() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL이 환경변수 또는 .env에 없습니다.")
    return url


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _ensure_database(
    server_url: str, database: str, *, reset: bool, migrate_url: str | None = None
) -> bool:
    """maintenance DB(postgres)에 붙어 대상 DB를 만든다. 반환값: 새로 만들었는지.

    task-5782(esc-ci-prepare, 0f693214): 이 함수는 exists 조회 -> DROP -> CREATE가
    원자적이지 않았다. 같은 이름(예: `aios_test_ci`)을 향한 두 호출이 동시에
    `reset=True`로 들어오면 둘 다 exists=True를 보고 둘 다 `DROP DATABASE`를
    쏘는데, 먼저 성공한 쪽이 지운 직후 늦은 쪽의 `DROP DATABASE`(IF EXISTS
    없음)가 `InvalidCatalogNameError`로 죽었다. 호출자 쪽 락(pm/local_ci.py의
    db_provision_lock)은 그 오케스트레이터를 거치는 경로만 보호하므로, 이
    함수 자체를 postgres advisory lock으로 직렬화해 어떤 호출 경로든(다른
    스크립트·수동 실행 포함) 안전하게 만든다. `DROP DATABASE IF EXISTS`도 함께
    써서 락 밖에서 이미 지워진 경우(예: 수동 정리)에도 죽지 않는다.

    task-5822(esc-ci-prepare 재발): 위 락은 CREATE까지만 감쌌다. `main()`은
    락 해제 후 별도로 `_migrate()`(alembic subprocess)를 불렀는데, 그 창에서
    같은 DB를 향한 동시 `--reset` 호출(예: local_ci와 ci_recheck이 겹쳐 실행)이
    락을 잡아 방금 만든 DB를 다시 DROP하면, migrate 쪽 커넥션이
    `InvalidCatalogNameError`로 죽었다. `migrate_url`을 넘기면 마이그레이션을
    advisory lock을 쥔 채로(락 해제 전에) 실행해 그 창을 없앤다.
    """
    admin = await asyncpg.connect(_asyncpg_dsn(_with_database(server_url, "postgres")))
    try:
        await admin.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", database)
        try:
            exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
            if exists and reset:
                await admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    database,
                )
                await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
                exists = None
            created = False
            if not exists:
                await admin.execute(f'CREATE DATABASE "{database}"')
                created = True
            if migrate_url is not None:
                _migrate(migrate_url)
            return created
        finally:
            await admin.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", database)
    finally:
        await admin.close()


async def _drop_database(server_url: str, database: str) -> bool:
    """대상 DB를 지운다(존재하지 않으면 아무 일도 하지 않는다). 반환값: 실제로 지웠는지.
    `_ensure_database`와 같은 advisory lock으로 직렬화 — 같은 이름을 향한 동시 --reset과
    경합해도 DROP 순서가 꼬이지 않는다."""
    admin = await asyncpg.connect(_asyncpg_dsn(_with_database(server_url, "postgres")))
    try:
        await admin.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", database)
        try:
            exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
            if not exists:
                return False
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
            return True
        finally:
            await admin.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", database)
    finally:
        await admin.close()


_LIST_SIZE_LOCK_TIMEOUT_MS = 1_500
_LIST_SIZE_CONCURRENCY = 16
_LIST_SIZE_BUDGET_S = 10.0


async def _size_of_database(pool: asyncpg.Pool, name: str) -> int:
    """`pg_database_size`는 대상 DB에 AccessShareLock을 건다 -- 같은 서버를 공유하는
    다른 워커가 그 순간 `DROP DATABASE`/`CREATE DATABASE ... TEMPLATE` 중이면(다른
    워크트리들이 상시 동시 실행 중) 그 DDL이 끝날 때까지 블록될 수 있다.
    `lock_timeout`으로 상한을 두고, 잠겼거나 권한이 없으면(호출자 접속 불가 DB는
    NULL) 0으로 취급해 집계에서 조용히 빠지게 한다."""
    async with pool.acquire() as conn:
        await conn.execute(f"SET lock_timeout = '{_LIST_SIZE_LOCK_TIMEOUT_MS}ms'")
        try:
            size = await conn.fetchval("SELECT pg_database_size($1)", name)
        except asyncpg.exceptions.LockNotAvailableError:
            return 0
        return int(size or 0)


async def _list_test_databases(server_url: str) -> list[tuple[str, int]]:
    """`aios_test_` 접두어 DB만 (이름, 바이트 크기) 목록으로 — healthcheck.py의
    test_db_bloat 소견과 scripts/cleanup_orphan_test_dbs.py(pm 저장소)가 이 출력을 파싱한다.

    이름 나열(카탈로그 스캔)은 잠금·디스크 I/O가 필요 없어 즉시 끝나지만, 크기는
    DB마다 `pg_database_size`가 그 DB의 파일들을 stat해야 해서 대상이 100+개로
    불어난 상시 병존 워크트리 환경에서는(task-6096/esc-ci-pytest_perf 실측: 140+개,
    직렬 조회 시 개당 0.4~1s로 전체 --list가 수십~150s) 한 번에 다 세는 것 자체가
    비용이 된다. 개별 락 상한만으로는(직전 시도) 막지 못한다 -- 잠기지 않은
    DB조차 개당 I/O 지연이 누적되기 때문이다. 이름 목록은 항상 전부 반환하되,
    크기 조회는 커넥션 풀로 동시에 돌리고 전체에 유한한 시간 예산을 둔다 -- 예산
    안에 못 끝난 DB는 크기 0으로 보고한다(cleanup_orphan_test_dbs.py는 이름으로
    정리 대상을 판별하고 크기는 우선순위/로그용이라 최선 노력으로 충분하다).
    테스트 예산(LIST_SUBPROCESS_BUDGET_S)을 올리는 대신, DB 증가에 무관하게 --list
    자체가 유한 시간에 끝나도록 만드는 근본 수정이다(DECISION_GUIDELINES B-2)."""
    dsn = _asyncpg_dsn(_with_database(server_url, "postgres"))
    admin = await asyncpg.connect(dsn)
    try:
        names = [
            r["datname"]
            for r in await admin.fetch(
                "SELECT datname FROM pg_database WHERE datname LIKE $1 ORDER BY datname",
                PREFIX + "%",
            )
        ]
    finally:
        await admin.close()

    sizes: dict[str, int] = dict.fromkeys(names, 0)
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=_LIST_SIZE_CONCURRENCY)
    try:

        async def _fill(name: str) -> None:
            sizes[name] = await _size_of_database(pool, name)

        tasks = [asyncio.create_task(_fill(name)) for name in names]
        _done, pending = await asyncio.wait(tasks, timeout=_LIST_SIZE_BUDGET_S)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await pool.close()
    return [(name, sizes[name]) for name in names]


def _migrate(test_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": test_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("name", nargs="?", help="세션 이름 — DB는 aios_test_<name>")
    parser.add_argument("--reset", action="store_true", help="이미 있으면 DROP 후 재생성")
    parser.add_argument("--print-env", action="store_true", help="URL 한 줄만 출력(스크립트용)")
    parser.add_argument(
        "--template",
        action="store_true",
        help="이름 고정 aios_test_template DB를 생성·마이그레이션(PLT-36 워커별 복제 원본)",
    )
    parser.add_argument(
        "--drop",
        action="store_true",
        help="생성 없이 aios_test_<name>만 DROP(task-5807, worker_runner 종료 시 정리용)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="aios_test_* 전부를 'name size_bytes' 한 줄씩 출력하고 종료(name/--template 불필요)",
    )
    args = parser.parse_args()

    if args.list:
        server_url = _server_url()
        for database, size in asyncio.run(_list_test_databases(server_url)):
            print(f"{database} {size}")
        return 0

    if args.template:
        name = "template"
    elif args.name:
        name = args.name
    else:
        parser.error("name 또는 --template 중 하나가 필요합니다.")

    if not _NAME_RE.match(name):
        raise SystemExit("이름은 소문자·숫자·밑줄 40자 이내여야 합니다.")
    database = PREFIX + name
    server_url = _server_url()

    if args.drop:
        dropped = asyncio.run(_drop_database(server_url, database))
        print(f"{'DROP 완료' if dropped else '존재하지 않음(스킵)'}: {database}")
        return 0

    test_url = _with_database(server_url, database)

    created = asyncio.run(
        _ensure_database(server_url, database, reset=args.reset, migrate_url=test_url)
    )

    if args.print_env:
        print(f"TEST_DATABASE_URL={test_url}")
        return 0
    print(f"{'생성' if created else '재사용'}: {database} — alembic head 적용 완료")
    print("pytest 실행 전:")
    print(f"  bash:       export TEST_DATABASE_URL={test_url}")
    print(f'  PowerShell: $env:TEST_DATABASE_URL = "{test_url}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
