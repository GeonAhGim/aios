"""scripts/setup_test_db.py 단위/통합 테스트 — task-5782(esc-ci-prepare, 0f693214).

`_ensure_database`의 exists 조회 -> DROP -> CREATE 시퀀스가 원자적이지 않아,
같은 DB 이름(`aios_test_ci`)을 향한 동시 `--reset` 호출이 `DROP DATABASE`
경합으로 `asyncpg.exceptions.InvalidCatalogNameError`를 던지던 회귀를
재현·정정한다(root cause: `DROP DATABASE`에 `IF EXISTS`가 없고, 호출 전체가
advisory lock으로 직렬화되지 않았다).

`DATABASE_URL`은 `tests/conftest.py`가 모듈 임포트 시점에 `TEST_DATABASE_URL`
(없으면 즉시 ``RuntimeError``로 수집 자체를 막는다)로부터 채워 넣으므로, 이
스위트 안의 테스트가 실행되는 시점에는 항상 설정돼 있다 — 스킵 분기는
불필요하다(task-5820: `pytest.skip` 방어 분기가 도달 불가능한 채로 코드
래칫 `skip_xfail`만 증가시켰다).
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

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"

# 실DB 쓰기 성능 예산 — ADR-2026-09-09-C의 고정비 DB 쓰기 축(주문 제출 p95
# 50ms)과 달리 DB *생성*은 그 축에 해당 항목이 없으므로, 동시 8개 재생성이
# 순차 실행(각 수백ms)보다 크게 느려지지 않는다는 것만 지역 예산으로 못박는다.
CONCURRENT_RESET_BUDGET_S = 15.0
# task-5822: 4-way concurrent reset+migrate — each leg runs a real `alembic upgrade
# head` subprocess against a fresh DB (all revisions, not the already-migrated
# no-op case), so this budget is looser than CONCURRENT_RESET_BUDGET_S.
CONCURRENT_RESET_MIGRATE_BUDGET_S = 90.0


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup_test_db = _load_module("setup_test_db", SCRIPTS_DIR / "setup_test_db.py")


def _server_url() -> str:
    return os.environ["DATABASE_URL"]


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
    server_url = _server_url()
    try:
        created = asyncio.run(
            setup_test_db._ensure_database(server_url, scratch_db_name, reset=False)
        )
        assert created is True
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_ensure_database_reset_recreates_existing(scratch_db_name: str) -> None:
    server_url = _server_url()
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


@pytest.mark.perf
def test_ensure_database_concurrent_reset_survives_race(scratch_db_name: str) -> None:
    """failure-injection: task-5782 회귀 직접 재현 — 정정 전에는 여러 동시
    `reset=True` 호출 중 늦게 `DROP DATABASE`를 쏘는 쪽이
    `InvalidCatalogNameError`로 죽었다(esc-ci-prepare sha 0f693214).
    """
    server_url = _server_url()
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


@pytest.mark.perf
def test_ensure_database_concurrent_reset_with_migrate_survives_race(
    scratch_db_name: str,
) -> None:
    """failure-injection: task-5822(esc-ci-prepare 재발) 재현 — `_ensure_database`가
    `migrate_url`을 넘기기 전에는 advisory lock이 CREATE까지만 감싸고 락 해제
    후 별도로 `_migrate()`를 불렀다. 그 창에서 같은 DB를 향한 동시 `--reset`
    호출(local_ci와 ci_recheck이 겹쳐 실행하는 실제 시나리오)이 방금 만든 DB를
    다시 DROP하면 migrate 쪽 커넥션이 `InvalidCatalogNameError`로 죽었다.
    `migrate_url`을 넘겨 락을 쥔 채로 마이그레이션까지 끝내면 이 경합이 사라져야
    한다: 4-way 동시 reset+migrate가 예외 없이 전부 끝나고, 최종 DB에
    `alembic_version`이 채워져 있어야 한다(마이그레이션이 실제로 적용됐다는 증거).
    """
    server_url = _server_url()
    test_url = setup_test_db._with_database(server_url, scratch_db_name)

    async def _run_concurrent() -> list[bool]:
        results = await asyncio.gather(
            *[
                setup_test_db._ensure_database(
                    server_url, scratch_db_name, reset=True, migrate_url=test_url
                )
                for _ in range(4)
            ]
        )
        return cast("list[bool]", results)

    try:
        start = time.monotonic()
        results = asyncio.run(_run_concurrent())
        elapsed = time.monotonic() - start

        assert results == [True] * 4
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True

        async def _read_alembic_version() -> str | None:
            conn = await asyncpg.connect(setup_test_db._asyncpg_dsn(test_url))
            try:
                return await conn.fetchval("SELECT version_num FROM alembic_version")
            finally:
                await conn.close()

        assert asyncio.run(_read_alembic_version()) is not None

        assert elapsed < CONCURRENT_RESET_MIGRATE_BUDGET_S, (
            f"4-way concurrent reset+migrate took {elapsed:.2f}s, budget "
            f"{CONCURRENT_RESET_MIGRATE_BUDGET_S}s"
        )
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_drop_database_removes_existing(scratch_db_name: str) -> None:
    server_url = _server_url()
    asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=False))
    try:
        dropped = asyncio.run(setup_test_db._drop_database(server_url, scratch_db_name))
        assert dropped is True
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is False
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_drop_database_missing_is_noop(scratch_db_name: str) -> None:
    server_url = _server_url()
    dropped = asyncio.run(setup_test_db._drop_database(server_url, scratch_db_name))
    assert dropped is False


def test_list_test_databases_includes_created(scratch_db_name: str) -> None:
    server_url = _server_url()
    asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=False))
    try:
        rows = asyncio.run(setup_test_db._list_test_databases(server_url))
        names = {name for name, _size in rows}
        assert scratch_db_name in names
        assert all(name.startswith(setup_test_db.PREFIX) for name in names)
        assert all(size >= 0 for _name, size in rows)
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_main_drop_flag_removes_database(scratch_db_name: str) -> None:
    server_url = _server_url()
    session_name = scratch_db_name.removeprefix(setup_test_db.PREFIX)
    asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=False))
    try:
        sys.argv = ["setup_test_db.py", session_name, "--drop"]
        assert setup_test_db.main() == 0
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is False
    finally:
        asyncio.run(_drop_if_exists(server_url, scratch_db_name))


def test_main_list_flag_needs_no_name(capsys: pytest.CaptureFixture[str]) -> None:
    _server_url()
    sys.argv = ["setup_test_db.py", "--list"]
    assert setup_test_db.main() == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert all(line.split()[0].startswith(setup_test_db.PREFIX) for line in lines)


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
