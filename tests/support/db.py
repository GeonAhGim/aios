"""워커별 격리 테스트 DB.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.4/§9 PLT-36.

`TEST_DATABASE_URL`이 가리키는 DB(세션 하나에 이미 `alembic upgrade head`가 적용된
상태 — 예: `aios_test_backend_4`)를 템플릿으로, pytest-xdist 워커별 DB를
`CREATE DATABASE ... TEMPLATE`로 복제한다(마이그레이션 재실행 없이 ~1초). `-n` 없이
실행하면(`worker_id == "master"`) 복제하지 않고 템플릿 URL을 그대로 쓴다 — 기존
단일 프로세스 실행 경로는 비용·동작 변화가 없다.

동시 xdist 워커들이 같은 DB에 원장 append 등을 동시에 써서 시퀀스·시드 계정이
오염되는 문제(esc-ci-b120c35c318c, ci/latest.json 3646eda)가 이 모듈의 대상이다.

미확인 가정: 템플릿 DB에 활성 커넥션이 남아 있으면 Postgres가
`CREATE DATABASE ... TEMPLATE`를 거부한다(`source database ... being accessed by
other users`). 이 모듈은 그 경우 예외를 그대로 전파한다 — 조용히 템플릿 DB로
폴백해 격리를 깨뜨리지 않는다. 워커 DB는 매 pytest 세션마다 DROP 후 다시
복제된다(`ensure_worker_database` 참고) — 이전 실행이 죽으며 남긴 오염이 다음
실행으로 넘어가지 않는다.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_CLONE_ATTEMPTS = 5
_CLONE_RETRY_BASE_DELAY = 0.5


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def session_database_url(template_url: str, worker_id: str) -> str:
    """워커별 DB 접속 URL을 계산한다(I/O 없는 순수 함수).

    `worker_id == "master"`(xdist 미사용 — 일반 `pytest` 실행)면 template_url을
    그대로 반환한다.
    """
    if worker_id == "master":
        return template_url
    template_db = _db_name(template_url)
    if not _NAME_RE.match(template_db):
        raise ValueError(f"예상치 못한 템플릿 DB 이름: {template_db!r}")
    worker_db = f"{template_db}_{worker_id}"
    if not _NAME_RE.match(worker_db):
        raise ValueError(f"워커 DB 이름이 규칙(소문자·숫자·밑줄 40자)을 벗어남: {worker_db!r}")
    return _with_database(template_url, worker_db)


async def ensure_worker_database(template_url: str, worker_id: str) -> str:
    """워커 DB를 템플릿에서 새로 복제하고, 최종 접속 URL을 돌려준다.

    이름은 결정적이지만(같은 worker_id는 항상 같은 DB 이름), 매 pytest 세션마다
    무조건 DROP 후 재생성한다 — "존재하면 재사용"은 이전 실행이 크래시로
    죽으며 남긴 오염(예: tamper 테스트가 손상시킨 해시체인 행, write_frozen 잔류)이
    다음 실행까지 이어지는 사고를 낸다(실측: 이전 시도가 남긴 `..._gwN` DB에서
    `test_verify_integrity.py`가 재현 불가능한 값으로 실패). 템플릿 복제 자체가
    ~1초라 매번 재생성해도 비용이 무시할 만하다.
    """
    target_url = session_database_url(template_url, worker_id)
    if target_url == template_url:
        return target_url

    target_db = _db_name(target_url)
    template_db = _db_name(template_url)
    admin = await asyncpg.connect(_asyncpg_dsn(_with_database(template_url, "postgres")))
    try:
        last_exc: asyncpg.exceptions.ObjectInUseError | None = None
        for attempt in range(_CLONE_ATTEMPTS):
            # 템플릿·워커 DB 양쪽 다 살아있는 커넥션이 0개여야
            # `CREATE DATABASE ... TEMPLATE`가 통과한다. 크래시로 죽은 이전
            # 프로세스가 남긴 idle 커넥션이 있을 수 있으므로 매 시도 앞에서
            # 종료를 재요청한다(pg_terminate_backend는 비동기 SIGTERM이라
            # 즉시 반영되지 않을 수 있어 지수 백오프로 재시도).
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = ANY($1) AND pid <> pg_backend_pid()",
                [template_db, target_db],
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{target_db}"')
            try:
                await admin.execute(f'CREATE DATABASE "{target_db}" TEMPLATE "{template_db}"')
                break
            except asyncpg.exceptions.ObjectInUseError as exc:
                last_exc = exc
                await asyncio.sleep(_CLONE_RETRY_BASE_DELAY * (attempt + 1))
        else:
            assert last_exc is not None
            raise last_exc
    finally:
        await admin.close()
    return target_url


@pytest.fixture
async def tx_conn(pool):
    """단일 커넥션 트랜잭션 픽스처 — 테스트 종료 시 항상 ROLLBACK.

    커넥션 풀 전체가 아니라 한 커넥션 안에서만 격리하면 되는 가벼운 테스트용
    (여러 커넥션에 걸친 동시성을 검증하는 테스트는 `pool`을 직접 써야 한다 —
    트랜잭션 밖에서 커밋된 행만 다른 커넥션에서 보인다).
    """
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            yield conn
        finally:
            await tx.rollback()
