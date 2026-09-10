"""R-57 — 사전검사(PRE_TRADE t0~t6 + PRE_SUBMIT) 지연 측정 + CI 왕복 수 단언(실 DB).

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-57 ("p99 ≤ 50 ms 단언", 선행
R-32 `bc2d7da`), §3.5(R-31 2왕복 예산), §3.6(R-35 PRE_SUBMIT).

**측정 대상**은 `tick_risk_phase.run_pre_trade_risk_phase()`(R-32, t0 intent →
t1 캔들 캐시 → t2 assemble_risk_inputs → t3 check_decision → t4 WORM 기록 →
t5 TTL → t6 REDUCE 축소) 단독 호출과 `evaluate_pre_submit()`(R-35) 단독
호출이다. 전략 평가·executor·FSM 쓰기(t7 이후)는 범위 밖이다. 시딩·시나리오·
왕복 계수 헬퍼는 `pre_trade_latency_support.py`.

**CI 게이트 = 순차 DB 왕복 수 정확 단언, 절대시간은 print(비차단)**:
task-1521 decision — "절대 지연 단언(p99 ≤ 50 ms)은 선례(task-1038 `3ea1fc1`
· task-1405 `90f5c17` · task-822 `88932f9`)대로 환경 정규화 상한 또는
print+왕복수 단언으로 대체하고 로컬 실측값은 task note에 기록 — 절대값
단언으로 CI 상시 적색을 만들지 말 것". 공유 CI에서 p99 절대값은 이 파일이
통제할 수 없는 CPU·DB 편차 신호이고(로컬 실측 n=100, 2회: p50≈19~20 ms,
p95≈29~43 ms, p99≈39~80 ms — 같은 코드가 실행마다 목표 50 ms를 넘나든다),
사전검사 지연의 회귀 원인은
사실상 전부 "왕복이 하나 늘었다"(행별 조회·중복 SELECT·seed 재조회)라서
왕복 수를 **정확히**(≤가 아니라 ==) 단언한다 — 왕복이 줄어도 예산 상수를
갱신해야 하므로 구조 변경이 항상 리뷰에 드러난다. 임계 상향도 xfail 은닉도
아니다(task-920 XPASS strict 전례). src 무수정.

PRE_TRADE 정상 상태 예산(`_PRE_TRADE_ROUND_TRIPS` = 7) 구성:
  t2 R-31 `assemble_risk_inputs` — `load_exposure_snapshot` CTE 1 +
     `read_fence_snapshot` 1 + R-30 `save_equity_baseline` 조건부 UPDATE 1 = 3
     (§3.5 "정확히 2회 SELECT" + R-30 write-through 1)
  `list_active_controls`(kill switch 우회불가, R-32) = 1
  t4 R-25 `RiskDecisionRecorder.record` — 시계 드리프트 `SELECT now()` 1 +
     `risk_decision` WORM INSERT 1 + `audit_log` INSERT 1 = 3
PRE_SUBMIT 예산(`_PRE_SUBMIT_ROUND_TRIPS` = 8) 구성:
  `read_fence_and_controls` — BEGIN + fence SELECT + control SELECT + COMMIT = 4
  (§3.6 같은 트랜잭션 스냅샷) · `read_safety_state` 1 · connection freshness는
  connections 컨텍스트(R-35 테스트와 같은 fake, DB 0) · recorder 3.
  task-1532(ae2a452) 의도 결속 키(`side`·`quantity`)는 WORM 스냅샷 필드만 늘리고
  왕복은 0 — 재실측 8 유지(task-1550). WORM 재조회는 `fenced_submit` 범위.

negative test 3개(I-10 — 게이트가 "있다"가 아니라 "작동함"): 왕복을 하나 더
내는 recorder/저장소를 끼우면 계수가 예산과 정확히 1 어긋나 실제로 실패하고,
WORM insert 자체가 실패하면 예외가 삼켜지지 않고 그대로 전파된다(fail-closed).

task-2835(DEPTH 감사 D1→D3) 추가분:
  - 성능 단언(D2): `_measure_and_gate` 왕복 수 게이트는 "왕복이 하나 늘었다"류
    회귀만 잡는다 — 같은 왕복 *안에서* 스캔 비용이 커지는 회귀(R-27 노출
    스냅샷의 `orders WHERE execution_id = $3` 집계 등)는 못 잡는다. 이
    execution_id에 주문 이력을 대량으로 채운 뒤에도 phase가 관대한 절대
    상한(task-1038/1405/822 선례와 동일한 "환경 정규화 상한" 방식) 안에서
    끝나는지 확인한다.
  - 실패 주입(D2): t4 WORM insert(R-24)가 실패하면 예외가 그대로 전파되고
    그 뒤 audit_log 삽입까지 도달하지 않아야 한다(record()의 순서 보장,
    무기록 승인 방지).
  - 다중 인스턴스 동시성 증명(D3): 서로 다른 tenant의 사전검사가 동시에
    실행돼도 각자의 WORM 기록이 자신의 tenant_id로만 귀속된다(컨텍스트
    오염 없음).
"""
from __future__ import annotations

import asyncio
import os
import statistics
import time

import asyncpg
import pytest

from src.core.risk.decision import RiskOutcome
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.integration.conftest import NoopEventBus, create_test_tenant
from tests.performance.pre_trade_latency_support import (
    PROVIDER,
    SYMBOL,
    count_pre_submit_round_trips,
    count_pre_trade_round_trips,
    new_scenario,
    percentile,
)

_PRE_TRADE_ROUND_TRIPS = 7  # 모듈 docstring 구성표 — 정확 단언(==)
_PRE_SUBMIT_ROUND_TRIPS = 8  # 모듈 docstring 구성표 — 정확 단언(==)
_P99_TARGET_MS = 50.0  # §9 R-57 운영 목표 — 여기서는 비차단(print)
_SAMPLE_COUNT = int(os.environ.get("AIOS_PRE_TRADE_LATENCY_SAMPLES", "100"))


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    async with p.acquire() as conn:
        # system_safety_state는 전역 싱글톤 행 — test_execution_tick.py 관례대로
        # 다른 파일이 남긴 CB 레벨을 정상으로 되돌린다(DENY로 새면 t5에서 끊겨
        # t6까지의 경로가 측정되지 않는다).
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.mark.perf
async def test_pre_trade_phase_p99_measured_and_round_trips_exact(pool):
    """R-57 — t0~t6 N회 실측(p50/p95/p99 print, 50 ms 목표 비차단) + 순차 DB
    왕복 수 정확 단언(CI 게이트)."""
    scenario = await new_scenario(pool)
    recorder = RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), NoopEventBus())
    warm = await scenario.run_once(pool, recorder)  # seed·캐시 워밍업(정상 상태 측정)
    assert warm is not None and warm.decision.outcome == RiskOutcome.ALLOW

    samples_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        started = time.perf_counter()
        outcome = await scenario.run_once(pool, recorder)
        samples_ms.append((time.perf_counter() - started) * 1000.0)
        assert outcome is not None and outcome.decision.outcome == RiskOutcome.ALLOW

    round_trips = await count_pre_trade_round_trips(pool, scenario)
    print(
        f"\npre_trade_risk_phase latency (n={_SAMPLE_COUNT}): "
        f"p50={statistics.median(samples_ms):.2f}ms p95={percentile(samples_ms, 95):.2f}ms "
        f"p99={percentile(samples_ms, 99):.2f}ms max={max(samples_ms):.2f}ms "
        f"(target p99<{_P99_TARGET_MS}ms §9 R-57 운영 목표, 비차단 — task-1521 decision); "
        f"sequential DB round trips={round_trips} (budget=={_PRE_TRADE_ROUND_TRIPS})"
    )
    assert round_trips == _PRE_TRADE_ROUND_TRIPS, (
        f"PRE_TRADE t0~t6 순차 DB 왕복 수({round_trips})가 예산({_PRE_TRADE_ROUND_TRIPS})과 "
        "다릅니다 — 왕복 수 구조 변경입니다(모듈 docstring 구성표를 갱신하고 리뷰를 받으세요)."
    )
    # 절대시간(p99 ≤ 50 ms)은 게이트로 쓰지 않는다(모듈 docstring, task-1521 decision).


async def test_pre_submit_gate_round_trips_exact(pool):
    """R-35 PRE_SUBMIT(t7 직전) 순차 DB 왕복 수 정확 단언."""
    tenant_id = await create_test_tenant(pool)
    round_trips = await count_pre_submit_round_trips(pool, tenant_id)
    print(
        f"\npre_submit gate sequential DB round trips={round_trips} "
        f"(budget=={_PRE_SUBMIT_ROUND_TRIPS})"
    )
    assert round_trips == _PRE_SUBMIT_ROUND_TRIPS, (
        f"PRE_SUBMIT 순차 DB 왕복 수({round_trips})가 예산({_PRE_SUBMIT_ROUND_TRIPS})과 "
        "다릅니다 — 왕복 수 구조 변경입니다(모듈 docstring 구성표를 갱신하고 리뷰를 받으세요)."
    )


class _ChattyRecorder(RiskDecisionRecorder):
    """negative 전용 — 기록 전에 불필요한 왕복 하나를 더 낸다."""

    async def record(self, decision, inputs, *, actor: str) -> None:  # type: ignore[override]
        async with self._pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await super().record(decision, inputs, actor=actor)


class _ChattyRiskRepo(PostgresRiskGateRepository):
    """negative 전용 — 안전 상태를 읽기 전에 불필요한 왕복 하나를 더 낸다."""

    async def read_safety_state(self, *, provider_code: str, symbol: str):
        async with self._pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return await super().read_safety_state(provider_code=provider_code, symbol=symbol)


async def test_pre_trade_round_trip_gate_detects_extra_query(pool):
    """negative(I-10): recorder가 왕복을 하나 더 내면 계수가 예산과 정확히 1 어긋난다."""
    scenario = await new_scenario(pool)
    round_trips = await count_pre_trade_round_trips(pool, scenario, recorder_cls=_ChattyRecorder)
    assert round_trips == _PRE_TRADE_ROUND_TRIPS + 1
    assert round_trips != _PRE_TRADE_ROUND_TRIPS


async def test_pre_submit_round_trip_gate_detects_extra_query(pool):
    """negative(I-10): 저장소가 왕복을 하나 더 내면 계수가 예산과 정확히 1 어긋난다."""
    tenant_id = await create_test_tenant(pool)
    round_trips = await count_pre_submit_round_trips(pool, tenant_id, repo_cls=_ChattyRiskRepo)
    assert round_trips == _PRE_SUBMIT_ROUND_TRIPS + 1
    assert round_trips != _PRE_SUBMIT_ROUND_TRIPS


_ORDER_HISTORY_ROWS = 2000
_ORDER_HISTORY_LATENCY_CEILING_SECONDS = 0.5  # _P99_TARGET_MS(50ms)의 10배 — 환경 정규화 상한


@pytest.mark.perf
async def test_pre_trade_phase_stays_fast_with_large_order_history(pool):
    """성능 단언(D2, task-2835) — 왕복 수 게이트(위 테스트들)는 "왕복이 하나
    늘었다"류 회귀만 잡고, R-27 노출 스냅샷의 `orders WHERE execution_id = $3`
    집계(trades_1h/trades_24h)처럼 같은 왕복 *안에서* 스캔 비용이 커지는
    회귀는 놓친다. 이 execution_id에 주문 이력 2,000행을 채운 뒤에도 phase
    1회가 관대한 절대 상한 안에서 끝나는지 확인한다 — task-1038/1405/822
    선례와 동일하게, 정상 실측(p50≈19~20ms)의 10배를 상한으로 두어 CPU 편차로
    상시 적색이 되는 것을 피하면서도 스캔 비용 폭증(예: 인덱스 누락)은 잡는다.
    `created_at`을 2일 전으로 심어 trades_1h/24h 과다거래 룰(R-27 집계 소비자)을
    건드리지 않고 순수 스캔 비용만 키운다."""
    scenario = await new_scenario(pool)
    recorder = RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), NoopEventBus())
    warm = await scenario.run_once(pool, recorder)
    assert warm is not None and warm.decision.outcome == RiskOutcome.ALLOW

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity,
                is_liquidation, execution_id, created_at
            )
            SELECT $1, 'perf-r57-hist-' || $5 || '-' || gs::text, $2, '1.0.0', $3, $4,
                   'BUY', 'MARKET', 1, 'FILLED', 1, false, $6, now() - interval '2 days'
            FROM generate_series(1, $7) AS gs
            """,
            scenario.user_id,
            scenario.signal.strategy_id,
            SYMBOL,
            PROVIDER,
            str(scenario.execution_id),
            scenario.execution_id,
            _ORDER_HISTORY_ROWS,
        )

    started = time.perf_counter()
    outcome = await scenario.run_once(pool, recorder)
    elapsed_seconds = time.perf_counter() - started
    print(
        f"\npre_trade_risk_phase latency with {_ORDER_HISTORY_ROWS}-row order history: "
        f"{elapsed_seconds * 1000:.2f}ms (ceiling={_ORDER_HISTORY_LATENCY_CEILING_SECONDS}s)"
    )
    assert outcome is not None and outcome.decision.outcome == RiskOutcome.ALLOW
    assert elapsed_seconds < _ORDER_HISTORY_LATENCY_CEILING_SECONDS, (
        f"주문 이력 {_ORDER_HISTORY_ROWS}행 상태에서 사전검사 지연이 {elapsed_seconds:.3f}s로 "
        f"상한({_ORDER_HISTORY_LATENCY_CEILING_SECONDS}s)을 넘었다 — R-27 노출 스냅샷의 "
        "execution_id 집계가 인덱스를 타지 못하는 회귀 의심."
    )


class _FailingDecisionRepo(PostgresDecisionRepository):
    """실패 주입 전용(task-2835) — WORM insert가 DB 장애로 실패하는 상황을
    흉내낸다(예: 커넥션 단절·디스크 풀)."""

    async def insert(self, decision, inputs_snapshot):  # type: ignore[override]
        raise RuntimeError("simulated WORM write failure (fault injection)")


async def test_pre_trade_phase_propagates_worm_write_failure_fail_closed(pool):
    """실패 주입(D2, task-2835) — t4 `RiskDecisionRecorder.record`의 WORM
    insert(R-24)가 실패하면 `run_pre_trade_risk_phase`는 예외를 삼키지 않고
    그대로 전파해야 한다. 삼켜지면 결정이 WORM에 남지 않았는데도 t5/t6를
    통과해 무기록 승인(`RiskPhaseOutcome`)을 돌려줄 수 있다 — R-25 "거부·허용
    모두 WORM" 불변식 위반이다. `record()`는 insert 다음에 audit_log를
    쓰므로(모듈 docstring 순서 보장), insert 실패 시 audit_log에도 남지
    않아야 한다."""
    scenario = await new_scenario(pool)
    failing_recorder = RiskDecisionRecorder(pool, _FailingDecisionRepo(pool), NoopEventBus())

    with pytest.raises(RuntimeError, match="simulated WORM write failure"):
        await scenario.run_once(pool, failing_recorder)

    async with pool.acquire() as conn:
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM audit_log "
            "WHERE user_id = $1 AND action_type = 'risk_decision_recorded'",
            scenario.user_id,
        )
    assert audit_count == 0, (
        "WORM insert가 실패했는데도 audit_log가 기록됐다 — record()의 순서 보장이 깨졌다."
    )


async def test_concurrent_pre_trade_phases_across_tenants_do_not_cross_contaminate(pool):
    """다중 인스턴스 동시성 증명(D3, task-2835) — 서로 다른 tenant·
    execution_id의 사전검사가 동시에 실행돼도(예: 여러 execution 루프
    인스턴스) 각자의 WORM 기록(`risk_decision`)이 자신의 tenant_id로만
    귀속돼야 한다. 한 인스턴스의 결정이 다른 인스턴스의 감사 추적으로
    새면 §3.8 감사 추적 자체가 오염된다."""
    scenarios = [await new_scenario(pool) for _ in range(5)]
    recorder = RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), NoopEventBus())

    async def _run(scenario):
        outcome = await scenario.run_once(pool, recorder)
        assert outcome is not None and outcome.decision.outcome == RiskOutcome.ALLOW
        return scenario.user_id, outcome.decision.decision_id

    pairs = await asyncio.gather(*[_run(scenario) for scenario in scenarios])

    decision_ids = [decision_id for _, decision_id in pairs]
    assert len(set(decision_ids)) == len(decision_ids), "동시 실행 간 decision_id가 충돌했다"

    async with pool.acquire() as conn:
        for user_id, decision_id in pairs:
            row = await conn.fetchrow(
                "SELECT tenant_id FROM risk_decision WHERE decision_id = $1", decision_id
            )
            assert row is not None and row["tenant_id"] == user_id, (
                "동시 실행 중 WORM 기록의 tenant_id가 실행 주체와 어긋났다 — 컨텍스트 오염 의심."
            )
