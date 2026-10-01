"""R-3/R-28 `risk_inputs_assembler.py` D3 증빙 — negative/실패주입/성능.

depth=D3: circuit_breaker safety_state, correlation_rule, DB failure propagation,
missing fields become None, latency budget, concurrent equity peak convergence.
`test_risk_inputs_assembler.py`(R-31 기본 계약)에서 분리(task-10539,
loc_over_500 분리, CLAUDE.md §7). 제품 코드는 변경하지 않는다.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.core.risk.rules import correlation, safety_state
from src.data.models.trading import AccountBalance
from src.services.execution_loop.risk_inputs_assembler import assemble_risk_inputs
from tests.integration.conftest import create_test_user
from tests.integration.risk._risk_inputs_assembler_fixtures import (
    NOW,
    POLICY,
    asyncpg_dsn,
    caches,
    candles,
    create_execution,
    insert_position,
    intent,
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


async def test_circuit_breaker_halted_denies_via_safety_state_rule(pool):
    """negative + 게이트 적색 재현(D2) — CB 레벨이 halted면 조립된
    `SafetyInputs.circuit_breaker_level`이 그 값을 그대로 실어 safety_state
    규칙을 실제로 DENY시킨다(차단 판정 자체는 R-13 책임이지만, 이 리프가
    조립한 값이 적색을 가린 채 통과시키지 않음을 증명한다)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'halted' WHERE id = 1"
        )
    try:
        riskcaches = caches()
        balances = [
            AccountBalance(
                exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
            )
        ]
        inputs = await assemble_risk_inputs(
            pool,
            riskcaches,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-cb-halted"),
            balances=balances,
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        )
        assert inputs.safety.circuit_breaker_level == "halted"

        isolated = inputs.model_copy(
            update={
                "safety": inputs.safety.model_copy(update={"active_control_scopes": ()}),
            }
        )
        result = safety_state.safety_state(isolated, POLICY)
        assert result.outcome == RiskOutcome.DENY
        assert result.reason_code == "RISK_CIRCUIT_BREAKER_HALTED"
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE system_safety_state SET circuit_breaker_level = 'normal' WHERE id = 1"
            )


async def test_cross_symbol_exposure_denies_via_correlation_rule(pool):
    """negative(D2) — 이 심볼 말고 다른 심볼에도 보유가 있으면(§3.5 각주
    범위 밖) `missing_pairs`가 채워지고, correlation 규칙이 0.0 암묵 치환
    없이 그대로 DENY한다(R3 레거시 결함 재현 금지)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    other_symbol = "ETH/USDT"
    strategy_id = "strat-cross-symbol"

    await insert_position(
        pool,
        user_id=user_id,
        symbol=other_symbol,
        exchange="bitget",
        strategy_id=strategy_id,
        quantity=Decimal("1"),
        average_entry_price=Decimal("100"),
    )

    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]
    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, strategy_id),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )
    assert inputs.stats.missing_pairs == ("exposure:other_symbols_unresolved",)
    assert inputs.stats.correlated_exposure_pct is None

    result = correlation.correlation(inputs, POLICY)
    assert result.outcome == RiskOutcome.DENY
    assert result.reason_code == "RISK_INPUT_MISSING:stats.missing_pairs"


async def test_exposure_query_failure_propagates_not_swallowed(pool, monkeypatch):
    """negative + 실패주입(D2) — §3.5 노출 스냅샷 쿼리가 DB 레벨에서 실패하면
    예외가 그대로 전파된다. 기본값(0/None)으로 조용히 대체해 뒤이은 게이트가
    "포지션 없음"으로 착각하고 ALLOW로 새는 일이 없어야 한다(I2)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    original_fetchrow = asyncpg.Connection.fetchrow

    async def failing_fetchrow(self, query, *args, **kwargs):
        if "open_pos" in query:
            raise asyncpg.PostgresConnectionError("simulated exposure query failure")
        return await original_fetchrow(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", failing_fetchrow)

    with pytest.raises(asyncpg.PostgresConnectionError):
        await assemble_risk_inputs(
            pool,
            riskcaches,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-query-failure"),
            balances=balances,
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        )


async def test_missing_usdt_balance_fields_become_none_not_zero(pool):
    """negative(D2) — balances에 USDT 행이 없으면(거래소가 그 자산을 아직
    보고하지 않음 등) `total_equity`/`available_balance`뿐 아니라 그로부터
    파생되는 `daily_pnl_pct`/`drawdown_pct`/`day_start_equity`/`peak_equity`/
    `correlated_exposure_pct`가 모두 명시적 None이 된다 — 0으로 뭉개져
    "자산 0"(알려진 값)과 "자산 모름"(결손)을 혼동하지 않는다(I2)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="KRW", total=Decimal("100000"), available=Decimal("90000")
        )
    ]

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-no-usdt"),
        balances=balances,
        candles=candles(symbol, "bitget", 3),
        policy=POLICY,
        now=NOW,
    )

    assert inputs.equity.total_equity is None
    assert inputs.equity.available_balance is None
    assert inputs.equity.daily_pnl_pct is None
    assert inputs.equity.drawdown_pct is None
    assert inputs.equity.day_start_equity is None
    assert inputs.equity.peak_equity is None
    assert inputs.stats.correlated_exposure_pct is None


async def test_empty_candles_var_and_price_fields_none(pool):
    """negative(D2) — 캔들이 없으면(신규 상장 심볼, 피드 중단 등) VAR/ES/
    상관 관련 필드가 0.0/NORMAL로 암묵 치환되지 않고 명시적 None이 된다
    (R-28 candle_history.py가 stale 데이터를 돌려주지 않는 것과 같은
    fail-closed 원칙 — 이 조립기는 candle_history를 거치지 않고 호출부가
    넘긴 candles를 그대로 쓰므로, 빈 리스트가 들어오면 '측정 불가'를
    '정상 0'으로 둔갑시키지 않아야 한다)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "NEWLIST/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    inputs = await assemble_risk_inputs(
        pool,
        riskcaches,
        execution_id=execution_id,
        user_id=user_id,
        intent=intent(symbol, "strat-no-candles"),
        balances=balances,
        candles=[],
        policy=POLICY,
        now=NOW,
    )

    assert inputs.stats.var_pct is None
    assert inputs.stats.es_pct is None
    assert inputs.stats.var_method is None
    assert inputs.stats.lookback_bars is None
    assert inputs.stats.bars_used is None


async def test_fence_query_failure_propagates_not_swallowed(pool, monkeypatch):
    """negative + 실패주입(D2) — §3.5 read_fence 왕복(2회 중 두 번째)이 DB
    레벨에서 실패하면 예외가 그대로 전파된다. 첫 번째 왕복(노출 스냅샷)만
    성공했다고 해서 조립 결과를 절반만 채운 채 통과시키면 뒤이은 risk
    게이트가 fence_snapshot 없이 ALLOW로 샐 수 있다(I2, R-30과 동일하게
    read_fence 책임 경로이지만 이 조립기가 그 실패를 가리지 않음을
    증명한다)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    original_fetch = asyncpg.Connection.fetch

    async def failing_fetch(self, query, *args, **kwargs):
        if "fence" in query.lower():
            raise asyncpg.PostgresConnectionError("simulated fence query failure")
        return await original_fetch(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetch", failing_fetch)

    with pytest.raises(asyncpg.PostgresConnectionError):
        await assemble_risk_inputs(
            pool,
            riskcaches,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-fence-failure"),
            balances=balances,
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        )


@pytest.mark.perf
async def test_assemble_risk_inputs_latency_within_order_submission_budget(pool, perf_budget):
    """수치 성능 단언(D2) — `assemble_risk_inputs`는 주문 제출 경로의
    일부다. ADR-2026-09-09-C §축별 예산표의 "주문 제출→ACK p95 50ms(paper)"
    전체 예산 중, 이 조립기 자체(2회 SELECT)가 그 예산의 대부분을 혼자
    쓰면 안 된다 — 동일 예산을 서브단계 상한으로 그대로 쓴다."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    riskcaches = caches()
    balances = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]

    budget_ms = 50.0
    latencies_ms: list[float] = []
    for _ in range(10):
        sample = await perf_budget.sample_async(
            lambda: assemble_risk_inputs(
                pool,
                riskcaches,
                execution_id=execution_id,
                user_id=user_id,
                intent=intent(symbol, "strat-perf"),
                balances=balances,
                candles=candles(symbol, "bitget", 3),
                policy=POLICY,
                now=NOW,
            )
        )
        latencies_ms.append(sample.wall_ms)

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95) - 1]
    print(f"\nassemble_risk_inputs latency p95={p95_ms:.3f}ms (n=10, budget<{budget_ms}ms)")

    assert p95_ms < budget_ms, (
        f"assemble_risk_inputs p95 지연({p95_ms:.3f}ms)이 주문 제출→ACK 예산"
        f"({budget_ms}ms) 서브단계 상한을 초과했습니다 — 성능 회귀입니다."
    )


async def test_two_instances_concurrent_equity_peak_converges(pool):
    """다중 인스턴스 동시성(D3) — 서로 다른 프로세스를 흉내내는 두 개의
    독립 `RiskInputCaches`(각자 신선한 `ExecutionEquityTracker`)가 같은
    execution_id에 서로 다른 equity로 동시에 조립을 호출해도 예외로
    죽지 않고, DB의 peak_equity는 `GREATEST` 조건부 UPDATE(R-30 §5)로
    두 값 중 큰 쪽에 단조 수렴한다(lost update 없음)."""
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"

    caches_a = caches()
    caches_b = caches()
    balances_low = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
        )
    ]
    balances_high = [
        AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("1500"), available=Decimal("1400")
        )
    ]

    results = await asyncio.gather(
        assemble_risk_inputs(
            pool,
            caches_a,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-multi-a"),
            balances=balances_low,
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        ),
        assemble_risk_inputs(
            pool,
            caches_b,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, "strat-multi-b"),
            balances=balances_high,
            candles=candles(symbol, "bitget", 3),
            policy=POLICY,
            now=NOW,
        ),
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    assert not failures, results

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT equity_peak_value FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row["equity_peak_value"] == Decimal("1500")
