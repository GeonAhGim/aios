"""L4-28 perf 테스트 공용 빌더 — 실 스키마 시딩 헬퍼(테스트 파일 아님, pytest
수집 대상 제외 — `test_` 접두사 없음).

`submit_*`는 `tests/integration/oms/test_submit_order_tx.py`(L4-09)와 동일한
프로파일/레지스트리/명령 빌더다. `partial_fill_event`는 `tests/integration/
oms/test_inbox_processor.py`(L4-15)의 `_fill_event`와 동일하되, 이 파일들은
의도적으로 **부분** 체결만 만든다 — §7.1 "inbox 처리 지연(received_at→전이
commit)"의 측정 범위를 `InboxProcessor._process_row`의 전이 커밋까지로 좁혀
`_apply_position_ledger`(커밋 이후 별도 커넥션, 다른 모듈 소유)를 측정에서
제외하기 위해서다 — 전량 체결(FILLED)이면 position_ledger 호출이 섞여 이
파일들이 소유하지 않는 코드 경로의 왕복까지 회귀 가드에 들어간다.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.inbox_processor import InboxProcessor
from src.services.oms.application.outbox_dispatcher import OutboxDispatcher
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.contracts.v1_events import FillEvent, ProviderOrderEvent
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from tests.support.oms_outbox_fakes import ScriptedAdapter, allow_gate


def submit_profile() -> VenueCapabilityProfile:
    return VenueCapabilityProfile(
        venue="bitget", asset_classes=[AssetClass.CRYPTO],
        order_types={OrderType.MARKET, OrderType.LIMIT}, time_in_force={"GTC", "IOC"},
        supports_client_order_id=True, client_order_id_max_len=40,
        client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", id_policy="STABLE",
        supports_modify=True, supports_cancel="YES", supports_ws_orders=True,
        supports_batch=False, price_tick={}, qty_lot={}, min_notional={},
        rate_limits={}, submit_timeout=TimeoutBudget(), query_timeout=TimeoutBudget(),
        market_hours=None, max_open_orders_per_symbol=20, verified="DOC_ONLY",
    )


def submit_registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
    )
    return reg


async def create_running_execution(pool: asyncpg.Pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-perf-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies (strategy_id, version, owner_user_id, target_asset, "
            "market, exchange, fsm_definition, author_agent, lifecycle_status) VALUES "
            "($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb, 'test-author', "
            "'APPROVED')",
            strategy_id, user_id,
        )
        row = await conn.fetchrow(
            "INSERT INTO strategy_executions (strategy_id, strategy_version, user_id, "
            "exchange, mode, allocated_capital, currency, status) VALUES "
            "($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING') RETURNING id",
            strategy_id, user_id,
        )
    return int(row["id"])


def submit_command(user_id: uuid.UUID, execution_id: int, seq: int) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=seq,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope, symbol="BTC/USDT",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def insert_open_order(
    pool: asyncpg.Pool, user_id: uuid.UUID, *, quantity: Decimal = Decimal("10")
) -> tuple[uuid.UUID, str, str]:
    """`SUBMITTED` 상태 주문 하나(inbox 매칭 대상) — 수량은 부분체결 여러 건이
    들어와도 FILLED로 넘어가지 않을 만큼 넉넉히 잡는다(호출부가 매번 소량만
    체결시킨다는 전제)."""
    client_order_id = f"cid-{uuid.uuid4().hex}"
    exchange_order_id = f"ex-{uuid.uuid4().hex}"
    async with pool.acquire() as conn:
        order_id = await conn.fetchval(
            "INSERT INTO orders (user_id, client_order_id, exchange_order_id, strategy_id, "
            "strategy_version, symbol, exchange, side, order_type, quantity, status, "
            "filled_quantity, is_liquidation, asset_class) VALUES ($1,$2,$3,'oms-perf-inbox',"
            "'1.0.0','BTC/USDT','bitget','BUY','MARKET',$4,'SUBMITTED',0,false,'CRYPTO') "
            "RETURNING order_id",
            user_id, client_order_id, exchange_order_id, quantity,
        )
    return order_id, client_order_id, exchange_order_id


def partial_fill_event(
    *, exchange_order_id: str, client_order_id: str, quantity: Decimal = Decimal("1")
) -> ProviderOrderEvent:
    fill_id = f"fill-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    fill = FillEvent(
        provider_fill_id=fill_id, venue="bitget", order_id=None,
        exchange_order_id=exchange_order_id, symbol="BTC/USDT", side=OrderSide.BUY,
        quantity=quantity, price=Decimal("100"), fee=Decimal("0"), fee_currency="USDT",
        liquidity="TAKER", venue_ts=now,
    )
    return ProviderOrderEvent(
        provider_event_id=fill_id, venue="bitget", venue_symbol="BTCUSDT",
        exchange_order_id=exchange_order_id, client_order_id=client_order_id,
        venue_status="PARTIALLY_FILLED", filled_quantity=quantity, average_price=Decimal("100"),
        last_fill=fill, venue_ts=now, received_at=now, source="WS",
        raw_hash=hashlib.sha256(fill_id.encode()).hexdigest(),
    )


async def _drain_resolve_adapter(tenant_id: UUID, exchange: str) -> ScriptedAdapter:
    return ScriptedAdapter()


async def drain_outbox_backlog(pool: asyncpg.Pool, *, max_rounds: int = 20) -> None:
    """공유 `TEST_DATABASE_URL`에 이전 실행(다른 테스트 파일·이전 pytest
    실행)이 남긴 PENDING `order_command_outbox` 행을 이 파일들의 측정 시작
    전에 비운다. `claim_batch`는 §5.1 설계상 전역 큐라 주문/테스트로
    필터하지 않는다 — 잔여 행이 있으면 이 디렉터리의 정확 왕복 수 단언
    ("내가 방금 넣은 행만 클레임된다"는 전제)이 깨진다(`tests/integration/
    oms/test_concurrent_dispatchers.py`가 같은 근본 원인을 assert 범위를
    자기 client_order_id로 좁혀 우회한 것과 다른 해법 — 그쪽은 정확한 카운트가
    필요 없지만 이 파일들은 필요하다). 처리 실패(예: 잔여 MODIFY 행에 스크립트
    없음)는 `dispatch_once`가 이미 삼켜 `report.errors`로만 남기므로 드레인을
    막지 않는다 — 그 행은 SENDING으로 남아(리스 만료돼도 `claim_batch` 재클레임
    대상이 아님) 이후 PENDING 큐에서 완전히 빠진다."""
    dispatcher = OutboxDispatcher(
        pool, outbox_repo=OutboxRepository(), order_repo=PostgresOrderRepository(),
        resolve_adapter=_drain_resolve_adapter, pre_send_gate=allow_gate, worker_id="perf-drain",
    )
    for _ in range(max_rounds):
        report = await dispatcher.dispatch_once(limit=200)
        if report.claimed == 0:
            return


async def drain_inbox_backlog(pool: asyncpg.Pool, *, max_rounds: int = 20) -> None:
    """`drain_outbox_backlog`와 동일한 이유 — `claim_unprocessed`도 전역
    큐(§9 L4-15, `073beca589d5` 설계)라 이전 실행이 남긴 `provider_event_
    inbox` NEW 행을 먼저 비운다."""
    processor = InboxProcessor(pool)
    for _ in range(max_rounds):
        processed = await processor.process_once(limit=200)
        if processed == 0:
            return
