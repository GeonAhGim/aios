"""FA-16 — adversarial: OMS submit/cancel/modify never change `orders`
without a matching `order_events` row, in the same transaction.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-16
(선행 FA-14=task-2050, FA-15=task-2060). FA-14가 정한 이벤트 원천을 그대로
쓴다 — 새 이벤트 테이블은 없다: OMS의 이벤트 원천은 `order_events`이고,
`order_repository.transition()`이 세 경로(submit/cancel/modify) 모두가
공유하는 유일한 전이 지점이다(I-10 모듈 docstring). "이벤트 append가
상태 변경보다 먼저, 같은 `conn`, 같은 트랜잭션"은 이미 구현돼 있다 —
`073beca589d5`의 `oms_enforce_order_transition_trg`가 cutover 무장 후
DB 레벨에서 이 순서를 강제한다(`tests/integration/oms/
test_db_transition_trigger.py::test_status_change_without_order_event_
raises_once_cutover_armed`가 그 트리거 자체를 이미 검증). 이 파일은
cutover 여부와 무관하게 실제 애플리케이션 경로(submit_order/cancel_order/
modify_order)를 호출해 그 배선이 살아 있음을 증명한다.

DoD(1) "배선증명 없이 통과하는 테스트는 반려" — `test_oms_submit_cancel_
modify_each_produce_exactly_one_order_event`는 `order_events` 행 수를
`orders.version`과 직접 비교하므로, `order_repository.transition()`의
`await self._events.append(conn, event)` 줄을 지우면 다음 줄
(`event.model_copy(update={"seq": seq})`)이 `NameError`로 즉시 죽어
이 테스트가 FAIL한다 — 실제로 그 줄을 임시로 주석 처리하고 이 테스트가
FAIL하는 것을 확인한 뒤 원복했다(회귀 방지를 위해 sabotage 코드는
커밋에 남기지 않는다).

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md #task-2061) 보강분(이 파일
하단): 위 "줄 지우고 확인 후 원복" 절차는 커밋 메시지에만 서술돼 있고
리포에 자동 재현 가능한 형태로 남지 않았다 — 그리고 이 파일에는 성능
단언이 전혀 없었다. `test_oms_bypassed_event_append_return_value_is_not_
caught_by_flag_or_python`이 소스를 건드리지 않고 그 우회를 이중체로
영구 재현하고(그 과정에서 I6 트리거가 `oms.event_written` 세션 플래그만
확인하고 실제 `order_events` 행 존재는 확인하지 않는다는 것도 드러난다 —
플래그는 append 호출 *전에* 이미 세팅됨), `test_oms_transition_round_
trip_count_stays_bounded`가 FA-16 세 경로의 공유 전이 지점(`order_
repository.transition()`) 왕복 수 회귀 가드를 더한다(절대 ms 대신 왕복
수 — task-920/1029 전례와 동일 이유).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.application import cancel_order as cancel_order_module
from src.services.oms.application.cancel_order import cancel_order
from src.services.oms.application.modify_order import modify_order
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import (
    CancelOrderCommand,
    ModifyOrderCommand,
    OrderIdempotencyScope,
    SubmitOrderCommand,
)
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.conftest import create_test_tenant
from tests.integration.oms.conftest import insert_order, seed_entity_context

_MAX_TRANSITION_ROUND_TRIPS = 10

_MAX_TRANSITION_ROUND_TRIPS = 10


def _profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {},
        "qty_lot": {},
        "min_notional": {},
        "rate_limits": {},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "DOC_ONLY",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile.model_validate(defaults)


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT",
        "bitget",
        "BTCUSDT",
        tick=Decimal("0.1"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quote_ccy="USDT",
    )
    return reg


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
    )


async def _create_running_execution(pool: asyncpg.Pool, user_id: UUID) -> int:
    strategy_id = f"fa16-adv-{uuid4().hex[:8]}"
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
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


async def _submit_command(pool: asyncpg.Pool, user_id: UUID) -> SubmitOrderCommand:
    execution_id = await _create_running_execution(pool, user_id)
    scope = OrderIdempotencyScope(
        tenant_id=user_id,
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        scope=scope,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _order_state(pool: asyncpg.Pool, order_id: UUID) -> tuple[str, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, version FROM orders WHERE order_id = $1", order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
    if row is None:
        return "MISSING", 0
    assert event_count == row["version"], (
        f"order {order_id}: version={row['version']} but order_events count={event_count} "
        "— state changed without a matching event (FA-16 I-10 violation)."
    )
    return row["status"], row["version"]


async def test_oms_submit_cancel_modify_each_produce_exactly_one_order_event(pool):
    """Positive — DoD(1). §9 FA-16 대상 세 경로 각각 정확히 1개의
    `order_events` 행을 남기고, `orders.version`이 그 개수와 정확히
    일치한다(0건의 "이벤트 없는 상태 변경")."""
    user_id = await create_test_tenant(pool)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = await _submit_command(pool, user_id)
    submitted = await submit_order(
        cmd,
        pool=pool,
        profile=_profile(),
        registry=_registry(),
        pre_submit_gate=_allow_gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )
    status, version = await _order_state(pool, submitted.order_id)
    assert status == "VALIDATED" and version == 1

    async with pool.acquire() as conn:
        cancel_target = await insert_order(conn, user_id, status="ACKNOWLEDGED")
    cancelled = await cancel_order(
        CancelOrderCommand(
            command_id=uuid4(),
            trace_id=uuid4(),
            order_id=cancel_target,
            tenant_id=user_id,
            reason="fa16-adv-test",
            actor_subject_id=user_id,
            issued_at=datetime.now(timezone.utc),
        ),
        pool=pool,
    )
    _, version = await _order_state(pool, cancelled.order_id)
    assert version == 1

    async with pool.acquire() as conn:
        modify_target = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, price, status, filled_quantity
            ) VALUES ($1, $2, 'fa16-adv-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      'LIMIT', 1, 100, 'ACKNOWLEDGED', 0)
            RETURNING order_id
            """,
            user_id,
            f"fa16-adv-modify-{uuid4().hex}",
        )
    modified = await modify_order(
        ModifyOrderCommand(
            command_id=uuid4(),
            trace_id=uuid4(),
            order_id=modify_target,
            tenant_id=user_id,
            reason="fa16-adv-test",
            actor_subject_id=user_id,
            issued_at=datetime.now(timezone.utc),
            new_price=Decimal("101"),
        ),
        pool=pool,
        profile=_profile(),
    )
    _, version = await _order_state(pool, modified.order_id)
    assert version == 1


async def test_oms_event_append_failure_blocks_order_state_change(pool, monkeypatch):
    """Negative — DoD(3) fail-closed. `order_events` append 자체가 실패하면
    `orders` UPDATE는 이 함수까지 도달조차 못 한다(순서상 append가 먼저,
    §5.1) — tx 전체가 롤백돼 0행."""

    async def _boom(self, conn, ev):  # noqa: ANN001, ARG001 -- 테스트 전용 fault injection
        raise RuntimeError("injected order_events append failure")

    monkeypatch.setattr(PostgresOrderEventRepository, "append", _boom)

    user_id = await create_test_tenant(pool)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = await _submit_command(pool, user_id)

    with pytest.raises(RuntimeError, match="injected order_events append failure"):
        await submit_order(
            cmd,
            pool=pool,
            profile=_profile(),
            registry=_registry(),
            pre_submit_gate=_allow_gate,
            entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", user_id)
    assert order_count == 0


async def test_oms_transaction_rollback_removes_event_and_state_together(pool, monkeypatch):
    """DoD(2) — 실DB. `_orders.transition()`(order_events insert + orders
    UPDATE)이 성공한 *뒤* 같은 트랜잭션의 다른 단계(outbox enqueue)가
    실패하면, 커밋되지 않은 order_events 행도 orders 변경분과 함께 사라진다
    (같은 트랜잭션이라는 것의 실제 증거 — 부분 커밋 없음)."""

    async def _boom(conn, **kwargs):  # noqa: ANN001, ARG001 -- 인스턴스 속성 패치라 self 없음
        raise RuntimeError("injected outbox enqueue failure")

    monkeypatch.setattr(cancel_order_module._outbox, "enqueue", _boom)

    user_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="ACKNOWLEDGED")

    with pytest.raises(RuntimeError, match="injected outbox enqueue failure"):
        await cancel_order(
            CancelOrderCommand(
                command_id=uuid4(),
                trace_id=uuid4(),
                order_id=order_id,
                tenant_id=user_id,
                reason="fa16-adv-rollback",
                actor_subject_id=user_id,
                issued_at=datetime.now(timezone.utc),
            ),
            pool=pool,
        )

    status, version = await _order_state(pool, order_id)
    assert status == "ACKNOWLEDGED" and version == 0


def _transition_event(order_id: UUID, user_id: UUID) -> OrderTransitionEvent:
    return OrderTransitionEvent(
        order_id=order_id,
        from_status=OrderStatus.CREATED,
        to_status=OrderStatus.VALIDATED,
        event="VALIDATED",
        reason_code=None,
        actor_subject_id=user_id,
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=datetime.now(timezone.utc),
        payload_hash="a" * 64,
    )


async def test_oms_bypassed_event_append_return_value_is_not_caught_by_flag_or_python(
    pool, monkeypatch
):
    """우회재현(자동) — DEPTH task-2724 보강. 원 커밋(task-2061)은
    `order_repository.transition()`의 `seq = await self._events.append(conn,
    event)` 줄을 손으로 지우고 DoD(1) 양성 테스트가 FAIL하는 것을 확인한
    뒤 원복했다 — 그 확인은 커밋 메시지에만 남고 리포에 자동 재현 가능한
    형태로 보존되지 않았다.

    소스를 건드리지 않고 이중체로 그 우회를 영구 재현한다: `append()`가
    실제 INSERT 없이 그럴듯한 seq만 돌려주면, 두 안전장치 모두 이를 잡지
    못한다는 것이 드러난다 — (1) I6 트리거(`073beca589d5`)는 `oms.
    event_written` 세션 플래그만 확인하는데, `transition()`이 그 플래그를
    append 호출 *전에* 이미 세팅해 두므로(§5.1 줄 순서) append가 실제로
    쓰든 말든 트리거를 통과한다, (2) `transition()` 마지막 줄
    (`event.model_copy(update={"seq": seq})`)도 `seq`가 정의만 돼 있으면
    NameError가 나지 않는다 — "줄을 지우면 NameError"라는 원래 근거는
    append가 *예외를 던지는* 실패에만 유효하고, "조용히 아무 것도 안 쓰는"
    실패에는 안전망이 없다. 결과: `orders.status`가 VALIDATED로 바뀌지만
    `order_events`는 0행 — I-10이 금지하는 바로 그 상황이 걸리지 않고
    커밋된다."""

    async def _silently_skip_insert(self, conn, ev):  # noqa: ANN001, ARG001
        return 1

    monkeypatch.setattr(PostgresOrderEventRepository, "append", _silently_skip_insert)

    user_id = await create_test_tenant(pool)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = await _submit_command(pool, user_id)

    submitted = await submit_order(
        cmd,
        pool=pool,
        profile=_profile(),
        registry=_registry(),
        pre_submit_gate=_allow_gate,
        entity_context=entity_context,
        entity_repo=PostgresEntityRepository(pool),
    )

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM orders WHERE order_id = $1", submitted.order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", submitted.order_id
        )
    assert status == "VALIDATED", "우회 하에서도 상태 전이 자체는 커밋된다"
    assert event_count == 0, (
        "FA-16/I-10 위반 재현: orders.status가 바뀌었는데 order_events는 0행 — "
        "I6 트리거(플래그만 확인)도 Python 쪽(seq가 정의돼 NameError 없음)도 "
        "이 우회를 잡지 못한다는 자동·영구 증거."
    )


async def test_oms_transition_round_trip_count_stays_bounded(pool):
    """성능단언 — DEPTH task-2724 보강(이 파일에 성능 단언이 전혀 없었다).
    `order_repository.transition()`(FA-16 세 경로가 공유하는 유일한 전이
    지점) 1회의 순차 DB 왕복 수 회귀 가드. 절대 ms 대신 왕복 수를 재는
    이유는 이 저장소의 CI 절대지연 게이트 금지 전례(task-920/1029,
    `test_perf_journal.py`)와 동일하다. `submit_order()` 전체 경로의 왕복
    예산(22, `tests/perf/oms/test_perf_submit_internal.py`)은 엔터티 검증·
    멱등 선점·게이트·outbox까지 포함한 상위 합계라 `transition()` 자체가
    늘어나는 회귀를 가려낼 수 없다 — 이 테스트는 그 구성요소 하나만
    떼어 잰다."""
    repo = PostgresOrderRepository()
    user_id = await create_test_tenant(pool)

    async with pool.acquire() as conn:
        warmup_order = await insert_order(conn, user_id, status="CREATED")
        await repo.transition(
            conn,
            order_id=warmup_order,
            expected_status=OrderStatus.CREATED,
            expected_version=0,
            new_status=OrderStatus.VALIDATED,
            patch={},
            event=_transition_event(warmup_order, user_id),
        )

        measured_order = await insert_order(conn, user_id, status="CREATED")
        queries: list[str] = []

        def _log(record: object) -> None:
            queries.append(getattr(record, "query", ""))

        conn.add_query_logger(_log)
        try:
            await repo.transition(
                conn,
                order_id=measured_order,
                expected_status=OrderStatus.CREATED,
                expected_version=0,
                new_status=OrderStatus.VALIDATED,
                patch={},
                event=_transition_event(measured_order, user_id),
            )
        finally:
            conn.remove_query_logger(_log)

    print(
        f"\norder_repository.transition() round trips: {len(queries)} "
        f"(max={_MAX_TRANSITION_ROUND_TRIPS})"
    )
    assert len(queries) <= _MAX_TRANSITION_ROUND_TRIPS, (
        f"transition() 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_TRANSITION_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
