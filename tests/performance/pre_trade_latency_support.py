"""R-57 `test_pre_trade_latency.py`의 시딩·시나리오·왕복 계수 헬퍼(실 DB).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-57. 계수 방식은
`tests/integration/foundation/market_data/perf_replay_support.py`
(`count_replay_round_trips`, task-1038/1405)와 동일하다: 커넥션 하나를 고정한
풀 대역(`PinnedConnectionPool`)에 asyncpg 쿼리 로거를 달아 **워밍업 1회 뒤
두 번째 호출만** 센다. 워밍업이 흡수하는 1회성 왕복 — asyncpg 코덱 조회,
R-30 equity 기준점 seed SELECT(execution당 최초 1회), R-28 캔들 캐시 채우기
(어댑터 호출, DB 아님). `conn.transaction()`의 BEGIN/COMMIT은 실제 서버
왕복이라 계수에 포함된다(asyncpg는 인자 없는 `execute()`도 로거에 남긴다).
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.portfolio.models import AllocationDecision
from src.core.risk.decision import RiskOutcome
from src.core.risk.engine import RiskEngine
from src.core.strategy.models import Signal
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import AccountBalance, OrderSide
from src.foundation.connections.domain.models import (
    AccountConnection,
    ConnectionHealth,
    ConnectionState,
    HealthState,
)
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.services.execution_loop.candle_history import CandleHistoryCache
from src.services.execution_loop.equity_tracker import ExecutionEquityTracker
from src.services.execution_loop.risk_inputs_assembler import RiskInputCaches
from src.services.execution_loop.tick_risk_phase import RiskPhaseOutcome, run_pre_trade_risk_phase
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.integration.conftest import NoopEventBus, create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter

PROVIDER = "bitget"
SYMBOL = "BTC/USDT"
_PRICE = Decimal("50")


class PinnedConnectionPool:
    """`acquire()`가 항상 미리 얻어 둔 커넥션 하나를 돌려주는 풀 대역 —
    워밍업과 계수가 같은 커넥션에서 일어나야 한다. 실제 `asyncpg.Pool.acquire()`
    컨텍스트 계약만 흉내 낸다."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        yield self._conn


class FakeHealthyConnectionRepo:
    """R-35 `test_pre_submit_gate.py`의 `_FakeConnectionRepo`와 동일 취지 —
    connection freshness는 connections 컨텍스트의 왕복이라 R-35 예산 밖."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._connection_id = uuid4()

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        return [
            AccountConnection(
                id=self._connection_id, tenant_id=self._tenant_id,
                owner_subject_id=self._tenant_id, provider_code=PROVIDER,
                opaque_account_ref="ACCT-PERF", state=ConnectionState.ACTIVE_READONLY,
                capability_profile=(), revision=1,
            )
        ]

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        return ConnectionHealth(
            connection_id=connection_id, evaluated_at=datetime.now(timezone.utc),
            state=HealthState.HEALTHY,
        )


async def _create_execution(pool, user_id: UUID, *, allocated_capital: Decimal) -> tuple[int, str]:
    strategy_id = f"perf-r57-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies (strategy_id, version, owner_user_id, target_asset, "
            "market, exchange, fsm_definition, author_agent, lifecycle_status) "
            "VALUES ($1, '1.0.0', $2, $3, 'crypto', $4, $5::jsonb, 'perf', 'APPROVED')",
            strategy_id, user_id, SYMBOL, PROVIDER, json.dumps({}),
        )
        row = await conn.fetchrow(
            "INSERT INTO strategy_executions (strategy_id, strategy_version, user_id, "
            "exchange, mode, allocated_capital, currency, status) "
            "VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', 'RUNNING') RETURNING id",
            strategy_id, user_id, PROVIDER, allocated_capital,
        )
    return int(row["id"]), strategy_id


class PreTradeScenario:
    """ALLOW가 나오는 고정 입력 — 미인증 자본배분 상한(10%) 이내: allocated 1000
    / total 10000, 수량 20 × 가격 50 = 1000. 캐시(`RiskInputCaches`·
    `CandleHistoryCache`)는 tick 경계를 넘어 재사용된다(운영 tick과 동일)."""

    def __init__(self, user_id: UUID, execution_id: int, strategy_id: str) -> None:
        self.user_id = user_id
        self.execution_id = execution_id
        self.adapter = FakeExchangeAdapter(
            closes=[_PRICE] * 100,
            usdt_balance=AccountBalance(
                exchange=PROVIDER, asset="USDT", total=Decimal("10000"),
                available=Decimal("10000"),
            ),
        )
        self.signal = Signal(
            strategy_id=strategy_id, strategy_version="1.0.0", symbol=SYMBOL,
            direction=OrderSide.BUY, confidence=1.0, target_position=Decimal("20"),
            stop_loss=None, take_profit=None, timestamp=datetime.now(timezone.utc),
            to_state=FSMState.BUY_ORDER_PENDING,
        )
        self.allocation = AllocationDecision(
            symbol=SYMBOL, strategy_id=strategy_id, approved_quantity=Decimal("20"),
            capital_pct=Decimal("10"),
        )
        self.policy = load_risk_policy()
        self.risk_engine = RiskEngine(self.policy)
        self.caches = RiskInputCaches(equity_tracker=ExecutionEquityTracker())
        self.candle_cache = CandleHistoryCache()

    async def run_once(self, pool, recorder: RiskDecisionRecorder) -> RiskPhaseOutcome | None:
        candles = await self.adapter.get_ohlcv(SYMBOL, "1m", limit=100)
        balances = await self.adapter.get_balance()
        return await run_pre_trade_risk_phase(
            pool, self.adapter, execution_id=self.execution_id, user_id=self.user_id,
            certified_badge=False, allocated_capital=Decimal("1000"), signal=self.signal,
            allocation=self.allocation, candles=candles, balances=balances,
            position_quantity=Decimal("0"), distrust_level="NORMAL",
            risk_engine=self.risk_engine, recorder=recorder, caches=self.caches,
            candle_cache=self.candle_cache, policy=self.policy,
            now=datetime.now(timezone.utc),
        )


async def new_scenario(pool) -> PreTradeScenario:
    user_id = await create_test_user(pool)
    execution_id, strategy_id = await _create_execution(
        pool, user_id, allocated_capital=Decimal("1000")
    )
    return PreTradeScenario(user_id, execution_id, strategy_id)


async def seed_normal_distrust_symbol(pool) -> str:
    """`read_safety_state`는 `data_distrust_state` 행이 없으면 distrust_level=None
    → I2 fail-closed DENY다. 실 저장소 경로로 ALLOW를 측정하려면 심볼별 행이
    있어야 하므로 테스트 전용 고유 심볼을 NORMAL로 심는다(전역 BTC/USDT 행을
    건드려 다른 테스트를 오염시키지 않는다)."""
    symbol = f"PERF{uuid.uuid4().hex[:8].upper()}/USDT"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO data_distrust_state "
            "(exchange, symbol, level, since, sources_available, updated_at) "
            "VALUES ($1, $2, 'NORMAL', now(), 3, now())",
            PROVIDER, symbol,
        )
    return symbol


def _query_logger(queries: list[str]) -> Callable[[object], None]:
    def log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    return log


async def count_pre_trade_round_trips(
    pool, scenario: PreTradeScenario, *,
    recorder_cls: type[RiskDecisionRecorder] = RiskDecisionRecorder,
) -> int:
    """`run_pre_trade_risk_phase` 1회(정상 상태)가 소비하는 순차 DB 왕복 수."""
    queries: list[str] = []
    log = _query_logger(queries)
    async with pool.acquire() as conn:
        pinned = PinnedConnectionPool(conn)
        recorder = recorder_cls(
            pinned, PostgresDecisionRepository(pinned), NoopEventBus()  # type: ignore[arg-type]
        )
        outcome = await scenario.run_once(pinned, recorder)  # 워밍업(seed·코덱·캔들 캐시)
        assert outcome is not None and outcome.decision.outcome == RiskOutcome.ALLOW
        conn.add_query_logger(log)
        try:
            outcome = await scenario.run_once(pinned, recorder)
        finally:
            conn.remove_query_logger(log)
        assert outcome is not None and outcome.decision.outcome == RiskOutcome.ALLOW
    return len(queries)


async def count_pre_submit_round_trips(
    pool, tenant_id: UUID, *,
    repo_cls: type[PostgresRiskGateRepository] = PostgresRiskGateRepository,
) -> int:
    """`evaluate_pre_submit` 1회(ALLOW 경로)가 소비하는 순차 DB 왕복 수."""
    queries: list[str] = []
    log = _query_logger(queries)
    symbol = await seed_normal_distrust_symbol(pool)
    async with pool.acquire() as conn:
        pinned = PinnedConnectionPool(conn)
        risk_repo = repo_cls(pinned)  # type: ignore[arg-type]
        recorder = RiskDecisionRecorder(
            pinned, PostgresDecisionRepository(pinned), NoopEventBus()  # type: ignore[arg-type]
        )
        connection_repo = FakeHealthyConnectionRepo(tenant_id)

        async def _once():
            return await evaluate_pre_submit(
                risk_repo, connection_repo, recorder, tenant_id=tenant_id,  # type: ignore[arg-type]
                execution_ref="exec:perf", provider_code=PROVIDER, symbol=symbol,
                trace_id=uuid4(),
            )

        await _once()  # 워밍업
        conn.add_query_logger(log)
        try:
            decision, _ = await _once()
        finally:
            conn.remove_query_logger(log)
        assert decision.outcome == RiskOutcome.ALLOW
    return len(queries)


def percentile(samples_ms: list[float], pct: float) -> float:
    """nearest-rank 백분위(보간 없음) — n=100이면 p99는 99번째로 큰 값."""
    ordered = sorted(samples_ms)
    index = min(len(ordered) - 1, max(0, round(pct / 100.0 * len(ordered)) - 1))
    return ordered[index]
