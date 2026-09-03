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


async def _ensure_database(server_url: str, database: str, *, reset: bool) -> bool:
    """maintenance DB(postgres)에 붙어 대상 DB를 만든다. 반환값: 새로 만들었는지."""
    admin = await asyncpg.connect(_asyncpg_dsn(_with_database(server_url, "postgres")))
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
        if exists and reset:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                database,
            )
            await admin.execute(f'DROP DATABASE "{database}"')
            exists = None
        if not exists:
            await admin.execute(f'CREATE DATABASE "{database}"')
            return True
        return False
    finally:
        await admin.close()


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
    args = parser.parse_args()

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
    test_url = _with_database(server_url, database)

    created = asyncio.run(_ensure_database(server_url, database, reset=args.reset))
    _migrate(test_url)

    if args.print_env:
        print(f"TEST_DATABASE_URL={test_url}")
        return 0
    print(f"{'생성' if created else '재사용'}: {database} — alembic head 적용 완료")
    print("pytest 실행 전:")
    print(f"  bash:       export TEST_DATABASE_URL={test_url}")
    print(f"  PowerShell: $env:TEST_DATABASE_URL = \"{test_url}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
