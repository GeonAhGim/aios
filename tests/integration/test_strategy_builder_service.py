"""14.3 통합테스트 — 실제 dev DB 대상."""

import asyncio
import time
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.strategy_builder_service import (
    LIFECYCLE_ORDER,
    StrategyBuilderService,
    StrategyLifecycleError,
    assert_executable,
)
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


async def test_list_strategies_returns_only_owned_strategies(service, pool):
    owner = await create_test_user(pool)
    other = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    other_strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={"states": ["IDLE"]},
    )
    await service.save_strategy(
        other,
        other_strategy_id,
        "1.0.0",
        target_asset="ETH/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={"states": ["IDLE"]},
    )

    summaries = await service.list_strategies(owner)

    assert any(s.strategy_id == strategy_id for s in summaries)
    assert all(s.strategy_id != other_strategy_id for s in summaries)


async def test_list_strategies_empty_for_new_user(service, pool):
    owner = await create_test_user(pool)

    summaries = await service.list_strategies(owner)

    assert summaries == []


async def test_save_strategy_starts_at_generated(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"

    saved = await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={"states": ["IDLE"]},
    )

    assert saved.lifecycle_status == "GENERATED"


async def test_save_strategy_rejects_duplicate_id_version(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    with pytest.raises(StrategyLifecycleError):
        await service.save_strategy(
            owner,
            strategy_id,
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={},
        )


async def test_transition_to_next_stage_succeeds(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    result = await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    assert result.lifecycle_status == "BACKTESTING"


async def test_concurrent_transitions_only_one_succeeds(service, pool, monkeypatch):
    """레드팀 감사 #17 — transition_lifecycle()이 방금 읽은 lifecycle_status를
    UPDATE 조건으로 다시 걸지 않으면, 거의 동시에 들어온 두 전이 요청이
    서로의 아직 커밋 안 된 변경을 못 본 채 둘 다 통과해버릴 수 있다
    (04/05/08/09/16번과 같은 "읽고 나서 별도로 조건 없이 쓰기" 근본원인).
    같은 GENERATED 상태에서 동시에 BACKTESTING 전이를 두 번 시도하면
    정확히 하나만 성공해야 한다.

    asyncio.gather만으로는 두 transition_lifecycle() 호출의 사전조회가
    실제로 동시에 겹친다는 보장이 없다 — #04/#05와 같은 원칙으로 barrier를
    걸어 원래 레이스 조건을 결정적으로 재현한다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    arrived = 0
    released = asyncio.Event()
    original_fetchrow = asyncpg.pool.PoolConnectionProxy.fetchrow

    async def _synced_fetchrow(self, query, *args, **kwargs):
        nonlocal arrived
        result = await original_fetchrow(self, query, *args, **kwargs)
        if "SELECT s.lifecycle_status" in query:
            arrived += 1
            if arrived >= 2:
                released.set()
            else:
                await released.wait()
        return result

    monkeypatch.setattr(asyncpg.pool.PoolConnectionProxy, "fetchrow", _synced_fetchrow)

    results = await asyncio.gather(
        service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING"),
        service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, StrategyLifecycleError)]
    assert len(successes) == 1
    assert len(failures) == 1


async def test_cannot_skip_lifecycle_stages(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    with pytest.raises(StrategyLifecycleError):
        await service.transition_lifecycle(strategy_id, "1.0.0", "RISK_REVIEW")


async def test_full_lifecycle_walk_reaches_approved(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )

    result = None
    for stage in LIFECYCLE_ORDER[1:7]:  # BACKTESTING ... APPROVED
        result = await service.transition_lifecycle(strategy_id, "1.0.0", stage)

    assert result.lifecycle_status == "APPROVED"


async def test_can_fail_from_any_non_terminal_stage(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )
    await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")

    result = await service.transition_lifecycle(strategy_id, "1.0.0", "FAILED")

    assert result.lifecycle_status == "FAILED"


async def test_cannot_transition_out_of_terminal_state(service, pool):
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    await service.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )
    await service.transition_lifecycle(strategy_id, "1.0.0", "FAILED")

    with pytest.raises(StrategyLifecycleError):
        await service.transition_lifecycle(strategy_id, "1.0.0", "BACKTESTING")


def test_assert_executable_blocks_freshly_generated_strategy():
    with pytest.raises(StrategyLifecycleError):
        assert_executable("GENERATED")


def test_assert_executable_allows_approved_strategy():
    assert_executable("APPROVED")  # 예외가 발생하지 않으면 성공


# --- D2 증빙 보강(task-3351) — 성능 단언 + 게이트 적색 재현. 위 negative
# 테스트 4건(중복 id/version, 단계 건너뛰기 금지, 종단 상태 이탈 금지,
# assert_executable 차단)과 실패 주입 1건(test_concurrent_transitions_
# only_one_succeeds)은 이미 있었다 — ADR-2026-09-09-C Decision 1 D2
# 하한(negative>=3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1)에서
# 남은 두 항목만 채운다. ---


@pytest.mark.perf
async def test_save_strategy_p95_latency_within_normalized_budget(service, pool):
    """성능 단언 — ADR-2026-09-09-C Decision 1 예산표에는 전략 저장 전용
    항목이 없다(가장 가까운 DSL 컴파일 300ms는 별도 컴파일 단계라 여기엔
    안 맞는다). save_strategy()는 105번 표준(존재 여부 SELECT + 조건부
    INSERT)을 따르는 순차 DB 왕복 2회짜리라, tests/integration/foundation/
    ledger/test_perf_journal.py(LC-17)와 같은 근거로 이 환경의 기준 DB
    왕복비용(rt) 대비 정규화한 p95를 잰다 — 절대 ms를 고정하면 CI 인프라
    변동성에 흔들린다(같은 파일의 task-1029 전례)."""
    baseline_samples_ms: list[float] = []
    for _ in range(5 + 30):
        started = time.perf_counter()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        baseline_samples_ms.append((time.perf_counter() - started) * 1000)
    baseline_samples_ms = baseline_samples_ms[5:]
    baseline_samples_ms.sort()
    baseline_p95_ms = baseline_samples_ms[int(len(baseline_samples_ms) * 0.95)]

    sample_count = 30
    owner = await create_test_user(pool)
    latencies_ms: list[float] = []
    for _ in range(sample_count):
        strategy_id = f"perf-strategy-{uuid4().hex[:8]}"
        started = time.perf_counter()
        await service.save_strategy(
            owner,
            strategy_id,
            "1.0.0",
            target_asset="BTC/USDT",
            market="crypto",
            exchange="bitget",
            fsm_definition={"states": ["IDLE"]},
        )
        latencies_ms.append((time.perf_counter() - started) * 1000)

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    normalized_target_ms = max(30.0, 6 * baseline_p95_ms)

    print(
        f"\nstrategy_builder save_strategy latency: p95={p95_ms:.3f}ms (n={sample_count}); "
        f"baseline round-trip p95={baseline_p95_ms:.3f}ms (n=30); "
        f"normalized target={normalized_target_ms:.3f}ms (max(30, 6*rt))"
    )
    assert p95_ms < normalized_target_ms


async def test_gate_red_without_conditional_write_guard_double_succeeds(pool):
    """게이트 적색 재현 — transition_lifecycle()의 UPDATE는 방금 읽은
    lifecycle_status를 WHERE 절 조건으로 다시 거는 105번 표준(조건부
    UPDATE)을 따른다(레드팀 감사 #17, src의 주석 참고). 위
    test_concurrent_transitions_only_one_succeeds는 그 결과(정확히 1건만
    성공)를 녹색으로 증명한다. 이 테스트는 그 조건절만 뺀 "읽고 나서
    조건 없이 쓰기" 버전을 이 파일 안에서만 재현해, 조건절이 없으면
    동시에 들어온 두 전이 요청이 서로의 커밋을 못 본 채 둘 다 성공으로
    보고돼버림(잃어버린 갱신)을 보여준다 — 실제 서비스 코드는 이 적색을
    막는다."""
    owner = await create_test_user(pool)
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
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
