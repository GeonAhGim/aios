"""L4-28 §7.1 "inbox 처리 지연 p99(received_at→전이 commit) ≤ 300 ms" —
환경 정규화 임계(print, 비차단) + DB 왕복 수 절대 단언(CI 게이트).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §7.1(측정 지점
"inbox"), §9 L4-28. 대상은 `InboxProcessor.ingest()`(L4-15) 단독 호출.

**부분체결만 쓰는 이유** — §7.1의 측정 지점은 "전이 commit"까지다.
`ingest()`가 새로 `FILLED`를 확정하면 커밋 *이후* 별도 커넥션으로
`position_ledger.record_fill_in_position_ledger`를 1회 더 부르는데
(`inbox_processor.py` 모듈 docstring 참조 — 원장 반영은 이 리프가 아니라
L4-15가 재사용하는 별도 모듈 소유), 이 호출까지 포함하면 이 파일이 소유하지
않는 코드 경로의 왕복까지 회귀 가드에 걸린다. **부분체결**(주문 수량보다
적게 채움)은 `next_status()`가 `PARTIALLY_FILLED`를 돌려줘
`_process_row`가 `position_ledger` 분기를 타지 않으므로(§4.2 "체결이 새로
FILLED를 만들면"만 그 분기를 탄다), "received_at → 전이 commit" 구간을
정확히 이 함수 하나로 격리해 잰다.

`ingest()` 1회(부분체결, 신규 이벤트)의 왕복 수 구성(실측, task-2323
`_discover_round_trips.py`로 확인):
  BEGIN 1 + `provider_event_inbox` INSERT(`insert_if_absent`) 1 + 방금 넣은
  행 id SELECT 1 + `_resolve_order_id` SELECT 1 + `get_for_update`(venue
  확인) 1 + `fills` INSERT(`insert_if_absent`) 1 + orders.filled_quantity
  갱신 트리거의 잠금 SELECT 1 + `orders` UPDATE(filled_quantity 트리거) 1 +
  재조회 `get_for_update` 1 + `fills.list_for_order` SELECT 1 +
  `orders.transition`(get_for_update 1 + set_config 1 + order_events
  INSERT 1 + conditional UPDATE 1 + audit_bridge.emit[4]) 8 +
  `mark_processed` conditional UPDATE 1 + COMMIT 1 + 세션 리셋 1 = 21

negative test(I-10): `fills_repo.insert_if_absent`(ingest 1회당 정확히 1번만
호출)가 왕복을 하나 더 내면 계수가 예산과 정확히 1 어긋난다(`InboxProcessor`는
`order_repo`/`fills_repo`/`inbox_repo`를 생성자 인자로 받으므로 `submit_order`와
달리 직접 교체 가능하다 — `get_for_update`처럼 한 호출 안에서 여러 번(venue
확인·재조회·transition 내부) 불리는 메서드를 훅하면 +1이 아니라 +N이 된다).

DEEPEN(task-2802) — DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md#2323)
근거 보강: (1) 정규화 목표(`normalized_target_ms`)를 이제 실제로 단언한다
(절대 목표 `_P99_TARGET_MS` 자체는 여전히 비차단 print). (2) `order_repo.
transition`이 tx 안에서(fills INSERT 이후, `mark_processed` 이전) 인프라
오류로 실패하면 `ingest()`의 `async with ... conn.transaction()`이 전체를
롤백한다 — `provider_event_inbox`/`fills` 어느 쪽에도 고아 행이 남지 않아,
같은 이벤트를 나중에 재전달해도(F9 중복 흡수 전제가 깨지지 않음) 다시
처리될 수 있다는 불변식의 증명이다.
"""
from __future__ import annotations

import statistics
import time
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from src.data.models.trading import OrderStatus
from src.services.oms.adapters.fills_repository import FillsRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.contracts.v1_events import FillEvent, OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from tests.integration.oms.conftest import create_test_user
from tests.performance.oms._fixtures import insert_open_order, partial_fill_event
from tests.performance.oms.conftest import (
    attach_round_trip_logger,
    measure_baseline_round_trip_p95_ms,
)

_SAMPLE_COUNT = 100
_P99_TARGET_MS = 300.0  # §7.1 운영 목표 — 비차단(print), task-1038/1521 decision
_ROUND_TRIP_MULTIPLIER = 9
_INGEST_PARTIAL_ROUND_TRIPS = 21  # 모듈 docstring 구성표 — 정확 단언(==)


class _ChattyFillsRepo(FillsRepository):
    """negative 전용(I-10) — fills 삽입 전에 불필요한 왕복을 하나 더 낸다."""

    async def insert_if_absent(self, conn: asyncpg.Connection, fill: FillEvent) -> bool:
        await conn.fetchval("SELECT 1")
        return await super().insert_if_absent(conn, fill)


class _FailingOrderRepo(PostgresOrderRepository):
    """failure-injection 전용(DEEPEN task-2802) — fills INSERT 이후, tx
    커밋 이전 단계에서 인프라 오류를 흉내 낸다(전체 tx 롤백 증명용)."""

    async def transition(
        self,
        conn: asyncpg.Connection,
        *,
        order_id: UUID,
        expected_status: OrderStatus,
        expected_version: int,
        new_status: OrderStatus,
        patch: dict[str, Any],
        event: OrderTransitionEvent,
    ) -> OrderView:
        raise ConnectionError("simulated DB connection loss before order transition commit")


async def _count_ingest_round_trips(
    pool: asyncpg.Pool, *, fills_repo_cls: type[FillsRepository] = FillsRepository
) -> int:
    user_id = await create_test_user(pool)
    processor = InboxProcessor(pool, fills_repo=fills_repo_cls())
    queries = await attach_round_trip_logger(pool)

    _, cid, exoid = await insert_open_order(pool, user_id)  # 워밍업
    await processor.ingest(partial_fill_event(exchange_order_id=exoid, client_order_id=cid))

    _, cid, exoid = await insert_open_order(pool, user_id)
    queries.clear()
    ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
    result = await processor.ingest(ev)
    assert result is True
    return len(queries)


@pytest.mark.perf
async def test_inbox_ingest_p99_measured_and_round_trips_exact(pool: asyncpg.Pool) -> None:
    user_id = await create_test_user(pool)
    processor = InboxProcessor(pool)
    baseline_p95_ms = await measure_baseline_round_trip_p95_ms(pool)

    latencies_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        _, cid, exoid = await insert_open_order(pool, user_id)
        ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
        started = time.perf_counter()
        result = await processor.ingest(ev)
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        assert result is True

    normalized_target_ms = max(_P99_TARGET_MS, _ROUND_TRIP_MULTIPLIER * baseline_p95_ms)
    round_trips = await _count_ingest_round_trips(pool)

    latencies_ms.sort()
    print(
        f"\ninbox ingest(partial fill) latency (n={_SAMPLE_COUNT}): "
        f"p50={statistics.median(latencies_ms):.2f}ms "
        f"p95={latencies_ms[int(len(latencies_ms) * 0.95)]:.2f}ms "
        f"p99={latencies_ms[int(len(latencies_ms) * 0.99)]:.2f}ms "
        f"max={latencies_ms[-1]:.2f}ms "
        f"(target p99<{_P99_TARGET_MS}ms, 정규화 목표={normalized_target_ms:.2f}ms, "
        f"비차단 — task-1038/1521 decision); "
        f"sequential DB round trips={round_trips} (budget=={_INGEST_PARTIAL_ROUND_TRIPS})"
    )
    assert round_trips == _INGEST_PARTIAL_ROUND_TRIPS, (
        f"inbox ingest 순차 DB 왕복 수({round_trips})가 예산"
        f"({_INGEST_PARTIAL_ROUND_TRIPS})과 다릅니다 — 구조 변경입니다"
        "(모듈 docstring 구성표를 갱신하고 리뷰를 받으세요)."
    )
    # 절대시간(p99 ≤ 300ms)은 여전히 게이트가 아니다(모듈 docstring, esc-826/task-1038
    # decision) — p99/max는 n=100 표본에서 단일 꼬리 샘플이라 그 자체가 노이즈에
    # 취약해(GC/스케줄링 지터 1회로도 튐) 게이트로 쓰지 않는다. 대신 이 환경의 기준
    # 왕복비용에 정규화한 목표(DEEPEN task-2802)를 더 안정적인 p95에 건다.
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    assert p95_ms < normalized_target_ms, (
        f"inbox ingest p95({p95_ms:.2f}ms)가 정규화 목표({normalized_target_ms:.2f}ms = "
        f"max({_P99_TARGET_MS}, {_ROUND_TRIP_MULTIPLIER}x 기준왕복 {baseline_p95_ms:.2f}ms))를 "
        "넘었습니다 — 이 환경의 DB 왕복비용 대비 상대적인 성능 회귀입니다."
    )


async def test_inbox_round_trip_gate_detects_extra_query(pool: asyncpg.Pool) -> None:
    """negative(I-10): fills_repo가 왕복을 하나 더 내면 계수가 예산과 1 어긋난다."""
    round_trips = await _count_ingest_round_trips(pool, fills_repo_cls=_ChattyFillsRepo)
    assert round_trips == _INGEST_PARTIAL_ROUND_TRIPS + 1
    assert round_trips != _INGEST_PARTIAL_ROUND_TRIPS


async def test_ingest_rolls_back_fully_on_order_repo_failure(pool: asyncpg.Pool) -> None:
    """failure-injection(DEEPEN task-2802): `order_repo.transition`이 tx
    안에서 인프라 오류로 실패하면 `ingest()`는 그 오류를 그대로 전파하고,
    같은 tx에서 앞서 쓴 `provider_event_inbox`/`fills` 행도 함께 롤백된다 —
    이벤트가 "반쯤 처리된" 상태로 남지 않아 재전달 시 다시 처리될 수 있다."""
    user_id = await create_test_user(pool)
    _, cid, exoid = await insert_open_order(pool, user_id)
    ev = partial_fill_event(exchange_order_id=exoid, client_order_id=cid)
    processor = InboxProcessor(pool, order_repo=_FailingOrderRepo())

    with pytest.raises(ConnectionError):
        await processor.ingest(ev)

    async with pool.acquire() as conn:
        inbox_row = await conn.fetchrow(
            "SELECT 1 FROM provider_event_inbox WHERE venue = $1 AND provider_event_id = $2",
            ev.venue, ev.provider_event_id,
        )
        fill_row = await conn.fetchrow(
            "SELECT 1 FROM fills WHERE venue = $1 AND provider_fill_id = $2",
            ev.venue, ev.last_fill.provider_fill_id,
        )
        order_status = await conn.fetchval(
            "SELECT status FROM orders WHERE exchange_order_id = $1", exoid
        )
    assert inbox_row is None, "실패한 ingest가 provider_event_inbox에 고아 행을 남겼습니다."
    assert fill_row is None, "실패한 ingest가 fills에 고아 행을 남겼습니다."
    assert order_status == "SUBMITTED"
