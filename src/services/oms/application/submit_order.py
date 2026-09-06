"""L4-09 — OMS 유일 제출 경로: 멱등 선점 → orders INSERT → VALIDATED 전이 →
outbox enqueue, 단일 tx.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C 표
`submit_order(cmd, *, pool, profile, registry, pre_submit_gate, clock)->OrderView`,
§5.2(idempotency.py L4-03 재사용), §5.3(전송 전 실패=FAILED, 유실=UNKNOWN), §9.

FA-5(L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-5)가 `entity_context: EntityContext`
필수 인자를 추가했다 — `resolve_context()`(같은 리프) 산출값을 그대로 넘긴다.
`orders.fund_id`/`portfolio_id`(FA-3, 아직 nullable)를 이 INSERT부터 채운다(NOT NULL
승격은 범위 밖). task-1925(리뷰 REJECT 후속) — 호출자 직접 생성 `entity_context`는 위조
가능해(같은 tenant_id + 타 테넌트 fund_id) `entity_repo` 필수 인자로 INSERT 직전
`verify_entity_context()`를 호출해 실소유권을 재확인한다.

실제 거래소 호출은 이 함수가 하지 않는다 — `outbox_dispatcher.py`(L4-14)가
`order_command_outbox`의 SUBMIT 행을 비동기 소비해 호출한다(§5.3, 이 함수는 그 행을
만드는 것까지만 책임진다).

INSERT-먼저(claim이 그 다음) 순서: `order_idempotency.order_id`는 `orders`를 참조하는
NOT DEFERRABLE FK(073beca589d5)라 claim이 참조할 행이 먼저 있어야 한다 — §2-C 표의
"멱등 선점→orders INSERT"는 개념 순서고, 실제 SQL은 FK 제약이 강제하는 순서를 따른다.
경합에서 진 시도는 방금 넣은 CREATED 행까지 포함해 tx 전체를 롤백하므로("ok" 플래그로
커밋/롤백 명시 제어) 최종적으로 남는 행은 항상 승자 하나뿐이다(DoD "동시 50 submit → 1행").

`client_order_id`는 `scope`의 결정론적 함수(domain/idempotency.py L4-03)라 같은 의도의
동시 요청은 모두 같은 값을 계산한다 — `orders.client_order_id` UNIQUE 제약(210cc26533c7)이
진짜 동시성 관문이고, `order_idempotency.scope_hash` 선점은 digest 비교용이다(둘 다 같은
`scope`의 함수라 client_order_id 충돌 없이 scope_hash만 충돌하는 경로는 없다). 패자는
`UniqueViolationError`를 받는데, Postgres UNIQUE는 충돌 행이 **커밋된 뒤에만** 확정
에러를 내므로 이 시점엔 승자 tx가 이미 커밋 완료돼 있다 — 패자는 자기 tx를 롤백하고
새 tx로 승자 행을 조회해 반환한다.

게이트는 claim이 NEW를 반환했을 때만(§2-C "EXISTING이면 기존 OrderView 반환") INSERT
직후·VALIDATED 전이 직전에 평가한다. DENY면 커밋하지 않고 tx를 롤백한다(0행) —
`outbox_dispatcher._send_submit`과 같은 패턴(L4-14).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderStatus
from src.foundation.entities.application.resolve_context import (
    EntityContextResolutionError,
    EntityRepository,
    verify_entity_context,
)
from src.foundation.entities.contracts.v1 import EntityContext
from src.services.oms.adapters.idempotency_repository import IdempotencyRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.contracts.v1_commands import SubmitOrderCommand
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import IdempotencyDigestMismatchError
from src.services.oms.domain.idempotency import client_order_id as derive_client_order_id
from src.services.oms.domain.idempotency import command_digest, scope_hash
from src.services.oms.domain.state_machine import OrderEvent
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import VenueCapabilityProfile, assert_supported
from src.services.order_service.gate import GateOutcome, OrderContext, PreSubmitGate

Clock = Callable[[], datetime]

DEFAULT_IDEMPOTENCY_TTL = timedelta(minutes=5)

_idempotency = IdempotencyRepository()
_orders = PostgresOrderRepository()
_outbox = OutboxRepository()

_INSERT_ORDER_SQL = """
INSERT INTO orders (
    order_id, user_id, client_order_id, strategy_id, strategy_version, execution_id,
    symbol, venue_symbol, exchange, side, order_type, time_in_force, quantity, price,
    status, filled_quantity, is_liquidation, asset_class, parent_order_id, algo_run_id,
    fund_id, portfolio_id
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
    'CREATED', 0, $15, $16, $17, $18, $19, $20
)
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderSubmitDeniedError(Exception):
    """DENY — 이 호출로는 아무 행도 남지 않는다(tx 롤백, DoD "gate DENY 시 0행")."""

    def __init__(self, reason_codes: tuple[str, ...]) -> None:
        self.reason_codes = reason_codes
        super().__init__(f"submit_order이 pre_submit_gate에 의해 거부됐습니다: {reason_codes}")


def _venue_order(cmd: SubmitOrderCommand, *, order_id: UUID, client_id: str, venue: str) -> Order:
    """outbox SUBMIT payload(§2-C "order" 키) — `order_from_payload`가 order_id/
    client_order_id 일치만 검증하므로 나머지 필드는 어댑터 호출용 실값이면 된다.
    통화는 Phase 1 관례대로 USDT 고정(order_service/repository.py 동일 편차)."""
    price = Money(amount=cmd.price, currency=Currency.USDT) if cmd.price is not None else None
    return Order(
        order_id=order_id,
        client_order_id=client_id,
        strategy_id=cmd.scope.strategy_id,
        strategy_version=cmd.scope.strategy_version,
        execution_id=cmd.scope.execution_id,
        symbol=cmd.symbol,
        exchange=venue,
        side=cmd.side,
        order_type=cmd.order_type,
        quantity=cmd.quantity,
        price=price,
        status=OrderStatus.VALIDATED,
        asset_class=cmd.asset_class,
        is_liquidation=cmd.is_liquidation,
    )


def _event_payload_hash(order_id: UUID, scope_hash_val: str) -> str:
    canonical = json.dumps(
        {"order_id": str(order_id), "scope_hash": scope_hash_val}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def submit_order(
    cmd: SubmitOrderCommand,
    *,
    pool: asyncpg.Pool,
    profile: VenueCapabilityProfile,
    registry: SymbolRegistry,
    pre_submit_gate: PreSubmitGate,
    entity_context: EntityContext,
    entity_repo: EntityRepository,
    clock: Clock = utcnow,
) -> OrderView:
    if entity_context is None:  # 정적 검사 우회(런타임 None 주입) 방어, FA-5
        raise EntityContextResolutionError(
            "entity_context는 필수입니다(FA-5) — None을 명시적으로 넘길 수 없습니다."
        )
    if entity_context.tenant_id != cmd.scope.tenant_id:
        raise EntityContextResolutionError(
            f"entity_context.tenant_id({entity_context.tenant_id})가 "
            f"cmd.scope.tenant_id({cmd.scope.tenant_id})와 다릅니다 — 교차 테넌트 쓰기 거부."
        )
    if pre_submit_gate is None:  # 정적 검사 우회(런타임 None 주입) 방어, I-01
        raise TypeError("pre_submit_gate는 필수입니다(I-01) — None을 명시적으로 넘길 수 없습니다.")
    if entity_repo is None:  # 정적 검사 우회 방어, task-1925
        raise EntityContextResolutionError("entity_repo는 필수입니다(task-1925).")
    await verify_entity_context(entity_repo, entity_context)  # task-1925: 위조 EntityContext 방어
    assert_supported(profile, cmd)
    venue_symbol = registry.to_venue(cmd.symbol, profile.venue)
    client_id = derive_client_order_id(
        cmd.scope, max_len=profile.client_order_id_max_len, charset=profile.client_order_id_charset
    )
    scope_hash_val = scope_hash(cmd.scope)
    digest = command_digest(cmd)
    candidate_order_id = uuid4()

    collided = False
    result: OrderView | None = None

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        ok = False
        try:
            try:
                await conn.execute(
                    _INSERT_ORDER_SQL,
                    candidate_order_id,
                    cmd.scope.tenant_id,
                    client_id,
                    cmd.scope.strategy_id,
                    cmd.scope.strategy_version,
                    cmd.scope.execution_id,
                    cmd.symbol,
                    venue_symbol,
                    profile.venue,
                    cmd.side.value,
                    cmd.order_type.value,
                    cmd.time_in_force,
                    cmd.quantity,
                    cmd.price,
                    cmd.is_liquidation,
                    cmd.asset_class.value,
                    cmd.parent_order_id,
                    cmd.algo_run_id,
                    entity_context.fund_id,
                    entity_context.portfolio_id,
                )
            except asyncpg.UniqueViolationError:
                collided = True
            else:
                claim = await _idempotency.claim(
                    conn,
                    scope_hash=scope_hash_val,
                    digest=digest,
                    order_id=candidate_order_id,
                    ttl=DEFAULT_IDEMPOTENCY_TTL,
                )
                if claim.kind == "EXISTING":
                    # 방어적 분기 — client_order_id가 scope의 결정론 함수라 여기 도달하려면
                    # 이미 위 INSERT에서 UniqueViolationError로 걸러졌어야 한다(정상 도달 불가).
                    result = await _orders.find_by_scope_hash(conn, scope_hash_val)
                    if result is None:
                        raise RuntimeError(
                            f"order_idempotency scope_hash={scope_hash_val}: EXISTING인데 "
                            "orders 조인 결과가 없습니다 — 데이터 정합성 결함."
                        )
                else:
                    decision = await pre_submit_gate(
                        OrderContext(
                            user_id=cmd.scope.tenant_id,
                            execution_id=cmd.scope.execution_id,
                            exchange=profile.venue,
                            mandate_revision_id=None,
                        )
                    )
                    if decision.outcome != GateOutcome.ALLOW:
                        raise OrderSubmitDeniedError(decision.reason_codes)

                    occurred_at = clock()
                    event = OrderTransitionEvent(
                        order_id=candidate_order_id,
                        from_status=OrderStatus.CREATED,
                        to_status=OrderStatus.VALIDATED,
                        event=OrderEvent.VALIDATED.value,
                        reason_code=None,
                        actor_subject_id=cmd.actor_subject_id,
                        trace_id=cmd.trace_id,
                        command_id=cmd.command_id,
                        provider_event_id=None,
                        occurred_at=occurred_at,
                        payload_hash=_event_payload_hash(candidate_order_id, scope_hash_val),
                    )
                    validated = await _orders.transition(
                        conn,
                        order_id=candidate_order_id,
                        expected_status=OrderStatus.CREATED,
                        expected_version=0,
                        new_status=OrderStatus.VALIDATED,
                        patch={},
                        event=event,
                    )
                    venue_order = _venue_order(
                        cmd, order_id=candidate_order_id, client_id=client_id, venue=profile.venue
                    )
                    await _outbox.enqueue(
                        conn,
                        order_id=candidate_order_id,
                        command_type="SUBMIT",
                        payload={
                            "order": venue_order.model_dump(mode="json"),
                            "trace_id": str(cmd.trace_id),
                            "command_id": str(cmd.command_id),
                        },
                        not_before=occurred_at,
                    )
                    result = validated
                    ok = True
        finally:
            if ok:
                await tx.commit()
            else:
                await tx.rollback()

    if collided:
        return await _resolve_after_collision(pool, scope_hash_val=scope_hash_val, digest=digest)
    if result is None:
        raise RuntimeError("submit_order: 도달 불가 상태 — collided도 아니고 결과도 없습니다.")
    return result


async def _resolve_after_collision(
    pool: asyncpg.Pool, *, scope_hash_val: str, digest: str
) -> OrderView:
    """`orders.client_order_id` UNIQUE 충돌 뒤(패자) — 승자 tx는 이미 커밋
    완료라 새 tx로 조회하면 된다. digest도 직접 대조해 승자가 다른 내용의
    명령이었다면(이론상 도달 불가에 가깝지만 fail-closed) 거부한다."""
    async with pool.acquire() as conn:
        stored_digest = await conn.fetchval(
            "SELECT digest FROM order_idempotency WHERE scope_hash = $1", scope_hash_val
        )
        if stored_digest is not None and stored_digest != digest:
            raise IdempotencyDigestMismatchError(scope_hash_val)
        existing = await _orders.find_by_scope_hash(conn, scope_hash_val)
    if existing is None:
        raise RuntimeError(
            f"orders.client_order_id UNIQUE 충돌 뒤 scope_hash={scope_hash_val} 조회 실패 "
            "— 승자 tx가 아직 안 보입니다(격리수준/타이밍 가정 위반)."
        )
    return existing
