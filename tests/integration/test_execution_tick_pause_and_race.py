"""FD-8 — 일시정지(PAUSED)·동시성 경합·미체결 주문 되돌림 경로 전용
(test_execution_tick.py에서 task-10199로 분리, 레드팀 #23/#2026-09-02-22/#39
회귀 테스트 묶음).
"""

from decimal import Decimal

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import AccountBalance, OrderStatus
from src.services.execution_loop.tick import _make_fsm_state_writer, run_execution_tick
from tests.integration._execution_tick_helpers import (
    _create_execution,
    _engines,
    _insert_terminal_order,
    _make_pool,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


@pytest.fixture
async def pool():
    p = await _make_pool()
    yield p
    await p.close()


async def test_paused_execution_is_skipped(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "
            "WHERE id = $1",
            execution_id,
        )
    adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0


async def test_safety_pause_mid_tick_blocks_order_submission(pool):
    """레드팀 #23-a 회귀 테스트 — tick 시작 시점(_load_execution_context)에는
    paused_by가 비어 있었지만, 신호 평가·RiskEngine 검사를 거치는 사이
    Watchdog가 안전정지를 걸면(get_balance() 호출 시점에 주입해 시뮬레이션)
    이번 tick은 주문을 제출하지 않아야 하고, fsm_state도 PENDING류에
    갇히지 않고 원래 상태(IDLE)를 유지해야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    class _PausingAdapter(FakeExchangeAdapter):
        async def get_balance(self, asset: str | None = None):  # noqa: ANN001
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategy_executions SET status = 'PAUSED', "
                    "paused_by = 'SAFETY_LAYER' WHERE id = $1",
                    execution_id,
                )
            return await super().get_balance(asset)

    adapter = _PausingAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"  # PENDING류에 갇히지 않음 — 되돌림이 필요 없는 설계


async def test_paused_execution_still_checks_pending_order_fill(pool):
    """레드팀 #23-c 회귀 테스트 — 주문 제출 직후 일시정지된 실행이라도,
    이미 제출한 주문의 체결 여부는 계속 확인해야 한다. 정지 체크가
    PENDING-fill-check보다 먼저 실행되면 이 확인 자체가 영원히 스킵된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    # 1틱: 주문 제출 직후 아직 미체결(SUBMITTED) → fsm_state=BUY_ORDER_PENDING.
    submit_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.SUBMITTED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )
    await run_execution_tick(pool, submit_adapter, execution_id, **_engines())
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        # Watchdog가 그 사이 이 실행을 안전정지시켰다고 가정.
        await conn.execute(
            "UPDATE strategy_executions SET status = 'PAUSED', paused_by = 'SAFETY_LAYER' "
            "WHERE id = $1",
            execution_id,
        )
    assert fsm_state == "BUY_ORDER_PENDING"

    # 2틱: 정지된 상태에서도 미체결 주문이 실제로는 체결됐는지 확인돼야 한다.
    # LB-12 — get_order()는 자신이 실제로 낸 주문(placed_orders)의 수량을
    # 돌려주므로, 여기서도 1틱과 같은 어댑터를 재사용해야 한다(새 인스턴스는
    # placed_orders가 비어 있어 filled_quantity=0인 비현실적 체결이 된다).
    submit_adapter._get_order_status = OrderStatus.FILLED
    await run_execution_tick(pool, submit_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        order_status = await conn.fetchval(
            "SELECT status FROM orders WHERE execution_id = $1", execution_id
        )
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert order_status == "FILLED"
    assert fsm_state == "HOLDING"  # 체결 반영으로 PENDING을 벗어남 — 정지 중에도 이뤄져야 함


async def test_fsm_state_writer_raises_on_concurrent_state_change(pool):
    """레드팀 #2026-09-02-22 회귀 테스트 — writer가 읽었던 expected_state와
    실제 DB 값이 다르면(다른 tick이 먼저 바꿈) ConcurrencyConflictError를
    던지고 아무것도 쓰지 않아야 한다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_executions SET fsm_state = 'BUY_ORDER_PENDING' WHERE id = $1",
            execution_id,
        )

    writer = await _make_fsm_state_writer(pool)
    with pytest.raises(ConcurrencyConflictError):
        # 이 tick은 IDLE을 읽었다고 주장하지만 실제론 이미 BUY_ORDER_PENDING.
        await writer(execution_id, FSMState.IDLE, FSMState.BUY_ORDER_PENDING)

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"  # 충돌한 쓰기는 반영되지 않음


async def test_concurrent_tick_race_only_submits_one_order(pool):
    """레드팀 #2026-09-02-22 회귀 테스트 — get_balance() 호출 시점에 "다른
    tick"이 이미 이 execution을 BUY_ORDER_PENDING으로 선점했다고 가정하면
    (run_execution_tick이 IDLE을 읽은 *이후*, 자기 자신의 조건부 쓰기 *전*
    끼어든 상황을 시뮬레이션), 이 tick의 조건부 쓰기가 충돌해야 하고
    Executor.execute()까지 가서 실제 주문을 내면 안 된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    class _RacingAdapter(FakeExchangeAdapter):
        async def get_balance(self, asset: str | None = None):  # noqa: ANN001
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE strategy_executions SET fsm_state = 'BUY_ORDER_PENDING' WHERE id = $1",
                    execution_id,
                )
            return await super().get_balance(asset)

    adapter = _RacingAdapter(
        closes=[Decimal("50")] * 65,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )

    await run_execution_tick(pool, adapter, execution_id, **_engines())

    assert adapter.place_order_call_count == 0
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"  # "다른 tick"이 쓴 값 그대로 — 이 tick이 덮어쓰지 않음


@pytest.mark.parametrize("terminal_status", ["CANCELLED", "REJECTED", "EXPIRED"])
async def test_failed_terminal_order_reverts_fsm_state_and_allows_resubmission(
    pool, terminal_status
):
    """레드팀 #2026-09-02-39 회귀 테스트(3경로) — PENDING 상태에서 마지막
    주문이 체결 없이 취소/거부/만료로 종결되면, 이전엔 fsm_state가
    BUY/SELL_ORDER_PENDING에 영원히 갇혀 이후 어떤 신호도 재평가되지
    않았다. 지금은 신호평가로 그 PENDING에 들어오기 전 상태(IDLE)로
    되돌아가야 하고, 그 뒤 tick에서 새 주문이 실제로 다시 나가야 한다
    (되돌림만 확인하고 끝나면 fsm_state 컬럼값만 맞고 거래는 여전히
    멈춰 있는 회귀를 놓친다)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)
    async with pool.acquire() as conn:
        execution = await conn.fetchrow(
            "SELECT strategy_id, strategy_version FROM strategy_executions WHERE id = $1",
            execution_id,
        )
        await conn.execute(
            "UPDATE strategy_executions SET fsm_state = 'BUY_ORDER_PENDING' WHERE id = $1",
            execution_id,
        )
    await _insert_terminal_order(
        pool,
        user_id=user_id,
        execution_id=execution_id,
        strategy_id=execution["strategy_id"],
        strategy_version=execution["strategy_version"],
        status=terminal_status,
    )

    # 1틱: PENDING에서 종결 상태를 관측 — IDLE로 복귀해야 한다.
    revert_adapter = FakeExchangeAdapter(closes=[Decimal("50")] * 65)
    await run_execution_tick(pool, revert_adapter, execution_id, **_engines())

    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "IDLE"
    assert revert_adapter.place_order_call_count == 0  # 이 틱은 되돌림만, 신규 주문 없음

    # 2틱: 복귀 이후에도 신규 주문이 영구히 막히지 않고 다시 나가야 한다.
    resubmit_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.FILLED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )
    await run_execution_tick(pool, resubmit_adapter, execution_id, **_engines())

    assert resubmit_adapter.place_order_call_count == 1
    async with pool.acquire() as conn:
        fsm_state_after_resubmit = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state_after_resubmit == "HOLDING"


async def test_still_open_pending_order_does_not_resubmit_new_order(pool):
    """미체결 보호 불변식 회귀 테스트 — 종결(취소/거부/만료) 처리를
    추가하면서 아직 살아있는 주문(SUBMITTED, 최종 상태 아님)까지 되돌리면
    안 된다. PENDING 상태에서 최신 주문이 아직 미종결이면 tick은 체결
    재확인만 하고 새 주문을 내지 않아야 한다 — #39 수정이 이 기존 보호를
    깨지 않았는지 확인."""
    user_id = await create_test_tenant(pool)
    execution_id = await _create_execution(pool, user_id, entry_threshold=100.0)

    submit_adapter = FakeExchangeAdapter(
        closes=[Decimal("50")] * 65,
        place_order_result_status=OrderStatus.SUBMITTED,
        get_order_status=OrderStatus.SUBMITTED,
        usdt_balance=AccountBalance(
            exchange="bitget", asset="USDT", total=Decimal("10000"), available=Decimal("10000")
        ),
    )
    await run_execution_tick(pool, submit_adapter, execution_id, **_engines())
    async with pool.acquire() as conn:
        fsm_state = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
    assert fsm_state == "BUY_ORDER_PENDING"
    assert submit_adapter.place_order_call_count == 1

    # 아직 미체결(reconfirm도 SUBMITTED) — 다음 tick은 되돌리지도, 새 주문을
    # 내지도 않아야 한다.
    await run_execution_tick(pool, submit_adapter, execution_id, **_engines())

    assert submit_adapter.place_order_call_count == 1  # 추가 주문 없음
    async with pool.acquire() as conn:
        fsm_state_after = await conn.fetchval(
            "SELECT fsm_state FROM strategy_executions WHERE id = $1", execution_id
        )
        order_status = await conn.fetchval(
            "SELECT status FROM orders WHERE execution_id = $1", execution_id
        )
    assert fsm_state_after == "BUY_ORDER_PENDING"  # 여전히 미체결 — PENDING 유지
    assert order_status == "SUBMITTED"
