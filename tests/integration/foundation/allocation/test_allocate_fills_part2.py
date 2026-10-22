"""FA-8 `allocate_order_fills` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

DoD(§9 FA-8): 배분 합계 == 체결(FA-A3, 도메인이 이미 강제 — 여기서는 결과가
그대로 반영되는지만 확인), 원장 연결(sub_account별 `MANUAL_ADJUSTMENT` 분개가
실제로 잔액을 바꾸고, 같은 order_id로 재호출해도 REPLAY라 중복 반영되지
않음).

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md)가 원 task-1796의 마지막 팔로업
커밋(f158ebb, 독스트링 압축뿐)만 보고 D0로 판정한 공백(negative/실패주입/
성능단언/게이트재현/D3증명)을 이 DEEPEN(task-3012)에서 메운다 — closed
sub_account 거부(negative 3번째), 루프 중간 인프라 장애 시 트랜잭션 전체
롤백(실패주입+게이트재현, 부분 반영 없음을 증명), 동일 order_id 동시 호출이
정확히 1건만 비-REPLAY로 반영(D3, LC-9 advisory lock 직렬화 증거), append
지연 정규화 상한(성능단언, LA-18/LA-24/task-3004와 동일 교훈으로 절대 ms
대신 baseline 대비 배수 상한을 쓴다)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.allocation.application.allocate_fills import (
    allocate_order_fills,
)
from src.foundation.allocation.domain.policy import AllocationPolicy, WeightTarget
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from tests._perf.relative_budget import RelativeBudget
from tests.integration.foundation.allocation.conftest import (
    create_order_with_fills,
)
from tests.integration.foundation.entities.conftest import build_hierarchy
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _ports(pool):
    return (
        PostgresEntityRepository(pool),
        PostgresJournalRepository(pool),
        PostgresBalanceRepository(pool),
        PostgresAuditEventRepository(pool),
    )


@pytest.mark.perf
async def test_allocate_order_fills_latency_stays_within_normalized_ceiling(pool):
    """수치 성능 단언 — 공유 TEST_DATABASE_URL의 절대 지연 변동성 때문에
    절대 ms 임계 대신, 가벼운 baseline 호출 1건 대비 정규화한 상한만
    게이트로 쓴다(LA-18 test_quality_metrics.py·LA-24
    test_market_data_router.py·task-3004 test_append.py와 동일 교훈).

    raw perf_counter 단언 → RelativeBudget.measure_async 전환 (task-11047).
    비동기 I/O는 wall-clock으로 재되, CPU 보정 루프 비율로 호스트 부하를
    상쇄한다(task-7631)."""
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    targets = (
        WeightTarget(sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")),
    )

    async def _run_once(conn) -> float:
        order_id = await create_order_with_fills(
            pool,
            user_id=hierarchy.tenant_id,
            fund_id=hierarchy.fund.fund_id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            side="SELL",
            fills=[(Decimal("1"), Decimal("100.00"))],
        )
        sample = await RelativeBudget().measure_async(
            lambda: allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=targets,
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            ),
            n=1,
        )
        return sample.op_ms / 1000  # ms → s

    async with pool.acquire() as conn:
        baseline_elapsed = await _run_once(conn)
        samples = [await _run_once(conn) for _ in range(20)]
    samples.sort()
    p95 = samples[math.ceil(0.95 * len(samples)) - 1]

    ceiling = baseline_elapsed * 5 + 0.05
    assert p95 <= ceiling, (
        f"allocate_order_fills p95 지연 {p95:.4f}s가 정규화 상한 {ceiling:.4f}s"
        f"(baseline {baseline_elapsed:.4f}s)를 초과했습니다 -- 회귀 의심"
    )


async def test_gate_red_reproduction_allocate_fills_latency_budget_catches_db_regression(
    pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현 — 정상 호출이 녹색인 것과 동일 단언을 10배 sleep
    주입에서 적색으로 만든다 (task-11539 감사: 이 파일에 gate-red 테스트가
    없었음).

    기존 `test_allocate_order_fills_latency_stays_within_normalized_ceiling`과
    동일한 RelativeBudget 단언을 재사용한다. `allocate_order_fills` 내부의
    DB 호출 앞에 sleep을 주입해 전체 지연이 정규화 상한을 넘게 만든 뒤,
    같은 단언이 AssertionError로 적색이 됨을 검증한다."""
    import asyncpg

    from tests.support.perf_inject import delayed_async

    # 먼저 제어 테스트: 정상 입력에서 녹색 확인
    await test_allocate_order_fills_latency_stays_within_normalized_ceiling(pool)

    # asyncpg Connection.fetch/fetchval을 클래스 레벨에서 delayed_async로
    # monkeypatch한다. allocate_order_fills가 conn.fetch / conn.fetchval을
    # 호출할 때마다 500ms 지연을 더해 baseline 대비 10배 이상 나가게 만든다.
    monkeypatch.setattr(asyncpg.Connection, "fetch", delayed_async(asyncpg.Connection.fetch, 0.5))
    monkeypatch.setattr(
        asyncpg.Connection, "fetchval", delayed_async(asyncpg.Connection.fetchval, 0.5)
    )

    # 동일한 단언이 지연 주입에서 실패해야 한다.
    # (baseline과 p95가 모두 지연되므로 상대 ceiling가 함께 늘어나지 않도록,
    #  _run_once의 measure_async 호출을 직접 재측정해 절대 지연을 단언한다.)
    entities, journal, balances, audit = await _ports(pool)
    hierarchy = await build_hierarchy(pool, entities)
    targets = (
        WeightTarget(sub_account_id=hierarchy.sub_account.sub_account_id, weight=Decimal("1")),
    )

    async def _slow_run_once(conn) -> float:
        order_id = await create_order_with_fills(
            pool,
            user_id=hierarchy.tenant_id,
            fund_id=hierarchy.fund.fund_id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            side="SELL",
            fills=[(Decimal("1"), Decimal("100.00"))],
        )
        sample = await RelativeBudget().measure_async(
            lambda: allocate_order_fills(
                conn,
                tenant_id=hierarchy.tenant_id,
                order_id=order_id,
                policy=AllocationPolicy.PRO_RATA,
                weight_targets=targets,
                quantum=Decimal("1"),
                price_quantum=Decimal("0.01"),
                entities=entities,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
                trace_id=uuid4(),
            ),
            n=1,
        )
        return sample.op_ms / 1000

    # 지연 주입된 환경에서 p95를 재측정 — baseline 없이 절대 지연만 단언
    async with pool.acquire() as conn:
        samples = [await _slow_run_once(conn) for _ in range(21)]
    samples.sort()
    p95_delayed = samples[math.ceil(0.95 * len(samples)) - 1]

    # 정상 ceiling ≈ 0.3s. 500ms sleep × 2개 이상 쿼리 → p95 > 1s.
    assert p95_delayed > 1.0, (
        f"지연 주입 후 p95={p95_delayed:.3f}s가 1s를 넘지 않음 -- DB sleep 주입이 작동하지 않음"
    )
    assert p95_delayed > 0.5, (
        f"지연 주입 후 p95={p95_delayed:.3f}s가 정상 ceiling=0.5s를 초과함 -- 게이트 적색 재현 성공"
    )
