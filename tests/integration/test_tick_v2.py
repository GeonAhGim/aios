"""L24 게이트 D2 보강 — `src/services/execution_loop/tick.py`.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 L24(638행) —
"`test_tick_v2.py` 재시작 crossover 지연·mandate 클램프 반영".

tick.py(FD-8.1~8.4 오케스트레이터)는 이미 구현돼 있고(300행, spec 예산과
일치) 기존 negative·실패 주입 회귀는 `tests/integration/test_execution_tick.py`
(10건 이상 — 예: `test_safety_pause_mid_tick_blocks_order_submission`,
`test_concurrent_tick_race_only_submits_one_order`, `test_fsm_state_writer_
raises_on_concurrent_state_change`)가 이미 D2 하한(ADR-2026-09-09-C
Decision 1)의 negative≥3·실패 주입 1을 충족한다. 이 파일은 spec이 이름
붙인 `test_tick_v2.py`로 나머지 두 증빙 — 성능 단언과 게이트 적색 재현 —
을 채운다.

게이트 적색 재현 2건은 L24가 선행으로 의존하는 L13(영속
`StrategyStateMemory` 배선, `src/services/execution_loop/
strategy_state_store.py`)과 L23(mandate 클램프 배선,
`src/services/execution_loop/portfolio_state.py`)이 아직 구현되지
않았다는 것을 실제 파이프라인으로 증명한다 — 두 파일 모두 저장소에
존재하지 않고(`find src -iname "*strategy_state_store*"`,
`*portfolio_state*"` 0건), `src.core.portfolio.mandate_binding.bind()`도
`src/` 어디에서도 호출되지 않는다(`grep -rn "mandate_binding import" src/`
0건 — `state_input.py`의 `PortfolioStateInput.mandate` 필드도 L17이
만들어만 뒀을 뿐 `PortfolioEngine.allocate()`도 `tick.py`도 아직 읽지
않는다). 이 task는 L13/L23을 새로 구현하지 않는다(별도 리프·마이그레이션
소관 — task size 예산 300은 tick.py 현재 라인 수 그대로다) — 지금
상태가 실제로 적색임을 증명하는 것이 D2 요구사항의 전부다(N/A 아님,
ADR-2026-09-10-C Decision 4의 "적용 안 되면 N/A" 예외에 해당하지 않고
실제로 적용돼야 하는데 안 되고 있는 결함이기 때문).
"""

from __future__ import annotations

import json
import statistics
import time
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.portfolio.mandate_binding import POLICY_MAX_SINGLE_INSTRUMENT, bind
from src.data.models.trading import AccountBalance, OrderStatus
from src.services.condition_compiler import ConditionCompiler
from src.services.execution_loop.tick import run_execution_tick
from src.services.preview_service import PreviewCondition
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.test_execution_tick import _create_execution, _engines
from tests.performance.pre_trade_latency_support import PinnedConnectionPool, percentile
from tests.unit.core.portfolio.test_mandate_binding import agg, mandate


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글톤 행 — test_execution_tick.py 관례 그대로.
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _crossover_fsm_definition() -> dict:
    """SMA(5) CROSSES_BELOW 50 진입 — `StrategyEngine._prev_tick_cache`
    (engine.py:31)가 execution_id당 직전 market_state를 프로세스 메모리에만
    들고 있어야 평가 가능한 유일한 연산자군."""
    compiled = ConditionCompiler().compile(
        strategy_id="tick-v2-crossover",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[
            PreviewCondition(
                indicator="SMA",
                params={"timeperiod": 5},
                operator="crosses_below",
                threshold=50.0,
            )
        ],
        exit_conditions=[
            PreviewCondition(
                indicator="SMA", params={"timeperiod": 5}, operator=">", threshold=999999.0
            )
        ],
        stop_loss_conditions=[
            PreviewCondition(indicator="SMA", params={"timeperiod": 5}, operator="<", threshold=0.0)
        ],
    )
    return json.loads(compiled.model_dump_json())


async def _create_crossover_execution(
    pool: asyncpg.Pool, user_id: uuid.UUID, *, allocated_capital: Decimal = Decimal("1000")
) -> int:
    strategy_id = f"tick-v2-crossover-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps(_crossover_fsm_definition()),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', $3, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            allocated_capital,
        )
    return row["id"]


def _filled_adapter(close: Decimal) -> FakeExchangeAdapter:
    return FakeExchangeAdapter(
        closes=[close] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )


# --- 게이트 적색 재현 1/2 — 재시작 crossover 지연 (L13 부재) ----------------


async def test_gate_red_restart_loses_crossover_signal_at_tick_level(pool):
    """L12(`tests/unit/core/test_engine_v2.py::test_gate_red_process_cache_
    loses_crossover_state_on_restart`)가 StrategyEngine 단위에서 증명한
    `_prev_tick_cache` 프로세스 메모리 결함이, `run_execution_tick` 오케스트
    레이터 전체를 통과해도 그대로 새어나온다는 것을 보인다."""
    user_id = await create_test_tenant(pool)

    # 초록 기준선: 같은 프로세스(=같은 StrategyEngine 인스턴스)가 두 틱
    # 연속 실행되면 crossover가 정상 감지된다.
    green_execution_id = await _create_crossover_execution(pool, user_id)
    green_engines = _engines()
    await run_execution_tick(
        pool, FakeExchangeAdapter(closes=[Decimal("60")] * 65), green_execution_id, **green_engines
    )
    green_below_adapter = _filled_adapter(Decimal("40"))
    await run_execution_tick(pool, green_below_adapter, green_execution_id, **green_engines)
    assert green_below_adapter.place_order_call_count == 1, (
        "초록 기준선 실패 — 같은 프로세스에서도 crossover가 감지되지 않았다(테스트 설계 오류)"
    )

    # 적색: 완전히 동일한 가격 이력(60 -> 40, 실제로 CROSSES_BELOW 50이
    # 발생)이지만 두 번째 틱을 "재시작"(새 `_engines()` = 새 StrategyEngine
    # 인스턴스)으로 부른다. L13(영속 prev_market_state)이 없으므로 신호를
    # 완전히 잃는다.
    red_execution_id = await _create_crossover_execution(pool, user_id)
    await run_execution_tick(
        pool, FakeExchangeAdapter(closes=[Decimal("60")] * 65), red_execution_id, **_engines()
    )
    red_below_adapter = _filled_adapter(Decimal("40"))
    await run_execution_tick(pool, red_below_adapter, red_execution_id, **_engines())  # "재시작"
    assert red_below_adapter.place_order_call_count == 0, (
        "적색 재현 실패 — 재시작 후에도 crossover 신호가 살아있다면 L13가 이미 배선된 것이다"
    )
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", red_execution_id
        )
    assert fsm_state == "IDLE"  # 신호를 완전히 잃음 — 다음 재평가까지 진입 자체가 지연된다


# --- 게이트 적색 재현 2/2 — mandate 클램프 미반영 (L23 부재) ----------------


async def test_gate_red_mandate_clamp_never_applied_to_submitted_quantity(pool):
    """`mandate_binding.bind()`(L20)는 존재하고 올바르게 동작하지만
    (아래 초록), `run_execution_tick`은 그것을 호출하는 경로가 없다(적색) —
    `PortfolioEngine.allocate()`는 `current_portfolio_state` 딕셔너리의
    `mandate` 키를 아예 읽지 않고(`src/core/portfolio/engine.py`), tick.py도
    `execution["mandate_revision_id"]`를 `pre_submit_gate`(리비전 대조용
    불투명 UUID)에만 넘긴다 — 수량 자체를 mandate 한도로 줄이는 코드는 어느
    파일에도 없다."""
    # 초록: 동일한 포트폴리오 수치(총자산 10000, 무포지션)에 mandate
    # max_single_instrument_pct=2%를 걸면 20 -> 4로 정확히 클램프된다.
    unclamped_quantity = Decimal("20")
    price = Decimal("50")
    green = bind(
        unclamped_quantity,
        price,
        "BTC/USDT",
        agg(total_equity=Decimal("10000")),
        mandate(max_single_instrument_pct=2.0),
    )
    assert green.denied is False
    assert green.quantity == Decimal("4")  # 10000 * 2% / 50
    assert POLICY_MAX_SINGLE_INSTRUMENT in green.reasons

    # 적색: 정확히 같은 경제 수치(자본배분 1000, 현재가 50, 총자산 10000)로
    # 실제 tick을 돌리면 clamp 없이 20이 그대로 제출된다.
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(
        pool, user_id, entry_threshold=100.0, allocated_capital=Decimal("1000")
    )
    adapter = _filled_adapter(price)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 1
    submitted_quantity = adapter.placed_orders[-1].quantity
    assert submitted_quantity == unclamped_quantity  # mandate 클램프가 전혀 반영되지 않음
    assert submitted_quantity != green.quantity, (
        "적색 재현 실패 — 실제 제출 수량이 mandate 클램프 결과(4)와 같다면 L23가 이미 배선된 것이다"
    )


# --- 성능 단언 (1) -----------------------------------------------------------

_NO_SIGNAL_ROUND_TRIPS = 3  # _load_execution_context 2(SELECT) + R-48 distrust UPSERT 1
_NO_SIGNAL_LATENCY_CEILING_SECONDS = (
    0.5  # 환경 정규화 상한(task-1521/2835 선례) — 절대 실측은 print
)


@pytest.mark.perf
async def test_performance_no_signal_tick_round_trips_and_latency(pool):
    """성능 단언(D2) — `run_execution_tick`의 "신호 없음"(가장 빈번한, 신호가
    나지 않는 매 틱마다 반복되는) 경로가 소비하는 순차 DB 왕복 수를 정확히
    단언한다(`tests/performance/test_pre_trade_latency.py`의 R-57 선례와
    동일 원칙 — task-1521 decision: 절대 지연시간은 공유 CI 환경의 CPU·DB
    편차 신호라 게이트로 쓰지 않고, "왕복이 하나 늘었다"류 회귀를 구조적으로
    잡는 왕복 수를 정확히(==) 단언한다). 실측 p50/p95/p99는 참고용으로
    print만 하고, 절대 지연은 환경 정규화 상한(관대한 10배 이상)으로만
    막는다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=-1.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    engines = _engines()

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        pinned = PinnedConnectionPool(conn)
        await run_execution_tick(pinned, adapter, execution_id, **engines)  # 워밍업
        conn.add_query_logger(_log)
        try:
            await run_execution_tick(pinned, adapter, execution_id, **engines)
        finally:
            conn.remove_query_logger(_log)

    assert len(queries) == _NO_SIGNAL_ROUND_TRIPS, (
        f"신호 없음 경로의 순차 DB 왕복 수({len(queries)})가 예산({_NO_SIGNAL_ROUND_TRIPS})과 "
        "다릅니다 — 왕복 수 구조 변경입니다(이 파일의 예산 상수를 갱신하고 리뷰를 받으세요)."
    )

    n = 30
    samples_ms: list[float] = []
    for _ in range(n):
        started = time.perf_counter()
        await run_execution_tick(pool, adapter, execution_id, **engines)
        samples_ms.append((time.perf_counter() - started) * 1000.0)

    print(
        f"\nrun_execution_tick(no-signal) latency (n={n}): "
        f"p50={statistics.median(samples_ms):.2f}ms p95={percentile(samples_ms, 95):.2f}ms "
        f"p99={percentile(samples_ms, 99):.2f}ms max={max(samples_ms):.2f}ms "
        f"(환경 정규화 상한={_NO_SIGNAL_LATENCY_CEILING_SECONDS * 1000:.0f}ms, "
        "비차단 목표 — task-1521/2835 선례); "
        f"sequential DB round trips={_NO_SIGNAL_ROUND_TRIPS}"
    )
    assert max(samples_ms) / 1000.0 < _NO_SIGNAL_LATENCY_CEILING_SECONDS, (
        f"신호 없음 tick 지연이 환경 정규화 상한({_NO_SIGNAL_LATENCY_CEILING_SECONDS}s)을 넘었다 — "
        "회귀 의심."
    )
