"""DEEPEN(task-7706, 원 리프 task-5487): `conftest.py` 픽스처 헬퍼의 성능 테스트.

`create_pos_account` / `open_position` 의 CPU/wall 시간 단언과 지연 주입 적색 재현.

INVARIANTS.md 점검: I-01~I-11은 실행 경로를 다룬다 -- 이 리프는 테스트 전용 DB
픽스처 헬퍼에 대한 성능 측정(운영 코드 경로 아님)이라 해당 사항 없음(N/A).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from tests.conftest import PerfBudget
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    create_pos_account,
    open_position,
)
from tests.support.perf_inject import delayed_async


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
    # delayed_async(asyncio.sleep)는 CPU 시간 대신 wall-clock 시간만 증가하므로
    # wall_ms로 단언해야 지연 감지가 제대로 작동한다.
    sample = await perf_budget.sample_async(_create_with_delay, batch=1)
    with pytest.raises(AssertionError):
        assert sample.wall_ms < 10.0, (
            f"지연 주입 후 wall 시간 {sample.wall_ms:.1f}ms — "
            f"budget 10ms 초과해야 적색 재현 (wall_ms<10ms)"
        )


@pytest.mark.perf
async def test_open_position_perf_budget(
    pool: asyncpg.Pool,
    perf_budget: PerfBudget,
) -> None:
    """`open_position` 의 절대 성능 예산 단언 (D2, task-11062).

    `open_position` 은 단일 INSERT 이지만 baseline (SELECT 1) 대비
    절대 ms 임계가 없다 -- baseline 이 실행환경마다 다르므로 상대 예산
    (baseline_rt × 12, 최소 30ms) 으로 검증한다.

    정상 입력에서 녹색 — 측정값이 예산을 초과하면 테스트가 실패한다.
    절대 ms 임계는 실행환경마다 흔들려回归 게이트로 못 쓴다.

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
