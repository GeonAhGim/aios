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
import os
import random
import re
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

__all__ = [
    "session_database_url",
    "ensure_worker_database",
    "create_pool_with_retry",
    "tx_conn",
    "_db_name",
    "_with_database",
    "_asyncpg_dsn",
    "asyncpg",
]

_NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_CLONE_ATTEMPTS = 5
_CLONE_RETRY_BASE_DELAY = 0.5

# esc-ci-pytest.json (task-6235): local Windows CI intermittently resets the TCP
# socket to Postgres mid-connect (WinError 64 / asyncpg ConnectionDoesNotExistError,
# "connection was closed in the middle of operation") while a fixture opens a plain
# asyncpg.create_pool against the shared worker DB -- a transient OS-level reset, not
# a code regression (task-6212 confirmed the deterministic template_db-termination bug
# task-6176/9d8b281c already fixed was not the cause here; bisect kept landing on
# unrelated commits because the flake can surface on whichever run happens to race
# it). scripts/replay_verify.py hit the identical error shape twice
# (c6acdac8/task-6177, eb114fb0/task-6213) and fixed it with bounded retry-with-backoff
# on the initial connect; this mirrors that pattern for test fixtures instead of
# widening a budget or adding an ignore (DECISION_GUIDELINES B-2).
_POOL_CONNECT_ATTEMPTS = 5
_POOL_CONNECT_RETRY_BASE_DELAY = 0.5


def _db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{database}", parts.query, parts.fragment))


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


TEMPLATE_DATABASE_URL_ENV = "AIOS_TEST_TEMPLATE_DATABASE_URL"


def template_database_url() -> str:
    """일회용 DB 클론(마이그레이션 왕복 등)의 템플릿으로 쓸 URL.

    xdist 워커 안에서 `os.environ["DATABASE_URL"]`은 이미 이 워커 전용 DB
    (`..._gwN`)이고, 거기에는 같은 워커의 픽스처 풀이 살아 있다. PostgreSQL의
    `CREATE DATABASE ... TEMPLATE`는 템플릿 DB에 다른 세션이 하나라도 붙어 있으면
    `ObjectInUseError`("is being accessed by other users")로 거부하므로, 워커 DB를
    템플릿으로 삼는 클론은 인접 테스트의 커넥션 수에 따라 흔들린다(CI run
    36191114294: "There are 10 other sessions using the database"). tests/conftest.py가
    워커 DB로 갈아끼우기 전의 원본 `TEST_DATABASE_URL`(어느 워커도 붙지 않는
    순수 템플릿)을 `AIOS_TEST_TEMPLATE_DATABASE_URL`에 남겨 두고 여기서 돌려준다.
    xdist 없이(master) 실행하면 그 변수가 없으므로 기존처럼 `DATABASE_URL`을 쓴다.
    """
    return os.environ.get(TEMPLATE_DATABASE_URL_ENV) or os.environ["DATABASE_URL"]


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
        last_exc: (
            asyncpg.exceptions.ObjectInUseError | asyncpg.exceptions.UniqueViolationError | None
        ) = None
        for attempt in range(_CLONE_ATTEMPTS):
            # 워커 DB(target_db)는 이 프로세스가 배타적으로 소유하므로, 크래시로
            # 죽은 이전 프로세스가 남긴 idle 커넥션을 강제 종료해도 안전하다
            # (pg_terminate_backend는 비동기 SIGTERM이라 즉시 반영되지 않을 수
            # 있어 지수 백오프로 재시도). template_db는 절대 여기서 건드리지
            # 않는다 — template_db는 이 세션 전체(다른 테스트의 살아있는
            # 커넥션 포함)가 공유하는 `TEST_DATABASE_URL` 그 자체일 수 있고,
            # 거기 강제 종료를 걸면 마침 쿼리 중이던 다른 테스트가
            # `asyncpg.exceptions.ConnectionDoesNotExistError`로 깨진다
            # (esc-ci-pytest.json, task-6176 — task-6005가 실 DB로
            # `ensure_worker_database`를 직접 호출하며 처음 노출됐다). template_db에
            # 살아있는 커넥션이 남아 있으면 CREATE DATABASE ... TEMPLATE가
            # ObjectInUseError로 거부되고, 아래에서 그대로 전파한다(모듈
            # docstring의 "조용히 폴백하지 않는다" 계약과 일치).
            #
            # task-7375(esc-ci-pytest_perf): 같은 worker_id(예: "gw0")를 쓰는 두
            # 프로세스(로컬 CI의 `pytest_perf`/`pytest` 단계가 겹쳐 돌 때 등)가 이
            # 루프에 동시에 들어오면, 한쪽의 DROP 이후 다른 쪽의 DROP은 이미 없는
            # 이름이라 조용히 지나가고, 두 CREATE DATABASE가 거의 동시에 실행돼
            # 먼저 커밋된 쪽만 성공하고 나머지는 `ObjectInUseError`가 아니라
            # `pg_database_datname_index`(이름 UNIQUE 인덱스) 위반인
            # `UniqueViolationError`로 거부된다(관측: `conftest.py` 임포트 단계에서
            # 그대로 전파돼 `pytest_perf` 전체가 ImportError로 적색). ObjectInUseError와
            # 동일하게 재시도 대상에 포함한다 — DROP+CREATE 루프가 다음 회차에
            # 승자의 DB를 그대로 재사용하거나 다시 만들어 준다.
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                target_db,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{target_db}"')
            try:
                await admin.execute(f'CREATE DATABASE "{target_db}" TEMPLATE "{template_db}"')
                break
            except (
                asyncpg.exceptions.ObjectInUseError,
                asyncpg.exceptions.UniqueViolationError,
            ) as exc:
                last_exc = exc
                await asyncio.sleep(_CLONE_RETRY_BASE_DELAY * (attempt + 1))
        else:
            assert last_exc is not None
            raise last_exc
    finally:
        await admin.close()
    return target_url


def _pool_retry_delay(attempt: int) -> float:
    return _POOL_CONNECT_RETRY_BASE_DELAY * (attempt + 1)


# task-6687/esc-ci-coverage: the full-mode coverage step's `pytest --cov` run
# hit the same ConnectionDoesNotExistError/ConnectionResetError shape 5a7b61c6
# (task-6627) already root-caused for scripts/replay_verify.py --
# tests/adversarial/risk/conftest.py's `pool` fixture calls this exact
# function, and every worktree on the shared local Postgres computes the same
# deterministic `_pool_retry_delay(attempt)` schedule, so concurrent
# worktrees' retries converge on the same wall-clock instants and repeatedly
# recreate the contention spike they are backing off from (thundering herd).
# Per DECISION_GUIDELINES B-2 the retry budget/attempt cap is left untouched;
# only the sleep is randomized (full jitter: uniform over
# `[0, _pool_retry_delay(attempt)]`) to decorrelate concurrent processes,
# mirroring replay_verify.py's `_sleep_before_retry`.
async def _sleep_before_pool_retry(attempt: int) -> None:
    await asyncio.sleep(random.uniform(0, _pool_retry_delay(attempt)))  # noqa: S311 -- retry jitter, not crypto


async def create_pool_with_retry(dsn: str, **kwargs: Any) -> asyncpg.Pool:
    """`asyncpg.create_pool` with retry on the initial connection only.

    Fail-closed still applies: after `_POOL_CONNECT_ATTEMPTS` the original
    exception propagates unchanged, it is never swallowed into a false green.

    `asyncpg.create_pool(dsn, **kwargs)` returns a `Pool` object synchronously
    (unconnected); connecting happens only once it is awaited
    (`Pool.__await__` -> `_async__init__` -> `_initialize`). `_initialize`
    connects the first holder directly, then -- when `min_size > 1` -- gathers
    the rest concurrently. If a later holder's connect fails, `_initialize`
    still marks `self._initialized = True` in its `finally` (see
    asyncpg/pool.py `_async__init__`), so the already-open first holder is a
    live Postgres connection with no one holding a reference to the `Pool` to
    close it -- a leak on every failed attempt, previously discarded here
    because the failed `await` expression's `Pool` was never bound to a name.
    Retrying without terminating it compounds server-side connection pressure
    across attempts, which is the opposite of what the retry is for. Binding
    the `Pool` and calling the synchronous `terminate()` on failure closes
    whatever holders did connect before raising/retrying.
    """
    for attempt in range(_POOL_CONNECT_ATTEMPTS):
        pool = asyncpg.create_pool(dsn, **kwargs)
        try:
            await pool
            return pool
        except (OSError, asyncpg.exceptions.ConnectionDoesNotExistError):
            pool.terminate()
            if attempt + 1 >= _POOL_CONNECT_ATTEMPTS:
                raise
            await _sleep_before_pool_retry(attempt)
    raise AssertionError("unreachable -- loop always returns or raises")


@pytest.fixture
async def tx_conn(pool: Any) -> AsyncGenerator[Any, None]:
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
