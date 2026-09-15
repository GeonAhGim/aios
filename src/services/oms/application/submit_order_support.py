"""`submit_order.py`(L4-09)의 300줄 캡 분할 — outbox payload 빌더/이벤트 해시/
UNIQUE 충돌 뒤 조회, 세 가지 다 단일 tx 밖(또는 tx 진입 전) 순수 보조 로직이다
(outbox_dispatcher.py -> outbox_writes.py, unknown_resolver.py ->
unknown_resolver_writes.py와 동일한 분할 원칙 — 그 모듈들 docstring 참조).

이 파일은 `orders` INSERT를 하지 않는다 — EM-3(`scripts/check_child_order_path.py`)의
"child 컬럼을 쓰는 INSERT INTO orders는 submit_order.py 안에서만 허용" 불변식이
`submit_order.py` 단일 파일을 앵커로 삼으므로, 그 SQL과 커밋/롤백 tx 본문은
분할 대상에서 제외하고 `submit_order.py`에 남겨둔다.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

import asyncpg

from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderStatus
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_commands import SubmitOrderCommand
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import IdempotencyDigestMismatchError


def venue_order(cmd: SubmitOrderCommand, *, order_id: UUID, client_id: str, venue: str) -> Order:
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


def event_payload_hash(order_id: UUID, scope_hash_val: str) -> str:
    canonical = json.dumps(
        {"order_id": str(order_id), "scope_hash": scope_hash_val}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def resolve_after_collision(
    pool: asyncpg.Pool,
    *,
    order_repo: PostgresOrderRepository,
    scope_hash_val: str,
    digest: str,
) -> OrderView:
    """`orders.client_order_id` UNIQUE 충돌 뒤(패자) — 승자 tx는 이미 커밋 완료라
    새 tx로 조회한다. digest도 대조해 승자가 다른 명령이었다면 거부한다(fail-closed).
    `order_repo`는 호출자(`submit_order.py`)의 모듈 싱글톤을 그대로 받는다 —
    failure-injection 테스트가 그 싱글톤 인스턴스를 monkeypatch하므로 여기서
    새로 만들면 안 된다."""
    async with pool.acquire() as conn:
        stored_digest = await conn.fetchval(
            "SELECT digest FROM order_idempotency WHERE scope_hash = $1", scope_hash_val
        )
        if stored_digest is not None and stored_digest != digest:
            raise IdempotencyDigestMismatchError(scope_hash_val)
        existing = await order_repo.find_by_scope_hash(conn, scope_hash_val)
    if existing is None:
        raise RuntimeError(
            f"orders.client_order_id UNIQUE 충돌 뒤 scope_hash={scope_hash_val} 조회 실패 "
            "— 승자 tx가 아직 안 보입니다(격리수준/타이밍 가정 위반)."
        )
    return existing
