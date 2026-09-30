"""FD-8/R-48 — 데이터불신(DataDistrust) 상태가 신규 진입 신호를 차단/
허용하는 경로 전용(test_execution_tick.py에서 task-10199로 분리).
"""

from decimal import Decimal

import pytest

from src.data.models.trading import AccountBalance, OrderStatus
from src.services.execution_loop.tick import run_execution_tick
from tests.integration._execution_tick_helpers import _create_execution, _engines, _make_pool
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


@pytest.fixture
async def pool():
    p = await _make_pool()
    yield p
    await p.close()


async def test_distrusted_blocks_new_entry_signal(pool, monkeypatch):
    """R-48 — DISTRUSTED면 신규 진입 주문 자체를 건너뛴다(FSM은 IDLE
    그대로, 다음 틱 재평가). data_distrust_state 마이그레이션이 아직
    origin/main에 없어(task-103 참조) 실제 DB 영속 대신
    check_and_persist_distrust를 monkeypatch로 대체한다."""
    from src.core.safety.data_distrust import DataDistrustLevel

    async def _fake_check_and_persist(*args, **kwargs):
        return DataDistrustLevel.DISTRUSTED

    monkeypatch.setattr(
        "src.services.execution_loop.tick.check_and_persist_distrust", _fake_check_and_persist
    )

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"


async def test_suspicious_blocks_new_entry_signal(pool, monkeypatch):
    """R-48 — SUSPICIOUS도 신규 진입은 막는다(DISTRUSTED와 동일하게
    IDLE -> BUY_ORDER_PENDING 경로가 차단된다)."""
    from src.core.safety.data_distrust import DataDistrustLevel

    async def _fake_check_and_persist(*args, **kwargs):
        return DataDistrustLevel.SUSPICIOUS

    monkeypatch.setattr(
        "src.services.execution_loop.tick.check_and_persist_distrust", _fake_check_and_persist
    )

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0


async def test_degraded_single_source_does_not_block_entry(pool, monkeypatch):
    """R-48 — DEGRADED_SINGLE_SOURCE(참조 소스 없음)는 관측만, 신규 진입을
    막지 않는다 — quorum 미달로 영구 고착됐던 예전 SUSPICIOUS 동작과의
    핵심 차이."""
    from src.core.safety.data_distrust import DataDistrustLevel

    async def _fake_check_and_persist(*args, **kwargs):
        return DataDistrustLevel.DEGRADED_SINGLE_SOURCE

    monkeypatch.setattr(
        "src.services.execution_loop.tick.check_and_persist_distrust", _fake_check_and_persist
    )

    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 1
