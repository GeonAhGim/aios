"""task-2310(L4-18b) 적대적 테스트 — outbox SENDING lease 만료 복구.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F2/F6 ①②, §4.2/§4.4,
§9 L4-18. 시나리오 이름("send와 commit 사이 크래시")은 `outbox_dispatcher.py`의
tx 경계 그대로다: tx2(VALIDATED→SUBMITTED, `SENT`)는 어댑터 호출 *전*에
커밋되고, finalize(outbox 펜스+주문 전이)는 어댑터 호출 *후* 별도 tx(tx3)다.
프로세스가 어댑터 호출 이후·tx3 커밋 이전에 죽으면 주문은 SUBMITTED로 남고
outbox 행은 lease가 만료된 SENDING으로 남는다 — 어댑터를 실제로 불렀는지
알 수 없으므로 재전송은 금지, UNKNOWN만 안전하다(§5.4). tx2조차 커밋 안 된
경우(VALIDATED)는 반대로 어댑터 호출 0회가 확정이라 재진입이 안전하다 —
두 경로가 서로 다른 결과를 내야 "상태기계로 분기한다"는 주장이 증명된다
(test_response_lost_after_send_becomes_unknown 선례와 동일 원칙).
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
import pytest

from src.data.models.trading import Order
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application import restart_recovery
from tests.integration.oms.conftest import create_test_user, insert_order

_PAST = datetime.now(timezone.utc) - timedelta(minutes=5)
_FUTURE = datetime.now(timezone.utc) + timedelta(minutes=5)


@pytest.fixture
async def pool(pool):
    """`order_command_outbox`는 스위트 전체가 공유하는 테이블이고 이 파일의
    복구 함수는 특정 주문으로 좁히지 않고 SENDING+lease 만료 행 전부를 스캔한다
    (test_outbox_repository.py의 동일 문제·동일 해법 — leftover 행이 `processed`
    카운트를 오염시킨다)."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM order_command_outbox")
    return pool


class _LookupAdapter:
    """place_order를 부르면 즉시 실패한다 — 재전송이 발생하면 이 테스트가
    죽는다("중복 전송 0건"을 어댑터 호출 자체를 막아 증명)."""

    def __init__(self, *, lookup_result: Order | None = None) -> None:
        self._lookup_result = lookup_result
        self.lookup_calls = 0

    async def place_order(self, order: Order) -> Order:
        raise AssertionError("복구 경로는 어댑터에 재전송하면 안 된다 — 중복 주문 위험")

    async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
        self.lookup_calls += 1
        return self._lookup_result

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []


async def _insert_stuck_outbox_row(
    pool: asyncpg.Pool,
    order_id: UUID,
    *,
    lease_until: datetime,
    command_type: str = "SUBMIT",
    attempt: int = 0,
) -> UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO order_command_outbox
                (order_id, command_type, payload, state, attempt, lease_until, worker_id)
            VALUES ($1, $2, $3::jsonb, 'SENDING', $4, $5, 'dead-worker')
            RETURNING id
            """,
            order_id,
            command_type,
            json.dumps({"order": {}}),
            attempt,
            lease_until,
        )


async def _outbox_row(pool: asyncpg.Pool, row_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, worker_id FROM order_command_outbox WHERE id = $1", row_id
        )
    assert row is not None
    return row


async def _order_status(pool: asyncpg.Pool, order_id: UUID) -> str:
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
    assert status is not None
    return status


def _resolve_adapter(adapter: _LookupAdapter):
    async def _resolve(tenant_id: UUID, exchange: str) -> _LookupAdapter:
        return adapter

    return _resolve


async def _recover(pool: asyncpg.Pool, adapter: _LookupAdapter) -> int:
    return await restart_recovery.recover_stuck_outbox_commands(
        pool,
        order_repo=PostgresOrderRepository(),
        outbox_repo=OutboxRepository(),
        resolve_adapter=_resolve_adapter(adapter),
        risk_gate_repo=PostgresRiskGateRepository(pool),
        clock=lambda: datetime.now(timezone.utc),
    )


async def test_crash_after_sent_before_finalize_becomes_unknown_not_resent(pool):
    """DoD 핵심 시나리오 — send 직후 commit 전에 죽은 상태(주문 SUBMITTED,
    outbox SENDING lease 만료). 재전송 금지(어댑터 미호출), 주문은 UNKNOWN,
    outbox는 DONE(재클레임 대상에서 제외)으로만 확정돼야 한다."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="SUBMITTED")
    row_id = await _insert_stuck_outbox_row(pool, order_id, lease_until=_PAST)
    adapter = _LookupAdapter(lookup_result=None)

    processed = await _recover(pool, adapter)

    assert processed == 1
    assert await _order_status(pool, order_id) == "UNKNOWN"
    outbox = await _outbox_row(pool, row_id)
    assert outbox["state"] == "DONE"
    # UNKNOWN 해소가 곧장 unknown_resolver로 위임됐다는 증거 — 역조회가 실제로 불렸다.
    assert adapter.lookup_calls >= 1


async def test_crash_before_sent_reenters_as_pending(pool):
    """대조 시나리오 — 어댑터 호출 0회가 확정된 경우(주문 아직 VALIDATED)는
    재진입이 안전하다: outbox가 PENDING으로 돌아가 다음 클레임 대상이 되고,
    주문 상태는 손대지 않는다."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="VALIDATED")
    row_id = await _insert_stuck_outbox_row(pool, order_id, lease_until=_PAST)
    adapter = _LookupAdapter(lookup_result=None)

    processed = await _recover(pool, adapter)

    assert processed == 1
    assert await _order_status(pool, order_id) == "VALIDATED"
    outbox = await _outbox_row(pool, row_id)
    assert outbox["state"] == "PENDING"
    assert adapter.lookup_calls == 0  # UNKNOWN이 아니므로 resolver로 위임되지 않는다

    async with pool.acquire() as conn:
        reclaimed = await OutboxRepository().claim_batch(
            conn, worker_id="dispatcher-1", limit=10, lease_sec=30
        )
    assert row_id in [r.id for r in reclaimed]  # 재진입 = 정상 클레임 경로로 복귀


async def test_live_lease_not_expired_is_left_untouched(pool):
    """negative — 아직 살아있는 워커가 쥔(lease 미만료) SENDING 행은
    복구가 손대면 안 된다(다른 프로세스의 진행 중 작업을 뺏지 않는다)."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="SUBMITTED")
    row_id = await _insert_stuck_outbox_row(pool, order_id, lease_until=_FUTURE)
    adapter = _LookupAdapter(lookup_result=None)

    processed = await _recover(pool, adapter)

    assert processed == 0
    assert await _order_status(pool, order_id) == "SUBMITTED"
    outbox = await _outbox_row(pool, row_id)
    assert outbox["state"] == "SENDING"
    assert outbox["worker_id"] == "dead-worker"  # 복구 워커가 리스를 훔치지 않았다
    assert adapter.lookup_calls == 0


def test_delegates_to_unknown_resolver_without_reimplementing() -> None:
    """구조 검증(grep 등가) — UNKNOWN 해소 로직(NOT_FOUND 카운트·backoff·
    safety control escalation)을 이 모듈이 다시 만들지 않고 `unknown_resolver.
    resolve_unknown`을 그대로 호출한다는 것을 소스에서 직접 확인한다."""
    source = inspect.getsource(restart_recovery)
    assert "resolve_unknown(" in source
    assert "def resolve_unknown" not in source
    assert "NOT_FOUND_STREAK_THRESHOLD" not in source  # resolver 내부 상수 재구현 흔적 없음


def test_order_view_has_no_duplicate_send_side_effect() -> None:
    """`recover_stuck_outbox_commands`가 거래소 어댑터의 제출 계열
    메서드(`place_order`)를 직접 참조하지 않는다는 것을 소스에서 확인한다
    — 재전송 경로 자체가 코드에 없다."""
    source = inspect.getsource(restart_recovery.recover_stuck_outbox_commands)
    source += inspect.getsource(restart_recovery._recover_stuck_submit)
    assert "place_order" not in source
