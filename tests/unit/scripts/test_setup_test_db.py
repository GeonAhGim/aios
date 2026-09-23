"""scripts/setup_test_db.py 단위/통합 테스트 — task-5782(esc-ci-prepare, 0f693214).

`_ensure_database`의 exists 조회 -> DROP -> CREATE 시퀀스가 원자적이지 않아,
같은 DB 이름(`aios_test_ci`)을 향한 동시 `--reset` 호출이 `DROP DATABASE`
경합으로 `asyncpg.exceptions.InvalidCatalogNameError`를 던지던 회귀를
재현·정정한다(root cause: `DROP DATABASE`에 `IF EXISTS`가 없고, 호출 전체가
advisory lock으로 직렬화되지 않았다).

실DB 접근(`DATABASE_URL`)이 필요한 테스트는 접속 실패 시 스킵한다 — 이
저장소 워크트리는 항상 로컬 Postgres가 떠 있지만(TESTING.md), CI worktree
프로비저닝 이전 단계 등 DB가 없는 실행 경로에서 무음 실패 대신 스킵으로
드러낸다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"

# 실DB 쓰기 성능 예산 — ADR-2026-09-09-C의 고정비 DB 쓰기 축(주문 제출 p95
# 50ms)과 달리 DB *생성*은 그 축에 해당 항목이 없으므로, 동시 8개 재생성이
# 순차 실행(각 수백ms)보다 크게 느려지지 않는다는 것만 지역 예산으로 못박는다.
CONCURRENT_RESET_BUDGET_S = 15.0


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_test_db = _load_module("setup_test_db", SCRIPTS_DIR / "setup_test_db.py")


def _server_url_or_skip() -> str:
    url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL 없음 — 실DB 통합 테스트 스킵")
    return url


async def _database_exists(server_url: str, database: str) -> bool:
    admin = await asyncpg.connect(
        setup_test_db._asyncpg_dsn(setup_test_db._with_database(server_url, "postgres"))
    )
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", database
        )
        return bool(exists)
    finally:
        await admin.close()


async def _drop_if_exists(server_url: str, database: str) -> None:
    admin = await asyncpg.connect(
        setup_test_db._asyncpg_dsn(setup_test_db._with_database(server_url, "postgres"))
    )
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
    finally:
        await admin.close()


@pytest.fixture
def scratch_db_name() -> str:
    return f"aios_test_scratch_{uuid4().hex[:12]}"


def test_ensure_database_creates_when_missing(scratch_db_name: str) -> None:
    server_url = _server_url_or_skip()
    try:
        created = asyncio.run(
            setup_test_db._ensure_database(server_url, scratch_db_name, reset=False)
        )
        assert created is True
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_ensure_database_reset_recreates_existing(scratch_db_name: str) -> None:
    server_url = _server_url_or_skip()
    try:
        first = asyncio.run(
            setup_test_db._ensure_database(server_url, scratch_db_name, reset=False)
        )
        assert first is True
        second = asyncio.run(
            setup_test_db._ensure_database(server_url, scratch_db_name, reset=True)
        )
        assert second is True
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_ensure_database_concurrent_reset_survives_race(scratch_db_name: str) -> None:
    """failure-injection: task-5782 회귀 직접 재현 — 정정 전에는 여러 동시
    `reset=True` 호출 중 늦게 `DROP DATABASE`를 쏘는 쪽이
    `InvalidCatalogNameError`로 죽었다(esc-ci-prepare sha 0f693214).
    """
    server_url = _server_url_or_skip()
    asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=False))
    try:

        async def _run_concurrent() -> list[bool]:
            results = await asyncio.gather(
                *[
                    setup_test_db._ensure_database(server_url, scratch_db_name, reset=True)
                    for _ in range(8)
                ]
            )
            return cast("list[bool]", results)

        start = time.monotonic()
        results = asyncio.run(_run_concurrent())
        elapsed = time.monotonic() - start

        assert results == [True] * 8
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True
        assert elapsed < CONCURRENT_RESET_BUDGET_S, (
            f"8-way concurrent reset took {elapsed:.2f}s, budget "
            f"{CONCURRENT_RESET_BUDGET_S}s"
        )
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_main_rejects_lowercase_violation() -> None:
    with pytest.raises(SystemExit):
        sys.argv = ["setup_test_db.py", "Not-Valid-Name"]
        setup_test_db.main()


def test_main_rejects_name_too_long() -> None:
    with pytest.raises(SystemExit):
        sys.argv = ["setup_test_db.py", "a" * 41]
        setup_test_db.main()


def test_main_requires_name_or_template() -> None:
    with pytest.raises(SystemExit):
        sys.argv = ["setup_test_db.py"]
        setup_test_db.main()
