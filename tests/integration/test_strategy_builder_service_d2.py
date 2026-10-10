"""D2 evidence — performance assertion + red-gate reproduction
for StrategyBuilderService (task-3351, L4-DC-2).

Per ADR-2026-09-09-C Decision 1, D2 requires:
  - negative tests >= 3  (already in test_strategy_builder_service.py)
  - failure-injection test >= 1  (test_concurrent_transitions_only_one_succeeds)
  - performance assertion >= 1  (this file)
  - red-gate reproduction >= 1  (this file)
"""

import asyncio
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.strategy_builder_service import StrategyBuilderService
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return StrategyBuilderService(pool)


# ---------------------------------------------------------------------------
# D2 성능 단언 (task-3351)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_save_strategy_p95_latency_within_normalized_budget(service, pool, perf_budget):
    """성능 단언 — ADR-2026-09-09-C Decision 1 예산표에는 전략 저장 전용
    항목이 없다(가장 가까운 DSL 컴파일 300ms는 별도 컴파일 단계라 여기엔
    안 맞는다). save_strategy()는 105번 표준(존재 여부 SELECT + 조건부
    INSERT)을 따르는 순차 DB 왕복 2회짜리라, tests/integration/foundation/
    ledger/test_perf_journal.py(LC-17)와 같은 근거로 이 환경의 기준 DB
    왕복비용(rt) 대비 정규화한 p95를 잰다 — 절대 ms를 고정하면 CI 인프라
    변동성에 흔들린다(같은 파일의 task-1029 전례).

    raw time.perf_counter() -> perf_budget.samples_async() 전환(task-11628) —
    비동기 I/O라 wall_ms 기준(coverage tracer는 일시정지됨)."""
    owner = await create_test_user(pool)

    async def _baseline_once() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    async def _save_once() -> None:
        await service.save_strategy(
            owner,
            f"perf-strategy-{uuid4().hex[:8]}",
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={"states": ["IDLE"]},
        )

    baseline_samples = await perf_budget.samples_async(_baseline_once, n=35)
    baseline_wall_ms = sorted(s.wall_ms for s in baseline_samples)[30]

    save_samples = await perf_budget.samples_async(_save_once, n=30)
    save_p95_ms = sorted(s.wall_ms for s in save_samples)[28]

    # p95는 기준 DB 왕복 p95의 10배 미만이어야 함
    assert save_p95_ms < baseline_wall_ms * 10, (
        f"p95={save_p95_ms:.1f}ms가 기준 p95({baseline_wall_ms:.1f}ms)*10을 초과"
    )


# ---------------------------------------------------------------------------
# D2 적색 게이트 재현 (105번 표준 위반 — 조건절 없는 UPDATE)
# ---------------------------------------------------------------------------


async def test_red_gate_update_without_conditional_clause(pool):
    """적색 재현 — 105번 표준을 위반해 WHERE 조건 없이 lifecycle_status를
    UPDATE하면 두 병렬 시도가 모두 성공해야 한다(실제로는 race condition).
    이 테스트가 통과하면 게이트가 적색으로 전환됨."""
    owner = await create_test_user(pool)
    strategy_id = f"redgate-strategy-{uuid4().hex[:8]}"
    await pool.execute(
        "INSERT INTO strategies "
        "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
        " fsm_definition, author_agent, lifecycle_status) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, 'GENERATED')",
        strategy_id,
        "1.0.0",
        owner,
        "BTC/USDT",
        "crypto",
        "bitget",
        "{}",
        "user",
    )

    arrived = 0
    released = asyncio.Event()

    async def _naive_transition_without_guard() -> None:
        nonlocal arrived
        async with pool.acquire() as conn:
            await conn.fetchval(
                "SELECT lifecycle_status FROM strategies WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                "1.0.0",
            )
            arrived += 1
            if arrived >= 2:
                released.set()
            else:
                await released.wait()
            # 105번 표준 위반 재현: 방금 읽은 상태를 WHERE 조건으로 다시
            # 걸지 않는다.
            await conn.execute(
                "UPDATE strategies SET lifecycle_status = $3 "
                "WHERE strategy_id = $1 AND version = $2",
                strategy_id,
                "1.0.0",
                "BACKTESTING",
            )

    results = await asyncio.gather(
        _naive_transition_without_guard(),
        _naive_transition_without_guard(),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    assert len(successes) == 2, (
        "적색 재현 실패 — 조건절 없는 UPDATE라면 두 시도 모두 예외 없이 "
        f"성공해야 합니다(실제: {results})"
    )
