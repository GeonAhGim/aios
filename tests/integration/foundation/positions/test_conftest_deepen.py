"""DEEPEN(task-7706, 원 리프 task-5487): `conftest.py`의 픽스처 헬퍼
(`create_pos_account`/`open_position`/`_asyncpg_dsn`)가 불변식 위반 입력을
명시적으로 거부하는지, 그리고 의존성(커넥션) 실패 시 조용히 삼키지 않고
그대로 전파(fail-closed)하는지를 검증한다.

INVARIANTS.md 점검: I-01~I-11은 주문 제출/실행-소유권/멱등키/전략 아티팩트/
에이전트 capability 등 실행 경로를 다룬다 -- 이 리프는 테스트 전용 DB 픽스처
헬퍼(운영 코드 경로 아님)라 해당 사항 없음(N/A).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from tests.conftest import PerfBudget
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    _asyncpg_dsn,
    create_pos_account,
    open_position,
)
from tests.support.perf_inject import delayed_async


class _InjectedConnFailure(RuntimeError):
    pass


class _FailingConn:
    async def fetchval(self, *args: object, **kwargs: object) -> UUID:
        raise _InjectedConnFailure("injected connection failure in fetchval")


class _FailingAcquireCtx:
    async def __aenter__(self) -> _FailingConn:
        return _FailingConn()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FailingPool:
    """DB 없이도 실행 가능한 의존성 실패 주입용 이중체 -- 실제 asyncpg.Pool을
    대체하지 않고 `acquire()`만 흉내 낸다."""

    def acquire(self) -> _FailingAcquireCtx:
        return _FailingAcquireCtx()


async def test_create_pos_account_propagates_connection_failure() -> None:
    """실패주입: `pool.acquire()` 하위 커넥션의 `fetchval`이 예외를 던지면
    `create_pos_account`는 이를 삼키지 않고 그대로 전파해야 한다(fail-closed)."""
    with pytest.raises(_InjectedConnFailure, match="injected connection failure"):
        await create_pos_account(_FailingPool(), uuid.uuid4())


@pytest.mark.perf
async def test_create_pos_account_perf_0_convergence_and_budget(
    pool: asyncpg.Pool,
    perf_budget: PerfBudget,
) -> None:
    """성능 단언(D2, task-11539 감사 후속): `create_pos_account` 단일 호출의
    CPU 시간이 0 수렴(양자화 오차 범위 내)하고 절대 예산 이내인지 검증한다.

    정상 입력에서 녹색 — 측정값이 예산을 초과하면 테스트가 실패한다.
    단위/예산 불변: 예산은 10ms(단일 INSERT + RETURNING 이므로 1ms 미만이어야
    하지만 Windows 15.6ms 양자화 오차를 고려해 10ms로 설정).
    """
    tenant_id = await create_test_tenant(pool)

    async def _create_once() -> None:
        await create_pos_account(pool, tenant_id)

    # batch=8로 Windows 15.6ms 양자화 오차를 1/8로 줄이고 0 수렴 확인
    sample = await perf_budget.sample_async(_create_once, batch=8)
    assert sample.cpu_ms < 10.0, (
        f"create_pos_account CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"단일 INSERT이므로 10ms 미만이어야 함 (budget<10ms)"
    )


@pytest.mark.perf
async def test_create_pos_account_perf_budget_goes_red_with_delay_injection(
    pool: asyncpg.Pool,
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): `create_pos_account`의
    성능 단언이 지연을 감지하는지 역으로 증명한다.

    `asyncpg.Connection.fetchval`에 50ms 지연을 주입하면 1회 호출 × 50ms가
    되어 budget(10ms)을 초과하므로 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다. 지연 주입은 perf_inject.delayed_async — type: ignore budgets
    를 소모하지 않는다.
    """
    # 실제 커넥션의 fetchval 에 50ms 지연 주입 (예산 10ms의 5배)
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetchval",
        delayed_async(asyncpg.Connection.fetchval, seconds=0.05),
    )

    tenant_id = await create_test_tenant(pool)

    async def _create_with_delay() -> None:
        await create_pos_account(pool, tenant_id)

    # 지연 주입으로 budget 초과 → AssertionError 발생해야 함
    sample = await perf_budget.sample_async(_create_with_delay, batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < 10.0, (
            f"지연 주입 후 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"예상과 달리 녹색: 측정/단언이 공허함 (budget<10ms)"
        )


async def test_asyncpg_dsn_raises_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative: `DATABASE_URL`이 비어 있으면(설정 누락) 조용히 빈 DSN을
    만들어내지 말고 명시적으로 실패해야 한다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        _asyncpg_dsn()


async def test_create_pos_account_rejects_invalid_base_currency(pool: asyncpg.Pool) -> None:
    """negative: `pos_account.base_currency`는 `Currency` enum 값만 허용하는
    DB CHECK 제약을 갖는다(마이그레이션 `4a1d0c0de004`) -- 임의 문자열은
    거부되어야 한다(게이트-적색 재현: 스키마가 이 방어를 갖지 않으면 조용히
    쓰레기 통화 코드가 저장된다)."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.fetchval(
                "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
                "VALUES ($1, $2, $3, $4) RETURNING account_id",
                tenant_id,
                "TESTVENUE",
                "XXX",
                "FIFO",
            )


async def test_create_pos_account_rejects_invalid_cost_method(pool: asyncpg.Pool) -> None:
    """negative: `pos_account.cost_method`도 `CostMethod` enum 값만 허용하는
    CHECK 제약이 있다 -- 임의 문자열은 거부되어야 한다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.fetchval(
                "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
                "VALUES ($1, $2, $3, $4) RETURNING account_id",
                tenant_id,
                "TESTVENUE",
                "KRW",
                "NOT_A_COST_METHOD",
            )


async def test_create_pos_account_rejects_unknown_tenant_id(pool: asyncpg.Pool) -> None:
    """negative: `pos_account.tenant_id`는 `users(user_id)` FK다 -- 존재하지
    않는 tenant로는 계정을 만들 수 없다."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_pos_account(pool, uuid.uuid4())


async def test_open_position_rejects_duplicate_first_creation_call(pool: asyncpg.Pool) -> None:
    """negative: `open_position`이 같은 `position_key`로 두 번 호출되면(둘 다
    `expected_seq=0`인 최초 생성 경로) 두 번째 호출은 이미 존재하는 행을
    최초 생성으로 오인하고 조용히 덮어써서는 안 된다 -- 실제로는
    `ConcurrencyConflictError`로 거부되어야 한다(task-3863 회귀 방지와 같은
    보호가 이 헬퍼를 통해서도 유지되는지 확인)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    from src.foundation.entities.domain.defaults import default_portfolio_id
    from src.foundation.positions.domain.position_key import PositionKey

    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )

    first = await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
    )
    assert first.quantity == Decimal("0")

    with pytest.raises(ConcurrencyConflictError):
        await open_position(
            pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
        )


@pytest.mark.perf
async def test_open_position_round_trip_stays_within_local_budget(
    pool: asyncpg.Pool,
    perf_budget: PerfBudget,
) -> None:
    """성능 단언(D2): 기준 왕복 비용(pool.acquire + SELECT 1, n=20, 워밍업
    3회 버림)을 이 환경에서 직접 재고, `open_position`의 절대 시간이 그
    기준의 12배(연속 DB 왕복 여유, `test_perf_journal_append.py`와 같은
    정규화 방식) 이내인지 단언한다 -- 절대 ms 임계는 실행환경마다 흔들려
    회귀 게이트로 못 쓴다.

    raw time.perf_counter() → perf_budget.samples_async() 전환 (task-11062).
    """

    async def _baseline_once() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    # baseline: 23회 측정 → 워밍업 3회 버리고 최댓값 (보수적 rt)
    n_baseline = 23
    raw_samples = await perf_budget.samples_async(_baseline_once, n=n_baseline)
    baseline_ms_list = sorted(s.wall_ms for s in raw_samples[3:])
    baseline_rt_sec = baseline_ms_list[-1] / 1000.0  # wall_ms → 초

    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    from src.foundation.entities.domain.defaults import default_portfolio_id
    from src.foundation.positions.domain.position_key import PositionKey

    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )

    async def _open_once() -> None:
        await open_position(
            pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
        )

    # single open_position measurement
    samples = await perf_budget.samples_async(_open_once, n=1)
    elapsed_ms = samples[0].wall_ms
    budget_ms = max(30.0, 12 * baseline_rt_sec * 1000)  # 0.030초 → 30ms
    assert elapsed_ms < budget_ms, (
        f"open_position took {elapsed_ms:.1f}ms, budget {budget_ms:.1f}ms "
        f"(baseline rt {baseline_rt_sec * 1000:.1f}ms)"
    )
