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
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from tests._perf.relative_budget import RelativeBudget

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"

# task-5822: 4-way concurrent reset+migrate — each leg runs a real `alembic upgrade
# head` subprocess against a fresh DB (all revisions, not the already-migrated
# no-op case).
#
# task-10646: this used to be a fixed `CONCURRENT_RESET_MIGRATE_BUDGET_S = 90.0`
# wall-clock ceiling, left behind when the sibling 8-way reset-only test (above)
# was switched to a self-calibrating `RelativeBudget` ratio in task-10652 — this
# migrate variant kept failing under host load (local repro: 201.28s > 90.0s,
# single reset+migrate baseline ~19.8-19.9s -> observed ratio ~10.1x). Switched
# to the same `RelativeBudget(calibration_fn=<single reset+migrate call>)`
# pattern the sibling test already uses, for the same reason: this is I/O-bound
# (mostly awaiting the DB/alembic subprocess, not CPU), so the ratio must be
# measured against the same real-world bottleneck, not a fixed-CPU loop or an
# absolute second count.


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
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
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

    task-10652: correctness(`results == [True] * 8`, DB가 실제로 존재)는
    advisory lock 직렬화 덕에 호스트 부하와 무관하게 항상 성립한다 — 로컬
    재현에서 8-way가 32~65s까지 걸려도 전부 True였다. 실패한 쪽은 절대
    벽시계 예산(`CONCURRENT_RESET_BUDGET_S=15.0`)뿐이다.

    `RelativeBudget`의 기본 CPU 보정 루프로는 이 작업을 정규화할 수 없다는
    것부터 로컬 재현으로 확인했다: 같은 세션에서 반복 실행 시 8-way 소요가
    34s -> 49s -> 65s로 커지는 동안 CPU 보정 루프는 102~106ms로 평평했다
    (host CPU는 안 바빠졌다는 뜻) — 반면 단일 `_ensure_database(reset=True)`
    호출 1회는 같은 구간에서 2.3s -> 4~7s로 거의 같은 비율로 느려졌다. 즉
    이 테스트의 병목은 CPU가 아니라 Postgres 서버 측 경합(동시 커넥션/락,
    `pg_terminate_backend` 대기 등 — 이 호스트에 다른 워커들이 각자의
    TEST_DATABASE_URL로 같은 서버에 동시 접속)이다. `calibration_fn`에 같은
    프로세스·같은 순간에 돈 "DB 작업 1회"(단일 reset 호출)를 넘겨, 호스트
    CPU가 아니라 실제 병목(Postgres 서버 부하)과 같은 축으로 비율을 잰다."""
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

        results_holder: list[list[bool]] = []

        def _run_and_capture() -> None:
            results_holder.append(asyncio.run(_run_concurrent()))

        def _single_reset_call() -> None:
            asyncio.run(setup_test_db._ensure_database(server_url, scratch_db_name, reset=True))

        budget = RelativeBudget()
        # 로컬 실측 ratio ~13-15x(8-way가 단일 reset 호출의 13-15배) — 순수
        # 직렬화 하한(8x)에 연결/락 오버헤드 여유를 더해 2배 이상 여유를 둔
        # 40x를 바닥선으로 건다.
        sample = budget.assert_within(
            _run_and_capture,
            max_ratio=40.0,
            mode="wall",
            n=1,
            warmup=0,
            calibration_n=1,
            calibration_fn=_single_reset_call,
            label="8-way concurrent reset vs 1x sequential reset",
        )
        print(f"[setup_test_db] {budget.describe(sample, max_ratio=40.0)}")

        results = results_holder[0]
        assert results == [True] * 8
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True
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

    task-10646: correctness 단언(결과 전부 True, `alembic_version` 채워짐)은
    advisory lock 직렬화 덕에 호스트 부하와 무관하게 항상 성립한다. 타이밍
    단언만 호스트에 따라 흔들려서, 고정 벽시계 상한 대신 같은 실행에서 직접
    잰 "단일 reset+migrate 호출 1회" 비용과의 비율로 건다(`RelativeBudget`,
    sibling 8-way 테스트와 동일 패턴, task-10652). 이론적 하한은 4x(완전
    직렬화)지만, 매 호출이 별도 `alembic upgrade head` 서브프로세스(파이썬
    인터프리터 기동 포함)라 로컬 재현 실측 비율은 ~10.1x였다 — 순수 직렬화
    하한 위에 서브프로세스 기동 오버헤드 + 스케줄링 여유를 더해 30x를
    바닥선으로 건다."""
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

    results_holder: list[list[bool]] = []

    def _run_and_capture() -> None:
        results_holder.append(asyncio.run(_run_concurrent()))

    def _single_reset_migrate_call() -> None:
        asyncio.run(
            setup_test_db._ensure_database(
                server_url, scratch_db_name, reset=True, migrate_url=test_url
            )
        )

    try:
        budget = RelativeBudget()
        sample = budget.assert_within(
            _run_and_capture,
            max_ratio=30.0,
            mode="wall",
            n=1,
            warmup=0,
            calibration_n=1,
            calibration_fn=_single_reset_migrate_call,
            label="4-way concurrent reset+migrate vs 1x sequential reset+migrate",
        )
        print(f"[setup_test_db] {budget.describe(sample, max_ratio=30.0)}")

        results = results_holder[0]
        assert results == [True] * 4
        assert asyncio.run(_database_exists(server_url, scratch_db_name)) is True

        async def _read_alembic_version() -> str | None:
            conn = await asyncpg.connect(setup_test_db._asyncpg_dsn(test_url))
            try:
                return await conn.fetchval("SELECT version_num FROM alembic_version")
            finally:
                await conn.close()

        assert asyncio.run(_read_alembic_version()) is not None
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
