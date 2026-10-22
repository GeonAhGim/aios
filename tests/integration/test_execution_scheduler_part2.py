"""FD-8 실행 루프 스케줄러 통합테스트 — RUNNING PAPER 실행 전부를 tick.

전수감사(docs/FULL_AUDIT_2026-09-02.md §3) 회귀: run_execution_tick은 완전했지만
운영 앱에서 호출되지 않았다. 이 테스트는 스케줄러가 실제 DB의 RUNNING 실행을
찾아 tick하고, 한 실행의 실패가 나머지를 막지 않으며, LIVE·비RUNNING 실행은
건드리지 않음을 실제 Postgres 위에서 검증한다.

공유 dev DB에는 다른 테스트가 남긴 RUNNING 실행이 있을 수 있다 — 어댑터
리졸버가 이 테스트의 사용자만 알고 나머지는 CredentialNotFoundError를 내므로
그 실행들은 "자격증명 없음"으로 건너뛰어진다. 따라서 단언은 "내 실행 ⊆ 결과"
형태로만 쓴다.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast

import asyncpg
import pytest

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.safety.data_distrust import DataDistrustMonitor
from src.data.models.market_data import Ticker
from src.data.models.trading import AccountBalance, OrderStatus
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from src.services.credential_resolver import CredentialNotFoundError
from src.services.execution_loop.scheduler import ExecutionLoopScheduler
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests._perf.relative_budget import RelativeBudget
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution


async def _measure_tick_all_running_seconds(
    scheduler: ExecutionLoopScheduler,
) -> tuple[float, Any]:
    """`scheduler.tick_all_running()` 1회 왕복을 `RelativeBudget.measure_async`로
    잰다(task-11433, raw `time.perf_counter()` 단언 전환 —
    scripts/check_perf_measurement_guard.py). DB 왕복·엔진 체인을 포함한
    비동기 I/O라 `PerfBudget`(process_time 기반)이 아니라 wall-clock 기반
    `measure_async`를 쓴다 — 예산 값(2.0s)은 바꾸지 않고 측정 경로만
    coverage tracer-pause가 적용된 공용 헬퍼로 옮긴다."""
    result: Any = None

    async def _tick() -> None:
        nonlocal result
        result = await scheduler.tick_all_running()

    sample = await RelativeBudget().measure_async(_tick, n=1, warmup=0, calibration_n=1)
    return sample.op_ms / 1000, result


def _asyncpg_dsn() -> str:
    # tests/conftest.py가 TEST_DATABASE_URL을 DATABASE_URL 환경변수로 옮겨
    # 두므로(이 worktree 전용 DB), 다른 통합테스트(execution_ownership/conftest.py
    # 등)와 동일하게 os.environ에서 읽는다 — .env 파일을 직접 파싱하면 이
    # override를 우회해 공유 dev DB(migrations 미적용 가능)로 잘못 붙는다.
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _allow_all(_context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid.uuid4(), compliance_decision_id=uuid.uuid4()
    )


def _owner_id() -> str:
    # execution_leases는 이 테스트 세션 전체가 공유하는 테이블이라(트랜잭션
    # 롤백 격리 없음) 매 테스트마다 고유한 owner_id를 써야 다른 테스트가
    # 남긴 리스와 섞이지 않는다(test_postgres_lease_repository.py와 동일 이유).
    return f"scheduler-test-owner-{uuid.uuid4().hex[:8]}"


def _scheduler(pool: asyncpg.Pool, **overrides: Any) -> ExecutionLoopScheduler:
    kwargs: dict[str, Any] = dict(
        resolve_adapter=_resolver_for({}),
        policy=load_risk_policy(),
        pre_submit_gate=_allow_all,
        distrust_monitor=DataDistrustMonitor(),
        lease_repo=PostgresExecutionLeaseRepository(pool),
        owner_id=_owner_id(),
    )
    kwargs.update(overrides)
    return ExecutionLoopScheduler(pool, **kwargs)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _filled_adapter() -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


def _resolver_for(adapters: dict[uuid.UUID, ExchangeAdapter]):
    async def resolve(user_id: uuid.UUID, exchange: str) -> ExchangeAdapter:
        try:
            return adapters[user_id]
        except KeyError as exc:
            raise CredentialNotFoundError(f"{exchange} 자격증명 없음(테스트 리졸버)") from exc

    return resolve


async def _fsm_state(pool: asyncpg.Pool, execution_id: int) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )


def _reference_ticker(price: str) -> Ticker:
    return Ticker(
        symbol="BTC/USDT",
        exchange="reference",
        price=Decimal(price),
        bid=Decimal(price),
        ask=Decimal(price),
        volume_24h=Decimal("1"),
        timestamp=datetime.now(timezone.utc),
        source_type="reference",
    )


class _StubReferenceProvider:
    def __init__(self, price: str) -> None:
        self._price = price

    async def get_reference_ticker(self, symbol: str) -> Ticker | None:
        return _reference_ticker(self._price)


@pytest.mark.perf
async def test_tick_all_running_p95_latency_within_budget(pool):
    """성능 단언(D2, ADR-2026-09-09-C Decision 1) — 이 스케줄러 축은 예산표에
    전용 행이 없다(가장 가까운 행은 "주문 제출→ACK p95 50ms(paper)"이지만
    tick_all_running은 전략/포트폴리오/리스크/실행 4엔진 + 리스 획득 + DB
    왕복을 한 번에 묶으므로 그보다 넉넉해야 한다). 회귀 감지용으로 2개 실행을
    동시에 tick하는 데 2초를 예산으로 건다."""
    durations: list[float] = []
    for _ in range(5):
        user_a, user_b = await create_test_tenant(pool), await create_test_tenant(pool)
        exec_a = await _create_execution(pool, user_a, entry_threshold=100.0)
        exec_b = await _create_execution(pool, user_b, entry_threshold=100.0)
        scheduler = _scheduler(
            pool,
            resolve_adapter=_resolver_for({user_a: _filled_adapter(), user_b: _filled_adapter()}),
        )

        elapsed_sec, report = await _measure_tick_all_running_seconds(scheduler)
        durations.append(elapsed_sec)

        assert {exec_a, exec_b} <= set(report.ticked)

    durations.sort()
    p95 = durations[-1]
    assert p95 < 2.0, f"tick_all_running p95 latency {p95:.3f}s exceeded 2.0s budget"


def test_interval_comes_from_risk_policy():
    policy = load_risk_policy()
    # DB 없이 policy 인자만 검증(pool은 사용되지 않음) — cast로 실제 asyncpg.Pool
    # 시그니처는 그대로 두고 이 호출 지점에서만 None을 허용한다.
    scheduler = _scheduler(cast(asyncpg.Pool, None), policy=policy)
    assert scheduler.interval_seconds == policy.execution_loop.interval_sec
