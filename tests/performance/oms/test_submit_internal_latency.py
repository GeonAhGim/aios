"""L4-28 §7.1 "제출 내부 경로 p99(gate→멱등 선점→INSERT→VALIDATED→outbox
commit) ≤ 50 ms" — 환경 정규화 임계(print, 비차단) + DB 왕복 수 절대 단언
(CI 게이트).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1(측정 지점
"submit_order 진입/커밋"), §9 L4-28. 대상은 `submit_order()`(L4-09)
단독 호출 — 실제 거래소 호출은 이 함수가 하지 않는다(outbox 디스패처가
별도 소비, `test_outbox_dispatch_latency.py`가 그쪽을 다룬다).

**CI 게이트 = 순차 DB 왕복 수 정확 단언, 절대시간은 print(비차단)** —
선례(task-1038 `3ea1fc1` ledger append, task-1521 `R-57` pre_trade_risk_phase)
와 동일 decision: 공유 CI의 절대 지연은 이 파일이 통제할 수 없는 CPU/DB
편차 신호라 게이트로 쓰지 않는다. 이 환경의 기준 DB 왕복비용 대비 정규화한
목표는 print로만 남기고, `submit_order()` 1회(NEW claim, gate ALLOW)가 쓰는
왕복 수만 정확히(==) 단언한다.

왕복 수 구성(실측, task-2323 작업 중 `_discover_round_trips.py` 임시
스크립트로 확인 — 커밋 대상 아님, 최종 수치만 이 표에 남는다):
  `verify_entity_context`(task-1925, FA-5) — `entity_repo`가 매 조회마다
    자체 `pool.acquire()/release()`를 쓴다(`PostgresEntityRepository`
    설계, 이 파일 소유 아님) — get_legal_entity/get_fund/get_portfolio/
    get_sub_account 각 1 쿼리 + release마다 asyncpg 세션 리셋
    (`pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;`) 1회
    = 4 * 2 = 8
  메인 tx(단일 커넥션, BEGIN~COMMIT + 리셋 1) — BEGIN 1 + `orders` INSERT 1
    + `idempotency.claim` INSERT 1 + `orders.transition`(get_for_update 1 +
    set_config 1 + `order_events` INSERT 1 + conditional UPDATE 1 +
    `audit_bridge.emit`[`audit_log` INSERT 1 + advisory_xact_lock 1 +
    prev_row SELECT 1 + `foundation_audit_event` INSERT 1] = 8) +
    `outbox.enqueue` INSERT 1 + COMMIT 1 + 리셋 1 = 14
  합계 = 8 + 14 = 22

negative test(I-10 — 게이트가 "있다"가 아니라 "작동함"): `entity_repo`가
왕복을 하나 더 내면 계수가 예산과 정확히 1 어긋난다(다른 내부 리포지토리는
`submit_order.py` 모듈 전역 싱글톤이라 이 시그니처로는 교체 불가 — `entity_
repo`만 유일한 주입 지점).
"""
from __future__ import annotations

import statistics
import time
from uuid import UUID

import asyncpg
import pytest

from src.foundation.entities.adapters.postgres_repository import (
    PostgresEntityRepository,
    _row_to_legal_entity,
)
from src.services.oms.application.submit_order import submit_order
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context
from tests.performance.oms._fixtures import (
    create_running_execution,
    submit_command,
    submit_profile,
    submit_registry,
)
from tests.performance.oms.conftest import (
    attach_round_trip_logger,
    measure_baseline_round_trip_p95_ms,
)
from tests.support.oms_outbox_fakes import allow_gate

_SAMPLE_COUNT = 100
_P99_TARGET_MS = 50.0  # §7.1 운영 목표 — 비차단(print), task-1038/1521 decision
_ROUND_TRIP_MULTIPLIER = 9  # 실측 왕복수(22) 규모에 맞춘 배수 — ledger 선례와 동일 관례
_SUBMIT_ROUND_TRIPS = 22  # 모듈 docstring 구성표 — 정확 단언(==)


class _ChattyEntityRepo(PostgresEntityRepository):
    """negative 전용(I-10) — 조회를 별도 `pool.acquire()`로 감싸는 대신 같은
    커넥션 안에서 왕복을 하나 더 낸다(별도 acquire/release를 쓰면 asyncpg
    세션 리셋 쿼리까지 딸려 와 +2가 되어 "정확히 1 어긋남" 단언이 깨진다)."""

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID):  # type: ignore[override]
        async with self._pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            row = await conn.fetchrow(
                "SELECT * FROM legal_entity WHERE tenant_id = $1 AND entity_id = $2",
                tenant_id,
                entity_id,
            )
        return _row_to_legal_entity(row) if row is not None else None


async def _count_submit_round_trips(
    pool: asyncpg.Pool,
    *,
    entity_repo_cls: type[PostgresEntityRepository] = PostgresEntityRepository,
) -> int:
    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = entity_repo_cls(pool)
    queries = await attach_round_trip_logger(pool)

    await submit_order(  # 워밍업 — 커넥션의 1회성 드라이버 오버헤드 흡수
        submit_command(user_id, execution_id, 1), pool=pool, profile=submit_profile(),
        registry=submit_registry(), pre_submit_gate=allow_gate, entity_context=entity_context,
        entity_repo=entity_repo,
    )
    queries.clear()
    await submit_order(
        submit_command(user_id, execution_id, 2), pool=pool, profile=submit_profile(),
        registry=submit_registry(), pre_submit_gate=allow_gate, entity_context=entity_context,
        entity_repo=entity_repo,
    )
    return len(queries)


@pytest.mark.perf
async def test_submit_internal_p99_measured_and_round_trips_exact(pool: asyncpg.Pool) -> None:
    user_id = await create_test_tenant(pool)
    execution_id = await create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    entity_repo = PostgresEntityRepository(pool)
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)

    latencies_ms: list[float] = []
    for seq in range(_SAMPLE_COUNT):
        started = time.perf_counter()
        result = await submit_order(
            submit_command(user_id, execution_id, seq + 100), pool=pool, profile=submit_profile(),
            registry=submit_registry(), pre_submit_gate=allow_gate, entity_context=entity_context,
            entity_repo=entity_repo,
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        assert result.status.value == "VALIDATED"

    normalized_target_ms = max(_P99_TARGET_MS, _ROUND_TRIP_MULTIPLIER * baseline_p95_ms)
    round_trips = await _count_submit_round_trips(pool)

    latencies_ms.sort()
    print(
        f"\nsubmit_order internal latency (n={_SAMPLE_COUNT}): "
        f"p50={statistics.median(latencies_ms):.2f}ms "
        f"p95={latencies_ms[int(len(latencies_ms) * 0.95)]:.2f}ms "
        f"p99={latencies_ms[int(len(latencies_ms) * 0.99)]:.2f}ms "
        f"max={latencies_ms[-1]:.2f}ms "
        f"(target p99<{_P99_TARGET_MS}ms, 정규화 목표={normalized_target_ms:.2f}ms, "
        f"비차단 — task-1038/1521 decision); "
        f"sequential DB round trips={round_trips} (budget=={_SUBMIT_ROUND_TRIPS})"
    )
    assert round_trips == _SUBMIT_ROUND_TRIPS, (
        f"submit_order 순차 DB 왕복 수({round_trips})가 예산({_SUBMIT_ROUND_TRIPS})과 "
        "다릅니다 — 내부 경로 구조 변경입니다(모듈 docstring 구성표를 갱신하고 리뷰를 받으세요)."
    )
    # 절대시간(p99 ≤ 50ms)은 게이트로 쓰지 않는다(모듈 docstring, esc-826/task-1038 decision).


async def test_submit_round_trip_gate_detects_extra_query(pool: asyncpg.Pool) -> None:
    """negative(I-10): entity_repo가 왕복을 하나 더 내면 계수가 예산과 1 어긋난다."""
    round_trips = await _count_submit_round_trips(pool, entity_repo_cls=_ChattyEntityRepo)
    assert round_trips == _SUBMIT_ROUND_TRIPS + 1
    assert round_trips != _SUBMIT_ROUND_TRIPS
