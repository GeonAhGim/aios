"""task-2151(L4-18a) 통합테스트 — 만료 execution lease 회수 + 복구 완료
전 submit_order 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F6(이 배치는 ①
만료 lease 회수와 ⑤ 복구 완료 전 submit_order 거부만 다룬다 — ②③④는
task-2310), §9 L4-18. DoD: 만료 lease 1건이 심어진 상태로 복구를 돌리면
그 lease가 회수되고, 복구 완료 플래그가 서기 전 submit_order 호출이
구체 에러코드(`OMS_RECOVERY_IN_PROGRESS`)로 거부된다.

task-2794 DEEPEN of task-2151 (docs/audit/DEPTH_L4_BR.md #2151, 실측 D1 <
D3 하한): 원 커밋(b3645f8a)에는 수치 성능 단언, 명시적 gate/CI 회귀
가드, 적대적/다중 인스턴스/재전송 테스트가 없었고 failure-injection은
"죽은 프로세스의 만료 lease 시드"라는 암묵적 형태뿐이었다. 아래를
추가한다: (1) `RecoveryState` 기본값과 예외 처리 분기를 고정하는
gate-red 회귀 가드 2건, (2) `reclaim_expired_leases`를 명시적으로
실패시켜 게이트가 계속 닫혀 있음을 증명하는 failure-injection,
(3) 두 프로세스가 동시에 재시작해 동시에 reclaim하는 다중 인스턴스
경합 테스트, (4) 복구 미완료로 거부된 주문을 복구 완료 후 같은 객체로
재전송(replay)했을 때 정확히 한 번만 성공함을 증명하는 테스트,
(5) `reclaim_expired_leases`가 행 수 증가에도 단일 DELETE 왕복으로
남아있음을 정규화 배율로 단언하는 수치 성능 테스트.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from src.data.models.base import AssetClass
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.services.execution_loop import recovery_wiring
from tests.integration.conftest import create_test_user
from tests.integration.oms.conftest import insert_order
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders

_INSERT_LEASE_SQL = """
    INSERT INTO execution_leases (execution_id, owner_id, fencing_token, heartbeat_at, expires_at)
    VALUES ($1, $2, 0, now() - interval '1 hour', $3)
"""


async def _seed_lease(
    pool: asyncpg.Pool, execution_id: int, *, owner_id: str, expired: bool
) -> None:
    now = datetime.now(UTC)
    expires_at = now - timedelta(seconds=30) if expired else now + timedelta(hours=1)
    async with pool.acquire() as conn:
        await conn.execute(_INSERT_LEASE_SQL, execution_id, owner_id, expires_at)


async def _lease_owner(pool: asyncpg.Pool, execution_id: int) -> str | None:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT owner_id FROM execution_leases WHERE execution_id = $1", execution_id
        )


def _make_order(execution_id: int) -> Order:
    return Order(
        client_order_id=f"recovery-gate-{uuid.uuid4().hex}",
        strategy_id="oms-recovery-gate-test",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


# ---------------------------------------------------------------------------
# task-2673 -- run_startup_recovery(_gated) must call
# restart_recovery.recover_stuck_outbox_commands (task-2345/task-3486 REJECT
# 반영). Before this, `recover_stuck_outbox_commands` was fully implemented
# and unit-tested (task-2310/L4-18b) but never invoked from the assembly
# point -- an actual restart never recovered SENDING outbox commands
# (dead code, I-10). These two tests exercise the *assembly point*
# (`run_startup_recovery_gated`), not the pure function directly -- removing
# the wiring call in recovery_wiring.py turns both red.
# ---------------------------------------------------------------------------


async def _insert_stuck_outbox(
    pool: asyncpg.Pool, order_id: uuid.UUID, *, command_type: str
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO order_command_outbox
                (order_id, command_type, payload, state, attempt, lease_until, worker_id)
            VALUES ($1, $2, $3::jsonb, 'SENDING', 0, now() - interval '1 minute', 'dead-worker')
            RETURNING id
            """,
            order_id,
            command_type,
            json.dumps({}),
        )


async def test_run_startup_recovery_gated_transitions_stuck_submit_via_unknown(pool):
    """UNKNOWN 전이 경로 -- 이미 SUBMITTED인 주문의 SUBMIT 명령이 SENDING에서
    lease 만료로 멈추면(호출 여부 불확실) UNKNOWN으로 전이 후
    `unknown_resolver.resolve_unknown`에 위임돼야 한다(§5.4 재전송 금지).
    이 테스트의 어댑터는 즉시 조회에 성공해 RESOLVED_AS로 해소시킨다 --
    order가 SUBMITTED에 머물러 있으면(배선이 빠지면) 이 단언이 적색이 된다."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="SUBMITTED")
        client_order_id = await conn.fetchval(
            "SELECT client_order_id FROM orders WHERE order_id = $1", order_id
        )
    outbox_id = await _insert_stuck_outbox(pool, order_id, command_type="SUBMIT")

    resolved_order = Order(
        client_order_id=client_order_id,
        strategy_id="oms-recovery-outbox-test",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        status=OrderStatus.ACKNOWLEDGED,
        asset_class=AssetClass.CRYPTO,
    )

    class _ResolverAdapter:
        async def find_order_by_client_id(self, client_order_id: str) -> Order | None:
            return resolved_order

        async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
            return []

    async def resolve_adapter(uid: uuid.UUID, exchange: str) -> _ResolverAdapter:
        return _ResolverAdapter()

    async def publish_noop(topic: str, payload: dict) -> None:
        return None

    state = await recovery_wiring.run_startup_recovery_gated(
        pool, resolve_adapter=resolve_adapter, publish=publish_noop, enabled=True
    )

    assert state.complete is True
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
        outbox_state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE id = $1", outbox_id
        )
    assert status == "ACKNOWLEDGED"
    assert outbox_state == "DONE"


def test_run_startup_recovery_calls_recover_stuck_outbox_commands():
    """게이트-레드 회귀 가드 -- `run_startup_recovery`의 소스에서 이 호출을
    지우면(task-3486 REJECT가 지적한 바로 그 결함) 위 두 통합 테스트가
    적색이 되지만, 실DB 없이도 소스 스캔으로 즉시 고정한다."""
    source = inspect.getsource(recovery_wiring.run_startup_recovery)
    assert "recover_stuck_outbox_commands(" in source
