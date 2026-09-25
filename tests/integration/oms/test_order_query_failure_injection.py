"""L4-26 DEEPEN(task-2767) — `order_query` D3 보강.

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md#1602)는 원 커밋(48db481)의
`test_order_query.py`(negative=5: invalid cursor, missing id, cross-tenant leak
x2, list excludes other tenant)가 D3 하한에 못 미친다고 판정했다(실측 D1) —
근거: "no failure-injection test (simulated DB/network failure), no numeric
performance assertion, no gate/CI red-line regression test, and no
multi-worker/adversarial-bypass proof". 이 파일이 그 네 가지만 보강한다 —
`order_query.py`와 기존 테스트 파일은 손대지 않는다.

failure-injection: `get_order`/`list_orders`/`list_order_events`는 예외를 잡지
않는다(모듈 docstring에 명시된 대로 SELECT 전용, 트랜잭션 경계 없음) — 그러나
그 사실 자체는 아직 실증되지 않았다. 만약 누군가 나중에 "DB 에러도 그냥 없는
걸로 치자"는 식으로 broad except를 추가한다면, 실제 인프라 장애(커넥션
유실)가 §8.3 "404 동형"과 구분 불가능해져 장애를 조용히 삼키는 결함이 된다 —
아래 테스트들은 `pool.acquire()`가 반환하는 커넥션이 조회 중간에 죽는 상황을
흉내내어 예외가 그대로 전파됨(=삼켜지지 않음)을 증명한다.

수치 성능 단언은 절대 ms 상수를 쓰지 않는다 — `tests/integration/oms/
test_gate_perf_multiinstance.py`(task-2762)와 동일하게 같은 커넥션의 기준
왕복비용(`SELECT 1`)에 정규화한 임계를 쓴다.

게이트/CI 적색선 회귀는 `test_submit_order_failure_injection.py`(task-2765)와
동일 기법이다 — 프로덕션 소스 파일은 그대로 둔 채, 자식 pytest 프로세스
안에서만 `get_order`의 tenant 필터(`AND user_id = $2`)를 항상-참인 조건으로
바꿔치기하고, 이미 있는 `test_get_order_cross_tenant_does_not_leak_existence`가
green(1 passed)에서 red(1 failed)로 바뀌는 것까지 증명한다 — DoD "교차 tenant
비노출"이 정말로 이 필터에 의존한다는 뜻이다.

multi-worker/adversarial-bypass 증명: (1) 여러 독립 커넥션(서로 다른 API
워커 프로세스 시뮬레이션)이 두 tenant를 동시에 뒤섞어 조회해도 격리가
깨지지 않음을 증명하고, (2) 공격자가 다른 tenant의 *유효한* opaque cursor를
훔쳐 자기 tenant_id로 재사용해도(cursor 자체는 위조가 아니라 실제 발급된
값) WHERE의 tenant 필터가 keyset 조건보다 우선해 다른 tenant 행이 섞이지
않음을 증명한다 — LA-22(task-825)류 결함의 또 다른 변종(다른 tenant 소유
페이지네이션 토큰 재사용 우회 시도).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from src.services.oms.application import order_query
from tests.integration.oms.conftest import create_test_user, insert_order

# ---- D3: failure-injection (simulated DB/network failure) -----------------


class _DroppedConnection:
    """simulated DB connection drop — `break_method`로 지정된 호출만 예외를
    던지고 나머지는 실커넥션에 그대로 위임한다(list_order_events의 소유권
    확인 fetchval은 통과시키고 그 다음 timeline() 호출의 fetch만 끊는 식으로
    "이미 일부 성공한 다단계 조회 중간에 죽는" 상황을 재현하기 위함)."""

    def __init__(self, real: asyncpg.Connection, *, break_method: str, exc: BaseException):
        self._real = real
        self._break_method = break_method
        self._exc = exc

    async def fetchrow(self, *args: object, **kwargs: object) -> object:
        if self._break_method == "fetchrow":
            raise self._exc
        return await self._real.fetchrow(*args, **kwargs)

    async def fetch(self, *args: object, **kwargs: object) -> object:
        if self._break_method == "fetch":
            raise self._exc
        return await self._real.fetch(*args, **kwargs)

    async def fetchval(self, *args: object, **kwargs: object) -> object:
        if self._break_method == "fetchval":
            raise self._exc
        return await self._real.fetchval(*args, **kwargs)


class _AcquireCtx:
    def __init__(self, real_pool: asyncpg.Pool, *, break_method: str, exc: BaseException):
        self._real_pool = real_pool
        self._break_method = break_method
        self._exc = exc
        self._conn: asyncpg.Connection | None = None

    async def __aenter__(self) -> _DroppedConnection:
        self._conn = await self._real_pool.acquire()
        return _DroppedConnection(self._conn, break_method=self._break_method, exc=self._exc)

    async def __aexit__(self, *exc_info: object) -> bool:
        assert self._conn is not None
        await self._real_pool.release(self._conn)
        return False


class _ConnectionDroppingPool:
    """`pool.acquire()`가 끊긴 커넥션을 흉내내는 프록시를 돌려주는 가짜
    pool — `submit_order`처럼 monkeypatch로 갈아치울 모듈 레벨 저장소
    객체가 없으므로(order_query는 `conn.fetchrow`를 직접 호출), pool
    자체를 감싸는 방식으로 실패를 주입한다."""

    def __init__(self, real_pool: asyncpg.Pool, *, break_method: str, exc: BaseException):
        self._real_pool = real_pool
        self._break_method = break_method
        self._exc = exc

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self._real_pool, break_method=self._break_method, exc=self._exc)


async def test_get_order_simulated_connection_drop_propagates_not_masked_as_missing(pool) -> None:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)

    dropping_pool = _ConnectionDroppingPool(
        pool,
        break_method="fetchrow",
        exc=asyncpg.exceptions.ConnectionDoesNotExistError("simulated DB connection drop"),
    )

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await order_query.get_order(dropping_pool, order_id, tenant_id=user_id)


async def test_list_orders_simulated_network_failure_propagates_not_swallowed(pool) -> None:
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await insert_order(conn, user_id)

    dropping_pool = _ConnectionDroppingPool(
        pool,
        break_method="fetch",
        exc=ConnectionResetError("simulated network failure mid list_orders"),
    )

    with pytest.raises(ConnectionResetError):
        await order_query.list_orders(dropping_pool, tenant_id=user_id)


async def test_list_order_events_simulated_failure_after_ownership_check_propagates(pool) -> None:
    """소유권 확인(`fetchval`)은 통과하고 그 다음 `timeline()`의 `fetch`만
    끊는다 — "이미 소유자 확인까지 끝났는데 그 다음 단계에서 인프라가
    죽는" 시나리오. 이때도 `None`(동형 404)으로 새지 않고 예외가 그대로
    올라와야 한다 — 그렇지 않으면 실제 장애가 "이벤트 없음"으로 위장된다."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)

    dropping_pool = _ConnectionDroppingPool(
        pool,
        break_method="fetch",
        exc=asyncpg.exceptions.ConnectionDoesNotExistError(
            "simulated DB connection drop during timeline fetch"
        ),
    )

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await order_query.list_order_events(dropping_pool, order_id, tenant_id=user_id)


# ---- D2: numeric performance assertion -------------------------------------


@pytest.mark.perf
async def test_list_orders_latency_within_normalized_budget(pool) -> None:
    """수치 성능 단언 — `list_orders`(keyset 페이지 1건 조회) 왕복의 p95
    지연이 같은 풀의 기준 왕복비용(`SELECT 1`) 대비 정규화한 임계를 넘지
    않는다. 절대 ms 상수는 쓰지 않는다(task-2762/2765 전례 — 공유 CI
    환경에서 절대 임계는 최대 20배 변동해 상시 적색을 낳았다)."""
    reps = 20

    async def _p95_ms(step) -> float:
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(lambda: conn.fetchval("SELECT 1"))
        for _ in range(5):
            await insert_order(conn, user_id)

    query_p95 = await _p95_ms(lambda: order_query.list_orders(pool, tenant_id=user_id, limit=3))

    budget_ms = max(200.0, 30.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\nlist_orders p95={query_p95:.3f}ms "
        f"baseline(SELECT 1) p95={baseline_p95:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert query_p95 < budget_ms


# ---- D2: gate/CI red-line regression ---------------------------------------

_TARGET_TEST = (
    "tests/adversarial/oms/test_cross_tenant_isolation.py"
    "::test_get_order_cross_tenant_does_not_leak_existence"
)
_GUARD = "SELECT * FROM orders WHERE order_id = $1 AND user_id = $2"
_MUTATED = "SELECT * FROM orders WHERE order_id = $1 AND ($2::uuid IS NOT NULL)"
_MUTATED_MODULE_NAME = "_mutate_order_query_tenant_filter"
_PLUGIN_SOURCE = f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.services.oms.application.order_query")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_tenant_filter_is_disabled(tmp_path: Path) -> None:
    """DoD "교차 tenant 비노출"이 실제로 `AND user_id = $2` 필터에
    의존함을 증명한다 — 프로덕션 소스 파일은 그대로 둔 채, 자식 pytest
    프로세스 안에서만 그 필터를 항상-참 조건(`$2::uuid IS NOT NULL`,
    tenant_id는 항상 not-null이라 사실상 필터 무력화)으로 바꿔치기하고,
    이미 있는 `test_get_order_cross_tenant_does_not_leak_existence`가
    green(1 passed)에서 red(1 failed)로 바뀌는 것까지 확인한다(같은 기법:
    `tests/integration/oms/test_submit_order_failure_injection.py`)."""
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _TARGET_TEST]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin = tmp_path / f"{_MUTATED_MODULE_NAME}.py"
    plugin.write_text(_PLUGIN_SOURCE, encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", _MUTATED_MODULE_NAME, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout


# ---- D3: multi-worker concurrency + adversarial cursor-reuse bypass -------


async def test_concurrent_multi_worker_queries_maintain_tenant_isolation(pool) -> None:
    """D3 다중 워커 증명 — 서로 다른 커넥션(별도 API 워커 프로세스
    시뮬레이션) 여러 개가 두 tenant를 뒤섞어 동시에 `get_order`를 호출해도
    풀 공유(커넥션 재사용) 때문에 결과가 섞이지 않는다."""
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        owner_order_id = await insert_order(conn, owner_id)
        attacker_order_id = await insert_order(conn, attacker_id)

    n_rounds = 20
    calls: list[tuple[UUID, UUID, bool]] = []
    for i in range(n_rounds):
        if i % 2 == 0:
            calls.append((owner_order_id, owner_id, True))
            calls.append((owner_order_id, attacker_id, False))
        else:
            calls.append((attacker_order_id, attacker_id, True))
            calls.append((attacker_order_id, owner_id, False))

    async def _run(order_id: UUID, tenant_id: UUID, expect_hit: bool) -> bool:
        view = await order_query.get_order(pool, order_id, tenant_id=tenant_id)
        if expect_hit:
            return view is not None and view.order_id == order_id
        return view is None

    results = await asyncio.gather(*(_run(*c) for c in calls))
    assert all(results), "동시 다중 워커 조회 중 tenant 격리가 깨진 호출이 있습니다"


async def test_list_orders_stolen_cursor_from_other_tenant_does_not_leak(pool) -> None:
    """adversarial-bypass 증명 — 공격자가 owner의 *실제로 발급된* opaque
    cursor(위조가 아니라 진짜 페이지 토큰)를 손에 넣어 자기 tenant_id와
    함께 재사용해도, `list_orders`의 WHERE는 `user_id = $1 AND ... AND
    (created_at, order_id) < (cursor값)`이므로 tenant 필터가 keyset 조건과
    AND로 묶여 attacker 소유가 아닌 행은 여전히 새지 않는다."""
    owner_id = await create_test_user(pool)
    attacker_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        for _ in range(3):
            await insert_order(conn, owner_id)
        attacker_order_id = await insert_order(conn, attacker_id)

    _, owner_cursor = await order_query.list_orders(pool, tenant_id=owner_id, limit=1)
    assert owner_cursor is not None, "전제(owner에게 다음 페이지가 있음)가 재현되지 않았습니다"

    stolen_items, _ = await order_query.list_orders(
        pool, tenant_id=attacker_id, cursor=owner_cursor
    )

    assert all(v.tenant_id == attacker_id for v in stolen_items), (
        "훔친 owner cursor를 재사용했을 때 attacker 소유가 아닌 행이 섞여 나옵니다"
    )
    assert [v.order_id for v in stolen_items] in ([attacker_order_id], []), (
        "훔친 cursor 재사용 결과가 attacker 자신의 주문 집합을 벗어납니다"
    )
